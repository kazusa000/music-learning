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
    std::atomic<bool> suspended{false},suspendAck{false};
    std::atomic<uint64_t> written{0},read{0},underruns{0};
    std::atomic<uint64_t> seekRequested{0},seekApplied{0};
    std::atomic<uint64_t> updateRequested{0},updateApplied{0};
    mutable std::mutex activity;bool active=false;
    std::atomic<double> played{0};
    std::atomic<size_t> cache{0};
    std::thread producer;
    int frames;double rate,outputRate,origin,loopStart,loopEnd,duration;bool loop;
    size_t audioBudget;
    juce::StringArray retained;
public:
    GraphSession(const var& manifest,double deviceRate,int quantum,double start,bool looping,double begin,double end,size_t available=256ull*1024*1024)
        :initial(manifest),frames(quantum),rate((double)manifest["sample_rate"]),outputRate(deviceRate),origin(start),loopStart(begin),loopEnd(end),
         duration((double)manifest["duration_samples"]+(double)manifest.getProperty("tail_samples",0)),loop(looping),audioBudget(available){
        if(quantum<=0||quantum>4096||start<0||start>duration||(loop&&(begin<0||end<=begin||end>(double)manifest["duration_samples"])))throw std::runtime_error("Invalid graph transport");
        played.store(start);for(auto& block:blocks)block.audio.setSize(2,frames);
        size_t forecast=capacity*(size_t)frames*2*sizeof(float)+512*2*sizeof(float)
            +((size_t)std::ceil(frames*rate/outputRate)+64)*2*sizeof(float)+ListeningProcessor::bufferBytesBoundFor(512);
        auto* values=new juce::DynamicObject();auto* plugins=new juce::DynamicObject();
        if(auto* tracks=manifest["tracks"].getArray())for(const auto& track:*tracks){
            const auto count=(uint32_t)(int)track.getProperty("plugin_count",0);
            forecast+=(size_t)(4+count)*512*2*sizeof(float)+(count?NodeSource::bytesBound(512,count):0)
                +(size_t)(int)track.getProperty("audio_clip_count",0)*1024*1024;
            if(track["id"]==manifest["routing_graph"]["master"])continue;
            auto* value=new juce::DynamicObject();for(const char* key:{"gain_db","pan","mute","solo"})value->setProperty(key,track.getProperty(key,0));values->setProperty(track["id"].toString(),var(value));
            if((int)track.getProperty("plugin_count",0)>0){auto* state=new juce::DynamicObject();state->setProperty("status","loading");state->setProperty("load_stage",0);plugins->setProperty(track["id"].toString(),var(state));}
        }monitors=var(values);diagnostics=var(plugins);
        if(forecast>audioBudget)throw std::runtime_error("Graph preparation exceeds available audio-buffer budget");cache.store(forecast);
        producer=std::thread([this]{run();});
    }
    ~GraphSession(){stop();}
    void stop(){stopping.store(true);if(producer.joinable())producer.join();cache.store(0);}
    bool preparing()const{return !prepared.load()&&!failed.load();}
    void pauseProducer(){
        if(preparing()){stop();prepared.store(false);failed.store(true);std::lock_guard lock(control);error="Graph preparation cancelled";return;}
        suspended.store(true);const auto until=juce::Time::getMillisecondCounterHiRes()+3000;
        while(!suspendAck.load()&&!failed.load()){
            if(juce::Time::getMillisecondCounterHiRes()>until)throw std::runtime_error("Graph transition barrier timed out");juce::Thread::sleep(1);
        }
    }
    void resumeProducer(){suspended.store(false);}
    void run(){
        struct Loader{
            std::shared_ptr<GraphKernel::PreparedUpdate> plan;
            std::thread thread;std::atomic<bool> done{false};
            ~Loader(){plan->cancelled.store(true);if(thread.joinable())thread.join();}
        };
        std::vector<std::unique_ptr<Loader>> loaders;
        try{
            const auto outputBytes=capacity*(size_t)frames*2*sizeof(float)+512*2*sizeof(float)
                +((size_t)std::ceil(frames*rate/outputRate)+64)*2*sizeof(float);
            if(audioBudget<=outputBytes)throw std::runtime_error("No available native graph audio budget");
            GraphKernel kernel(initial,(int64_t)origin,loop,(int64_t)loopStart,(int64_t)loopEnd,false,&stopping,audioBudget-outputBytes);
            Stream stream(kernel);juce::ResamplingAudioSource resampler(&stream,false,2);
            resampler.setResamplingRatio(rate/outputRate);resampler.prepareToPlay(frames,outputRate);
            uint64_t counter=0,epoch=1,appliedId=0,planId=0;auto current=initial;bool ready=false;
            std::shared_ptr<GraphKernel::PreparedUpdate> plan;
            auto resetPaused=[&]{
                std::lock_guard guard(activity);
                if(!active){
                    origin=played.load();kernel.seek((int64_t)origin,loop,(int64_t)loopStart,(int64_t)loopEnd);
                    stream.available=stream.offset=0;resampler.flushBuffers();counter=0;read.store(0);written.store(0);prepared.store(false);
                }
            };
            auto apply=[&](const var& update,uint64_t id,const std::shared_ptr<GraphKernel::PreparedUpdate>& replacement){
                try{
                    if(replacement)kernel.install(replacement);
                    kernel.update(update);current=update;++epoch;resetPaused();appliedId=id;
                    {std::lock_guard lock(control);updateError.clear();}
                    if(replacement)replacement->applied.store(true,std::memory_order_release);
                }catch(const std::exception& e){
                    if(replacement)replacement->applied.store(true,std::memory_order_release);
                    std::lock_guard lock(control);updateError=e.what();updateApplied.store(id);
                }
            };
            while(!stopping.load()){
                if(suspended.load()){
                    if(plan){plan->cancelled.store(true);plan.reset();std::lock_guard lock(control);updateError="Resource preparation superseded by a graph transition";}
                    for(auto& loader:loaders)loader->plan->cancelled.store(true);loaders.clear();
                    cache.store(kernel.bytes()+outputBytes);suspendAck.store(true);
                    while(suspended.load()&&!stopping.load())juce::Thread::sleep(1);
                    suspendAck.store(false);continue;
                }
                for(auto it=loaders.begin();it!=loaders.end();)if((*it)->done.load())it=loaders.erase(it);else ++it;
                size_t staging=0;for(const auto& loader:loaders)staging+=loader->plan->audioBytes.load();
                cache.store(kernel.bytes()+outputBytes+staging);
                if(cache.load()>audioBudget)throw std::runtime_error("Graph playback exceeds audio-buffer budget");
                var update,monitor,seek;uint64_t seekId=0,updateId=0;
                {std::lock_guard lock(control);update=pendingUpdate;pendingUpdate=var();monitor=monitors.clone();seek=pendingSeek;pendingSeek=var();seekId=seekRequested.load();updateId=updateRequested.load();}
                if(seek.isObject()){
                    origin=(double)seek["position_sample"];loop=(bool)seek["loop"];loopStart=(double)seek["loop_start_sample"];loopEnd=(double)seek["loop_end_sample"];
                    kernel.seek((int64_t)origin,loop,(int64_t)loopStart,(int64_t)loopEnd);
                    stream.available=stream.offset=0;resampler.flushBuffers();counter=0;read.store(0);written.store(0);prepared.store(false);played.store(origin);seekApplied.store(seekId);
                }
                if(update.isObject()){
                    if(plan){plan->cancelled.store(true);plan.reset();}
                    if(kernel.requiresReplacement(current,update)){
                        plan=std::make_shared<GraphKernel::PreparedUpdate>();plan->previous=current;plan->manifest=update;plan->force=kernel.forceReplacements();planId=updateId;
                        auto loader=std::make_unique<Loader>();loader->plan=plan;auto* owner=loader.get();
                        const auto available=audioBudget-cache.load();
                        loader->thread=std::thread([owner,available]{GraphKernel::prepare(owner->plan,(size_t)available);owner->done.store(true);});
                        loaders.push_back(std::move(loader));
                    }else apply(update,updateId,{});
                }
                if(plan){
                    if(plan->failed.load()){
                        {std::lock_guard lock(control);updateError=plan->failure();}updateApplied.store(planId);plan.reset();
                    }else if(plan->ready.load(std::memory_order_acquire)){
                        apply(plan->manifest,planId,plan);plan.reset();
                    }
                }
                if(kernel.needsReplan()){
                    const auto nextSource=origin+(double)(counter*(uint64_t)frames)*rate/outputRate;
                    if(kernel.replan((int64_t)nextSource)){stream.available=stream.offset=0;resampler.flushBuffers();}
                }
                if(monitor.isObject())kernel.monitor(monitor);
                auto publish=[&](bool completed){
                    auto states=kernel.pluginValues(ready);
                    if(plan){
                        const auto loadingState=plan->states();
                        if(auto* loading=loadingState.getDynamicObject())for(const auto& item:loading->getProperties())states.getDynamicObject()->setProperty(item.name,item.value);
                        ready=false;
                    }
                    std::lock_guard lock(control);diagnostics=states;meters=kernel.meterValues();retained=kernel.programs();
                    for(const auto& loader:loaders)retained.addArray(loader->plan->programs());
                    if(completed&&ready)publishedRevision=current["revision_id"].toString();
                };
                publish(false);
                if(counter-read.load(std::memory_order_acquire)>=capacity){juce::Thread::sleep(1);continue;}
                auto& block=blocks[counter%capacity];juce::AudioSourceChannelInfo info(&block.audio,0,frames);resampler.getNextAudioBlock(info);
                const double source=origin+(double)((counter+1)*(uint64_t)frames)*rate/outputRate;
                block.endSample=loop&&source>=loopEnd?loopStart+std::fmod(source-loopStart,loopEnd-loopStart):juce::jmin(source,duration);block.epoch=epoch;
                publish(true);++counter;written.store(counter,std::memory_order_release);prepared.store(true);
                if(appliedId)updateApplied.store(appliedId);
            }
            if(plan)plan->cancelled.store(true);
            resampler.releaseResources();
        }catch(const std::exception& e){std::lock_guard lock(control);error=e.what();failed.store(true);}
        for(auto& loader:loaders)loader->plan->cancelled.store(true);
        loaders.clear();
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
    bool update(const var& manifest){
        for(const char* key:{"project_id","view","sample_rate","duration_samples","tail_samples"})if(initial[key]!=manifest[key])return false;
        if(initial["routing_graph"]!=manifest["routing_graph"]
           &&juce::JSON::toString(initial["routing_graph"],true)!=juce::JSON::toString(manifest["routing_graph"],true))return false;
        const auto* before=initial["tracks"].getArray();const auto* after=manifest["tracks"].getArray();
        if(!before||!after||before->size()!=after->size())return false;
        for(int i=0;i<before->size();++i)if((*before)[i]["id"]!=(*after)[i]["id"])return false;
        std::lock_guard lock(control);pendingUpdate=manifest;updateRequested.fetch_add(1);return true;
    }
    void setPlaying(bool playing){std::lock_guard lock(activity);active=playing;}
    void seek(double start,bool looping,double begin,double end){
        if(start<0||start>duration||(looping&&(begin<0||end<=begin||end>(double)initial["duration_samples"])))throw std::runtime_error("Invalid graph transport range");
        uint64_t id;const bool wait=prepared.load();
        {std::lock_guard lock(control);auto* value=new juce::DynamicObject();value->setProperty("position_sample",start);value->setProperty("loop",looping);
            value->setProperty("loop_start_sample",begin);value->setProperty("loop_end_sample",end);pendingSeek=var(value);id=seekRequested.fetch_add(1)+1;}
        played.store(start);if(!wait)return;
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
        std::lock_guard lock(control);ready=prepared.load()&&!failed.load()&&updateError.isEmpty()&&updateApplied.load()>=updateRequested.load();
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
