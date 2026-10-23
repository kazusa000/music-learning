#pragma once
#include "../include/MainOutputBus.h"

namespace ml {
struct NodeTimeline final : Timeline {
    bool looping=false;
    int64 loopStart=0,loopEnd=0;
    using Timeline::Timeline;
    double ppqAt(int64 sample) const {
        auto entry=tempos.front();for(const auto& tempo:tempos)if(tempo.sample<=sample)entry=tempo;
        return entry.ppq+(sample-entry.sample)/rate*entry.bpm/60.0;
    }
    Optional<PositionInfo> getPosition() const override {
        auto value=Timeline::getPosition();value->setIsLooping(looping);
        if(looping)value->setLoopPoints(LoopPoints{ppqAt(loopStart),ppqAt(loopEnd)});
        return value;
    }
};
// Worker-only DSP. It never owns a device callback or talks to Python for PCM.
class TrackRenderer final : public AudioSource {
    var project, track;
    std::unique_ptr<Timeline> timeline;
    std::vector<std::unique_ptr<AudioPluginInstance>> plugins;
    std::vector<std::map<String,float>> baseline;
    std::vector<MidiEvent> events;
    std::vector<size_t> eventIndices;
    std::vector<std::set<std::pair<int,int>>> activeNotes;
    struct Lane {AudioProcessorParameter* target;float base;bool linear;std::vector<std::pair<int64,float>> points;};
    std::vector<std::vector<Lane>> lanes;
    bool automated=false;
    AudioFormatManager formats;
    std::vector<AudioClip> clips;
    int channels=4, quantum=512, latency=0;
    int64 cursor=0, loopStart=0, loopEnd=0;
    bool looping=false, reset=true;
    bool graphMode=false;
    int64 graphOrigin=0;
    std::vector<int64> graphExpected;
    double rate=44100;
    AudioBuffer<float> work, input;

