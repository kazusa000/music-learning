#pragma once

namespace ml {
// Worker-only DSP. It never owns a device callback or talks to Python for PCM.
class TrackRenderer final : public AudioSource {
    var project, track;
    std::unique_ptr<Timeline> timeline;
    std::vector<std::unique_ptr<AudioPluginInstance>> plugins;
    std::vector<std::map<String,float>> baseline;
    std::vector<MidiEvent> events;
    std::vector<size_t> eventIndices;
    AudioFormatManager formats;
    std::vector<AudioClip> clips;
    int channels=4, quantum=512, latency=0;
    int64 cursor=0, loopStart=0, loopEnd=0;
    bool looping=false, reset=true;
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
    void process(int64 at,int count,bool chase) {
        work.clear(); input.clear();
        for(auto& clip:clips) {
            const auto begin=std::max(at,clip.start),end=std::min(at+count,clip.start+clip.length);
            if(end<=begin)continue;
            input.clear();require(clip.reader->read(&input,0,(int)(end-begin),clip.sourceOffset+begin-clip.start,true,true),"Worker clip read failed");
            for(int ch=0;ch<2;++ch)work.addFrom(ch,(int)(begin-at),input,ch,0,(int)(end-begin),clip.gain);
        }
        int preceding=0;
        for(size_t index=0;index<plugins.size();++index) {
            auto& plugin=*plugins[index]; const int64 source=at-preceding; timeline->cursor=source; MidiBuffer midi;
            if(chase) {
                for(int channel=1;channel<=16;++channel){midi.addEvent(MidiMessage::allSoundOff(channel),0);midi.addEvent(MidiMessage::allNotesOff(channel),0);}
                std::map<std::pair<int,int>,int> controls;
                for(auto cc:array(track,"controllers"))if(integer(cc,"sample")<source)
                    controls[{(int)integer(cc,"channel",1),(int)integer(cc,"controller")}]=(int)integer(cc,"value");
                for(const auto& value:controls)midi.addEvent(MidiMessage::controllerEvent(value.first.first,value.first.second,value.second),0);
                for(auto note:array(track,"notes"))if(integer(note,"sample")<source&&integer(note,"sample")+integer(note,"duration_samples")>source)
                    midi.addEvent(MidiMessage::noteOn((int)integer(note,"channel",1),(int)integer(note,"note"),(float)number(note,"velocity",.8)),0);
                eventIndices[index]=(size_t)(std::lower_bound(events.begin(),events.end(),source,[](const auto& event,int64 sample){return event.sample<sample;})-events.begin());
            }
            auto& next=eventIndices[index];
            while(next<events.size()&&events[next].sample<source+count) {
                require(events[next].sample>=source,"Worker MIDI clock lost an event");
                midi.addEvent(events[next].message,(int)(events[next].sample-source)); ++next;
            }
            const auto specs=array(track,"plugins");
            for(auto lane:array(track,"automation"))if(text(lane,"plugin_id")==text(specs[(int)index],"id")) {
                auto points=array(lane,"points");
                for(int i=0;i<points.size();++i)if(integer(points[i],"sample")<=source) {
                    auto value=number(points[i],"value",0);
                    if(text(lane,"interpolation")=="linear"&&i+1<points.size()&&integer(points[i+1],"sample")>source)
                        value+=(number(points[i+1],"value",0)-value)*(double)(source-integer(points[i],"sample"))/(double)(integer(points[i+1],"sample")-integer(points[i],"sample"));
                    parameter(plugin,text(lane,"parameter_id"))->setValueNotifyingHost((float)value);
                }
            }
            for(int ch=2;ch<channels;++ch)work.clear(ch,0,count);
            AudioBuffer<float> block(work.getArrayOfWritePointers(),std::max({2,plugin.getTotalNumInputChannels(),plugin.getTotalNumOutputChannels()}),count);
            plugin.processBlock(block,midi); preceding+=plugin.getLatencySamples();
        }
        require(preceding==latency,"Worker plugin latency changed; reopen track");
    }
public:
    explicit TrackRenderer(const var& value, mlengine::PluginRing* diagnostics=nullptr) {
        formats.registerBasicFormats(); project=value; track=array(value,"tracks")[0];
        rate=number(project,"sample_rate",44100); quantum=(int)integer(project,"block_size",512);
        require(rate>=8000&&rate<=192000&&quantum>=16&&quantum<=8192,"Invalid worker clock");
        timeline=std::make_unique<Timeline>(project,rate);
        for(auto spec:array(track,"plugins")) {
            if(diagnostics)diagnostics->loadStage.store(1);
            auto initial=spec.clone();initial.getDynamicObject()->removeProperty("parameters");
            auto plugin=load(initial,rate,quantum);
            if(diagnostics)diagnostics->loadStage.store(2);
            std::map<String,float> values;
            for(auto* value:plugin->getParameters())values[parameterId(value)]=value->getValue();
            baseline.push_back(std::move(values));
            if(auto* overrides=field(spec,"parameters").getDynamicObject())for(const auto& value:overrides->getProperties())
            {
                auto* target=parameter(*plugin,value.name.toString());const auto wanted=(float)value.value;
                if(std::abs(target->getValue()-wanted)>1e-6f)target->setValueNotifyingHost(wanted);
            }
            require(plugin->getTotalNumInputChannels()<=2,"Worker sidechain needs graph support");
            require(plugin->getTotalNumOutputChannels()==2||needsKeyscapeAuxiliaryOutputs(plugin->getPluginDescription()),"Worker requires stereo output");
            channels=std::max(channels,plugin->getTotalNumOutputChannels());
            plugin->setNonRealtime(needsKeyscapeAuxiliaryOutputs(plugin->getPluginDescription()));
            plugin->setPlayHead(timeline.get());plugin->setRateAndBufferSizeDetails(rate,quantum);plugin->prepareToPlay(rate,quantum);
            if(diagnostics)diagnostics->loadStage.store(3);
            if(needsKeyscapeAuxiliaryOutputs(plugin->getPluginDescription()))MessageManager::getInstance()->runDispatchLoopUntil(10000);
            latency+=plugin->getLatencySamples();plugins.push_back(std::move(plugin));
        }
        require(latency>=0&&latency<rate*2,"Worker plugin latency exceeds two seconds");
        for(auto clip:array(track,"audio_clips")) {
            auto reader=std::unique_ptr<AudioFormatReader>(formats.createReaderFor(File(text(clip,"path"))));
            require(reader&&reader->sampleRate==rate,"Worker audio source mismatch");
            clips.push_back({std::move(reader),integer(clip,"sample"),integer(clip,"source_offset_samples",0),integer(clip,"duration_samples"),(float)number(clip,"gain",1)});
        }
        work.setSize(channels,quantum); input.setSize(2,quantum); rebuildEvents();
        if(diagnostics)diagnostics->loadStage.store(4);
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
        project=value;track=array(value,"tracks")[0];timeline=std::make_unique<Timeline>(project,rate);
        auto specs=array(track,"plugins");
        for(size_t i=0;i<plugins.size();++i) {
            plugins[i]->setPlayHead(timeline.get());
            const auto values=field(specs[(int)i],"parameters");
            for(auto* value:plugins[i]->getParameters()) {
                const auto id=parameterId(value);const float target=(float)(double)values.getProperty(id,baseline[i].at(id));
                if(value->getValue()!=target)value->setValueNotifyingHost(target);
            }
        }
        rebuildEvents();reset=true;
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
                if(!array(track,"automation").isEmpty())end=origin+processed+1;
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
