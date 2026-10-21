#pragma once
#include "GraphKernel.h"
#include <mutex>
#include <thread>

namespace mlengine {
// Owns the source-rate kernel on a background thread. The device callback only
// consumes two preallocated blocks and updates lock-free counters.
class GraphSession final {
    using var=juce::var;using Buffer=juce::AudioBuffer<float>;
    struct Stream final:juce::AudioSource{
        GraphKernel& kernel;Buffer pending;int offset=0,available=0;
        explicit Stream(GraphKernel& value):kernel(value),pending(2,value.quantum()){}
        void prepareToPlay(int,double)override{}
        void releaseResources()override{}
        void getNextAudioBlock(const juce::AudioSourceChannelInfo& info)override{
            int done=0;
            while(done<info.numSamples){
                if(!available){kernel.process(pending,kernel.quantum());offset=kernel.consumeDiscard(kernel.quantum());available=kernel.quantum()-offset;if(!available)continue;}
                const int n=juce::jmin(available,info.numSamples-done);
                for(int ch=0;ch<2;++ch)info.buffer->copyFrom(ch,info.startSample+done,pending,ch,offset,n);
                done+=n;offset+=n;available-=n;
            }
        }
    };
    static constexpr size_t capacity=2;
    struct Block{Buffer audio;double endSample=0;uint64_t epoch=0;};
    std::array<Block,capacity> blocks;
    var initial,pendingUpdate,pendingSeek,diagnostics,monitors,meters;
    juce::String error,updateError,publishedRevision;
    mutable std::mutex control;
    std::atomic<bool> stopping{false},prepared{false},failed{false};
    std::atomic<uint64_t> written{0},read{0},underruns{0};
    std::atomic<uint64_t> seekRequested{0},seekApplied{0};
    std::atomic<uint64_t> updateRequested{0},updateApplied{0};
    bool pendingPaused=false;
    std::atomic<double> played{0};
    std::atomic<size_t> cache{0};
    std::thread producer;
    int frames;double rate,outputRate,origin,loopStart,loopEnd,duration;bool loop;
    juce::StringArray retained;
public:
    GraphSession(const var& manifest,double deviceRate,int quantum,double start,bool looping,double begin,double end)
        :initial(manifest),frames(quantum),rate((double)manifest["sample_rate"]),outputRate(deviceRate),origin(start),loopStart(begin),loopEnd(end),
         duration((double)manifest["duration_samples"]+(double)manifest.getProperty("tail_samples",0)),loop(looping){
        if(quantum<=0||quantum>4096||start<0||start>duration||(loop&&(begin<0||end<=begin||end>(double)manifest["duration_samples"])))throw std::runtime_error("Invalid graph transport");
        played.store(start);for(auto& block:blocks)block.audio.setSize(2,frames);
        auto* values=new juce::DynamicObject();auto* plugins=new juce::DynamicObject();
        if(auto* tracks=manifest["tracks"].getArray())for(const auto& track:*tracks){
            if(track["id"]==manifest["routing_graph"]["master"])continue;
            auto* value=new juce::DynamicObject();for(const char* key:{"gain_db","pan","mute","solo"})value->setProperty(key,track.getProperty(key,0));values->setProperty(track["id"].toString(),var(value));
            if((int)track.getProperty("plugin_count",0)>0){auto* state=new juce::DynamicObject();state->setProperty("status","loading");state->setProperty("load_stage",0);plugins->setProperty(track["id"].toString(),var(state));}
        }monitors=var(values);diagnostics=var(plugins);
        producer=std::thread([this]{run();});
    }
    ~GraphSession(){stopping.store(true);if(producer.joinable())producer.join();}
    void run(){
        try{
            GraphKernel kernel(initial,(int64_t)origin,loop,(int64_t)loopStart,(int64_t)loopEnd,false,&stopping);
            Stream stream(kernel);juce::ResamplingAudioSource resampler(&stream,false,2);
            resampler.setResamplingRatio(rate/outputRate);resampler.prepareToPlay(frames,outputRate);
            cache.store(kernel.bytes()+capacity*(size_t)frames*2*sizeof(float)+512*2*sizeof(float));
            if(cache.load()>256ull*1024*1024)throw std::runtime_error("Graph playback exceeds audio-buffer budget");
            uint64_t counter=0,epoch=1;auto current=initial;bool ready=false;
            while(!stopping.load()){
                var update,monitor,seek;uint64_t seekId=0,updateId=0;bool paused=false;
                {std::lock_guard lock(control);update=pendingUpdate;pendingUpdate=var();monitor=monitors.clone();seek=pendingSeek;pendingSeek=var();seekId=seekRequested.load();paused=pendingPaused;updateId=updateRequested.load();}
                if(seek.isObject()){
                    // Main stopped consumption before requesting this barrier.
                    // Worker instances/states remain loaded across locations.
                    origin=(double)seek["position_sample"];loop=(bool)seek["loop"];loopStart=(double)seek["loop_start_sample"];loopEnd=(double)seek["loop_end_sample"];
                    kernel.seek((int64_t)origin,loop,(int64_t)loopStart,(int64_t)loopEnd);
                    stream.available=stream.offset=0;resampler.flushBuffers();counter=0;read.store(0);written.store(0);prepared.store(false);played.store(origin);
                    seekApplied.store(seekId);
                }
                if(update.isObject()){
                    try{
                        kernel.update(update);current=update;++epoch;
                        if(paused){
                            // The pipe command remains at this barrier until a
                            // replacement block exists; no consumer can resume
                            // while the paused queue is being replaced.
                            origin=played.load();kernel.seek((int64_t)origin,loop,(int64_t)loopStart,(int64_t)loopEnd);
                            stream.available=stream.offset=0;resampler.flushBuffers();counter=0;read.store(0);written.store(0);prepared.store(false);
                        }
                        std::lock_guard lock(control);updateError.clear();
                    }catch(const std::exception& e){std::lock_guard lock(control);updateError=e.what();updateApplied.store(updateId);}
                }
                // The master is not user-selectable; discard its synthetic
                // initial monitor entry before passing the public track map.
                if(monitor.isObject()){
                    auto map=monitor.clone();map.getDynamicObject()->removeProperty(initial["routing_graph"]["master"].toString());kernel.monitor(map);
                }
                {std::lock_guard lock(control);diagnostics=kernel.pluginValues(ready);meters=kernel.meterValues();retained=kernel.programs();}
                if(counter-read.load(std::memory_order_acquire)>=capacity){juce::Thread::sleep(1);continue;}
                auto& block=blocks[counter%capacity];juce::AudioSourceChannelInfo info(&block.audio,0,frames);resampler.getNextAudioBlock(info);
                const double source=origin+(double)((counter+1)*(uint64_t)frames)*rate/outputRate;
                block.endSample=loop&&source>=loopEnd?loopStart+std::fmod(source-loopStart,loopEnd-loopStart):juce::jmin(source,duration);block.epoch=epoch;
                {std::lock_guard lock(control);diagnostics=kernel.pluginValues(ready);meters=kernel.meterValues();if(ready)publishedRevision=current["revision_id"].toString();}
                ++counter;written.store(counter,std::memory_order_release);prepared.store(true);
                if(updateId)updateApplied.store(updateId);
            }
            resampler.releaseResources();
        }catch(const std::exception& e){std::lock_guard lock(control);error=e.what();failed.store(true);}
    }
    bool consume(float*const* output,int channels,int count,float& peak){
        if(failed.load()||!prepared.load())return false;
        const auto current=read.load();if(count!=frames||current==written.load(std::memory_order_acquire)){underruns.fetch_add(1);return false;}
        const auto& block=blocks[current%capacity];
        for(int ch=0;ch<juce::jmin(channels,2);++ch)if(output[ch])for(int i=0;i<count;++i){
            const float value=block.audio.getSample(ch,i);
            output[ch][i]+=value;peak=juce::jmax(peak,std::abs(value));
        }
        played.store(block.endSample);read.store(current+1,std::memory_order_release);return true;
    }
    bool update(const var& manifest,bool paused){
        for(const char* key:{"project_id","view","sample_rate","duration_samples","tail_samples"})if(initial[key]!=manifest[key])return false;
        if(initial["routing_graph"]!=manifest["routing_graph"]
           &&juce::JSON::toString(initial["routing_graph"],true)!=juce::JSON::toString(manifest["routing_graph"],true))return false;
        const auto* before=initial["tracks"].getArray();const auto* after=manifest["tracks"].getArray();
        if(!before||!after||before->size()!=after->size())return false;
        for(int i=0;i<before->size();++i)if((*before)[i]["id"]!=(*after)[i]["id"]||(*before)[i]["runtime_signature"]!=(*after)[i]["runtime_signature"])return false;
        uint64_t id;const bool wait=paused&&prepared.load();
        {std::lock_guard lock(control);pendingUpdate=manifest;pendingPaused=paused;id=updateRequested.fetch_add(1)+1;}
        if(wait){
            const auto until=juce::Time::getMillisecondCounterHiRes()+8000;
            while(updateApplied.load()!=id){
                if(failed.load())throw std::runtime_error(failure().toStdString());
                if(juce::Time::getMillisecondCounterHiRes()>until)throw std::runtime_error("Paused graph update acknowledgement timed out");
                juce::Thread::sleep(1);
            }
            const auto error=failure();if(error.isNotEmpty())throw std::runtime_error(error.toStdString());
        }return true;
    }
    void seek(double start,bool looping,double begin,double end){
        if(start<0||start>duration||(looping&&(begin<0||end<=begin||end>(double)initial["duration_samples"])))throw std::runtime_error("Invalid graph transport range");
        uint64_t id;
        {std::lock_guard lock(control);auto* value=new juce::DynamicObject();value->setProperty("position_sample",start);value->setProperty("loop",looping);
            value->setProperty("loop_start_sample",begin);value->setProperty("loop_end_sample",end);pendingSeek=var(value);id=seekRequested.fetch_add(1)+1;}
        const auto until=juce::Time::getMillisecondCounterHiRes()+3000;
        while(seekApplied.load()!=id){
            if(failed.load())throw std::runtime_error(failure().toStdString());
            if(juce::Time::getMillisecondCounterHiRes()>until)throw std::runtime_error("Graph seek acknowledgement timed out");
            juce::Thread::sleep(1);
        }
    }
    var monitorValues()const{std::lock_guard lock(control);return monitors.clone();}
    var meterValues()const{std::lock_guard lock(control);return meters;}
    var pluginValues(bool& ready)const{
        std::lock_guard lock(control);ready=prepared.load()&&!failed.load()&&updateError.isEmpty();
        if(auto* object=diagnostics.getDynamicObject())for(const auto& p:object->getProperties())if(p.value["status"].toString()!="ready")ready=false;
        return diagnostics.isObject()?diagnostics:var(new juce::DynamicObject());
    }
    void monitor(const var& values){
        if(!values.isObject())throw std::runtime_error("Invalid monitor map");std::lock_guard lock(control);
        for(const auto& field:values.getDynamicObject()->getProperties())if(!monitors.hasProperty(field.name))throw std::runtime_error("Unknown monitor track");
        for(const auto& field:values.getDynamicObject()->getProperties()){
            const auto target=monitors.getProperty(field.name,{});if(!field.value.isObject())throw std::runtime_error("Invalid monitor track value");
            for(const auto& value:field.value.getDynamicObject()->getProperties())target.getDynamicObject()->setProperty(value.name,value.value);
        }
    }
    juce::String failure()const{std::lock_guard lock(control);return error.isNotEmpty()?error:updateError;}
    juce::String appliedRevision()const{std::lock_guard lock(control);return publishedRevision;}
    juce::StringArray programs()const{std::lock_guard lock(control);return retained;}
    bool isFailed()const{return failed.load();}
    bool ended()const{return !loop&&played.load()>=duration;}
    double position()const{return played.load();}
    uint64_t consumed()const{return read.load();}
    uint64_t misses()const{return underruns.load();}
    size_t bytes()const{return cache.load();}
    bool available()const{return prepared.load()&&written.load(std::memory_order_acquire)>read.load();}
};
}