    void rebuildEvents() {
        events.clear(); eventIndices.assign(plugins.size(),0);
        for(auto note:array(track,"notes")) {
            const auto at=integer(note,"sample"), end=at+integer(note,"duration_samples");
            const auto channel=(int)integer(note,"channel",1), pitch=(int)integer(note,"note");
            events.push_back({at,2,MidiMessage::noteOn(channel,pitch,(float)number(note,"velocity",.8))});
            events.push_back({end,0,MidiMessage::noteOff(channel,pitch)});
        }
        for(auto cc:array(track,"controllers"))events.push_back({integer(cc,"sample"),1,MidiMessage::controllerEvent(
            (int)integer(cc,"channel",1),(int)integer(cc,"controller"),(int)integer(cc,"value"))});
        std::stable_sort(events.begin(),events.end(),[](const auto& a,const auto& b){return a.sample==b.sample?a.priority<b.priority:a.sample<b.sample;});
    }
    void rebuildAutomation() {
        lanes.clear();lanes.resize(plugins.size());automated=false;
        const auto specs=array(track,"plugins");
        for(auto item:array(track,"automation")) {
            int index=-1;for(int i=0;i<specs.size();++i)if(text(specs[i],"id")==text(item,"plugin_id"))index=i;
            require(index>=0,"Unknown worker automation plugin");
            const auto id=text(item,"parameter_id");auto* target=parameter(*plugins[(size_t)index],id);
            const auto values=field(specs[index],"parameters");
            Lane lane{target,(float)(double)values.getProperty(id,baseline[(size_t)index].at(id)),text(item,"interpolation")=="linear",{}};
            for(auto point:array(item,"points"))lane.points.emplace_back(integer(point,"sample"),(float)number(point,"value",0));
            require(!lane.points.empty(),"Empty worker automation");lanes[(size_t)index].push_back(std::move(lane));automated=true;
        }
    }
    int64 songSample(int64 sample) const {return looping&&sample>=loopEnd?loopStart+(sample-loopStart)%(loopEnd-loopStart):sample;}
    void makeTimeline() {
        if(graphMode)timeline=std::make_unique<NodeTimeline>(project,rate);
        else timeline=std::make_unique<Timeline>(project,rate);
    }
    void process(int64 at,int count,bool chase,const AudioBuffer<float>* external=nullptr,
                 const std::vector<AudioBuffer<float>>* sides=nullptr,int externalOffset=0) {
        work.clear(); input.clear();
        const auto audioAt=graphMode?songSample(at):at;
        for(auto& clip:clips) {
            if(graphMode&&at<graphOrigin)continue;
            const auto begin=std::max(audioAt,clip.start),end=std::min(audioAt+count,clip.start+clip.length);
            if(end<=begin)continue;
            input.clear();require(clip.reader->read(&input,0,(int)(end-begin),clip.sourceOffset+begin-clip.start,true,true),"Worker clip read failed");
            for(int ch=0;ch<2;++ch)work.addFrom(ch,(int)(begin-audioAt),input,ch,0,(int)(end-begin),clip.gain);
        }
        if(external)for(int ch=0;ch<2;++ch)work.addFrom(ch,0,*external,ch,externalOffset,count);
        int preceding=0;
        for(size_t index=0;index<plugins.size();++index) {
            auto& plugin=*plugins[index];const int64 rawSource=at-preceding,source=graphMode?songSample(rawSource):rawSource;
            const bool beforeOrigin=graphMode&&rawSource<graphOrigin;
            const bool resync=chase||(graphMode&&graphExpected[index]!=source);
            timeline->cursor=source; MidiBuffer midi;
            if(resync&&!beforeOrigin) {
                if(graphMode){for(const auto& note:activeNotes[index])midi.addEvent(MidiMessage::noteOff(note.first,note.second),0);activeNotes[index].clear();}
                for(int channel=1;channel<=16;++channel){midi.addEvent(MidiMessage::allSoundOff(channel),0);midi.addEvent(MidiMessage::allNotesOff(channel),0);}
                std::map<std::pair<int,int>,int> controls;
                for(auto cc:array(track,"controllers"))if(integer(cc,"sample")<source)
                    controls[{(int)integer(cc,"channel",1),(int)integer(cc,"controller")}]=(int)integer(cc,"value");
                for(const auto& value:controls)midi.addEvent(MidiMessage::controllerEvent(value.first.first,value.first.second,value.second),0);
                for(auto note:array(track,"notes"))if(integer(note,"sample")<source&&integer(note,"sample")+integer(note,"duration_samples")>source) {
                    midi.addEvent(MidiMessage::noteOn((int)integer(note,"channel",1),(int)integer(note,"note"),(float)number(note,"velocity",.8)),0);
                    if(graphMode)activeNotes[index].emplace((int)integer(note,"channel",1),(int)integer(note,"note"));
                }
                eventIndices[index]=(size_t)(std::lower_bound(events.begin(),events.end(),source,[](const auto& event,int64 sample){return event.sample<sample;})-events.begin());
            }
            auto& next=eventIndices[index];
            while(!beforeOrigin&&next<events.size()&&events[next].sample<source+count) {
                require(events[next].sample>=source,"Worker MIDI clock lost an event");
                midi.addEvent(events[next].message,(int)(events[next].sample-source)); ++next;
                if(graphMode){const auto& message=events[next-1].message;
                    if(message.isNoteOn())activeNotes[index].emplace(message.getChannel(),message.getNoteNumber());
                    if(message.isNoteOff())activeNotes[index].erase({message.getChannel(),message.getNoteNumber()});}
            }
            if(graphMode)graphExpected[index]=beforeOrigin?std::numeric_limits<int64>::min():source+count;
            for(const auto& lane:lanes[index]) {
                const auto nextPoint=std::upper_bound(lane.points.begin(),lane.points.end(),source,[](int64 sample,const auto& point){return sample<point.first;});
                float value=lane.base;
                if(nextPoint!=lane.points.begin()) {
                    const auto previous=std::prev(nextPoint);value=previous->second;
                    if(lane.linear&&nextPoint!=lane.points.end())value+=(nextPoint->second-value)*(float)((double)(source-previous->first)/(double)(nextPoint->first-previous->first));
                }
                if(std::abs(lane.target->getValue()-value)>1e-7f)lane.target->setValueNotifyingHost(value);
            }
            for(int ch=2;ch<channels;++ch)work.clear(ch,0,count);
            if(sides&&index<sides->size()&&plugin.getTotalNumInputChannels()==4)
                for(int ch=0;ch<2;++ch)work.copyFrom(ch+2,0,(*sides)[index],ch,externalOffset,count);
            AudioBuffer<float> block(work.getArrayOfWritePointers(),std::max({2,plugin.getTotalNumInputChannels(),plugin.getTotalNumOutputChannels()}),count);
            plugin.processBlock(block,midi); preceding+=plugin.getLatencySamples();
        }
        if(graphMode)latency=preceding;
        else require(preceding==latency,"Worker plugin latency changed; reopen track");
    }
public:
    explicit TrackRenderer(const var& value, mlengine::PluginRing* diagnostics=nullptr,bool graph=false,
                           std::function<void(uint32_t)> loading={}) :graphMode(graph) {
        formats.registerBasicFormats(); project=value; track=array(value,"tracks")[0];
        rate=number(project,"sample_rate",44100); quantum=(int)integer(project,"block_size",512);
        require(rate>=8000&&rate<=192000&&quantum>=16&&quantum<=8192,"Invalid worker clock");
        makeTimeline();
        for(auto spec:array(track,"plugins")) {
            if(diagnostics)diagnostics->loadStage.store(1);
            if(loading)loading(1);
            auto initial=spec.clone();initial.getDynamicObject()->removeProperty("parameters");
            auto plugin=loadWorkerPlugin(initial,rate,quantum);
            if(diagnostics)diagnostics->loadStage.store(2);
            if(loading)loading(2);
            std::map<String,float> values;
            std::map<String,AudioProcessorParameter*> targets;
            for(auto* value:plugin->getParameters()){
                const auto id=parameterId(value);values[id]=value->getValue();
                require(targets.emplace(id,value).second,"Duplicate worker parameter ID");
            }
            baseline.push_back(std::move(values));
            if(auto* overrides=field(spec,"parameters").getDynamicObject())for(const auto& value:overrides->getProperties())
            {
                const auto found=targets.find(value.name.toString());
                require(found!=targets.end(),"Unknown parameter ID: "+value.name.toString());
                auto* target=found->second;const auto wanted=(float)value.value;
                if(std::abs(target->getValue()-wanted)>1e-6f)target->setValueNotifyingHost(wanted);
            }
            require(plugin->getTotalNumInputChannels()<=(graphMode?4:2),"Worker sidechain needs graph support");
            require(hasSupportedStereoMainOutput(*plugin, AudioChannelSet::stereo(),
                                                 needsKeyscapeAuxiliaryOutputs(plugin->getPluginDescription())),
                    "Worker requires a supported stereo main output");
            channels=std::max(channels,plugin->getTotalNumOutputChannels());
            plugin->setNonRealtime(needsKeyscapeAuxiliaryOutputs(plugin->getPluginDescription()));
            plugin->setPlayHead(timeline.get());plugin->setRateAndBufferSizeDetails(rate,quantum);plugin->prepareToPlay(rate,quantum);
            if(diagnostics)diagnostics->loadStage.store(3);
            if(loading)loading(3);
            if(needsKeyscapeAuxiliaryOutputs(plugin->getPluginDescription()))MessageManager::getInstance()->runDispatchLoopUntil(10000);
            latency+=plugin->getLatencySamples();plugins.push_back(std::move(plugin));
        }
        require(latency>=0&&latency<rate*(graphMode?10:2),"Worker plugin latency exceeds processing budget");
        for(auto clip:array(track,"audio_clips")) {
            auto reader=std::unique_ptr<AudioFormatReader>(formats.createReaderFor(File(text(clip,"path"))));
            require(reader&&reader->sampleRate==rate,"Worker audio source mismatch");
            clips.push_back({std::move(reader),integer(clip,"sample"),integer(clip,"source_offset_samples",0),integer(clip,"duration_samples"),(float)number(clip,"gain",1)});
        }
        work.setSize(channels,quantum); input.setSize(2,quantum); rebuildEvents();
        activeNotes.resize(plugins.size());rebuildAutomation();
        graphExpected.assign(plugins.size(),std::numeric_limits<int64>::min());
        if(diagnostics)diagnostics->loadStage.store(4);
        if(loading)loading(4);
    }
    ~TrackRenderer() override {for(auto& plugin:plugins){plugin->releaseResources();plugin->setPlayHead(nullptr);}}
    void prepareToPlay(int,double) override {}
    void releaseResources() override {}
    void seek(int64 sample,bool loop,int64 begin,int64 end) {
        cursor=sample;looping=loop;loopStart=begin;loopEnd=end;reset=true;
        for(auto& plugin:plugins)plugin->reset();
    }
    void update(const var& value) {
        require(array(array(value,"tracks")[0],"plugins").size()==(int)plugins.size(),"Worker plugin chain changed");
        const bool eventsChanged=JSON::toString(field(track,"notes"),true)!=JSON::toString(field(array(value,"tracks")[0],"notes"),true)
            ||JSON::toString(field(track,"controllers"),true)!=JSON::toString(field(array(value,"tracks")[0],"controllers"),true);
        project=value;track=array(value,"tracks")[0];makeTimeline();
        auto specs=array(track,"plugins");
        for(size_t i=0;i<plugins.size();++i) {
            plugins[i]->setPlayHead(timeline.get());
            const auto values=field(specs[(int)i],"parameters");
            for(auto* value:plugins[i]->getParameters()) {
                const auto id=parameterId(value);const float target=(float)(double)values.getProperty(id,baseline[i].at(id));
                if(value->getValue()!=target)value->setValueNotifyingHost(target);
            }
        }
        if(!graphMode||eventsChanged){rebuildEvents();reset=true;graphExpected.assign(plugins.size(),std::numeric_limits<int64>::min());}
        rebuildAutomation();
    }
    int reportedLatency() const {return latency;}
    std::vector<int> reportedLatencies() const {std::vector<int> result;for(const auto& plugin:plugins)result.push_back(plugin->getLatencySamples());return result;}
    void graphBlock(int64 globalSample,int64 inputDelay,int64 origin,bool chase,bool loop,int64 begin,int64 end,
                    const AudioBuffer<float>& external,const std::vector<AudioBuffer<float>>& sides,AudioBuffer<float>& output,int count) {
        require(graphMode&&count>0&&count<=quantum,"Invalid graph worker block");
        looping=loop;loopStart=begin;loopEnd=end;graphOrigin=origin;
        auto* clock=static_cast<NodeTimeline*>(timeline.get());clock->looping=loop;clock->loopStart=begin;clock->loopEnd=end;
        if(chase){for(auto& plugin:plugins)plugin->reset();graphExpected.assign(plugins.size(),std::numeric_limits<int64>::min());}
        int done=0;
        while(done<count) {
            const auto at=globalSample+done-inputDelay;
            int n=count-done,preceding=0;
            for(size_t i=0;i<=plugins.size();++i) {
                const auto raw=at-preceding,sample=songSample(raw);
                if(raw<origin)n=(int)std::min<int64>(n,origin-raw);
                if(loop)n=(int)std::min<int64>(n,end-sample);
                timeline->cursor=sample;n=(int)std::min<int64>(n,timeline->nextBoundary(sample+n)-sample);
                if(i<plugins.size())preceding+=plugins[i]->getLatencySamples();
            }
            if(automated)n=1;
            require(n>0,"Invalid graph timeline split");
            process(at,n,false,&external,&sides,done);
            for(int ch=0;ch<2;++ch)output.copyFrom(ch,done,work,ch,0,n);
            done+=n;
        }
    }
    void getNextAudioBlock(const AudioSourceChannelInfo& info) override {
        int done=0;
        while(done<info.numSamples) {
            if(looping&&cursor>=loopEnd){cursor=loopStart+(cursor-loopStart)%(loopEnd-loopStart);reset=true;for(auto& p:plugins)p->reset();}
            const int wanted=looping?(int)std::min<int64>(info.numSamples-done,loopEnd-cursor):info.numSamples-done;
            const int discard=reset?latency:0; const auto origin=cursor+(reset?0:latency); int processed=0;
            while(processed<wanted+discard) {
                auto end=std::min<int64>(origin+processed+quantum,origin+wanted+discard);
                timeline->cursor=origin+processed;end=timeline->nextBoundary(end);
                if(automated)end=origin+processed+1;
                const int n=(int)(end-origin-processed);process(origin+processed,n,reset&&processed==0);
                for(int i=0;i<n;++i){const int destination=processed+i-discard;if(destination<0)continue;
                    for(int ch=0;ch<2;++ch){const float sample=work.getSample(ch,i);require(std::isfinite(sample),"Non-finite worker output");info.buffer->setSample(ch,info.startSample+done+destination,sample);}}
                processed+=n;
            }
            cursor+=wanted;done+=wanted;reset=false;
        }
    }
};
}
