#define NOMINMAX
// Separate executable: the offline host and running verification stay unchanged.
#define main ml_offline_main
#include "../audio-host/Main.cpp"
#undef main
#include "../audio-host/Render.cpp"

namespace ml {
class LiveTrack {
    double rate = 48000;
    int blockSize = 512, channels = 4, latency = 0;
    int64 cursor = -1;
    var project, track;
    std::unique_ptr<Timeline> timeline;
    std::vector<std::unique_ptr<AudioPluginInstance>> plugins;
    std::vector<AudioClip> clips;
    AudioFormatManager formats;
    bool resetVoices = true;
    std::vector<MidiEvent> events;
    std::vector<size_t> eventIndices;
    void buildEvents() {
        events.clear();eventIndices.assign(plugins.size(),0);
        for(auto note:array(track,"notes")){
            const auto start=integer(note,"sample"),end=start+integer(note,"duration_samples");
            const auto ch=static_cast<int>(integer(note,"channel",1)),pitch=static_cast<int>(integer(note,"note"));
            events.push_back({start,2,MidiMessage::noteOn(ch,pitch,static_cast<float>(number(note,"velocity",.8)))});
            events.push_back({end,0,MidiMessage::noteOff(ch,pitch)});
        }
        for(auto cc:array(track,"controllers"))events.push_back({integer(cc,"sample"),1,MidiMessage::controllerEvent(static_cast<int>(integer(cc,"channel",1)),static_cast<int>(integer(cc,"controller")),static_cast<int>(integer(cc,"value")))});
        std::stable_sort(events.begin(),events.end(),[](const auto& a,const auto& b){return a.sample==b.sample?a.priority<b.priority:a.sample<b.sample;});
    }
    void audioClips() {
        clips.clear();
        for (auto value : array(track, "audio_clips")) {
            std::unique_ptr<AudioFormatReader> reader(formats.createReaderFor(File(text(value, "path"))));
            require(reader != nullptr && reader->sampleRate == rate, "Live audio needs source-rate PCM audio");
            clips.push_back({std::move(reader), integer(value, "sample"), integer(value, "source_offset_samples", 0),
                integer(value, "duration_samples"), static_cast<float>(number(value, "gain", 1))});
        }
    }
    void process(AudioBuffer<float>& out, int64 source, int count, bool reset) {
        out.clear();
        AudioBuffer<float> input(2, count);
        for (auto& clip : clips) {
            const auto begin = std::max(source, clip.start), end = std::min(source + count, clip.start + clip.length);
            if (begin >= end) continue;
            input.clear();
            require(clip.reader->read(&input, 0, static_cast<int>(end-begin), clip.sourceOffset + begin-clip.start, true, true), "Live clip read failed");
            for (int ch=0;ch<2;++ch) out.addFrom(ch, static_cast<int>(begin-source), input, ch, 0, static_cast<int>(end-begin), clip.gain);
        }
        int preceding = 0;
        for (size_t index=0;index<plugins.size();++index) {
            auto& plugin = plugins[index];
            const int64 at = source - preceding;
            timeline->cursor = at;
            MidiBuffer midi;
            if (reset) {
                for(int ch=1;ch<=16;++ch) {midi.addEvent(MidiMessage::allSoundOff(ch),0);midi.addEvent(MidiMessage::allNotesOff(ch),0);}
                // Chase the latest controller value at a seek/update, then held notes.
                std::map<std::pair<int,int>, int> chase;
                for(auto cc:array(track,"controllers")) if(integer(cc,"sample")<at)
                    chase[{static_cast<int>(integer(cc,"channel",1)),static_cast<int>(integer(cc,"controller"))}]=static_cast<int>(integer(cc,"value"));
                for(auto item:chase) midi.addEvent(MidiMessage::controllerEvent(item.first.first,item.first.second,item.second),0);
            }
            auto& eventIndex=eventIndices[index];
            if(reset){
                eventIndex=static_cast<size_t>(std::lower_bound(events.begin(),events.end(),at,[](const auto& event,int64 sample){return event.sample<sample;})-events.begin());
                for(auto note:array(track,"notes")){
                    auto start=integer(note,"sample"),end=start+integer(note,"duration_samples");
                    if(start<at&&end>at)midi.addEvent(MidiMessage::noteOn(static_cast<int>(integer(note,"channel",1)),static_cast<int>(integer(note,"note")),static_cast<float>(number(note,"velocity",.8))),0);
                }
            }
            while(eventIndex<events.size()&&events[eventIndex].sample<at+count){
                const auto& event=events[eventIndex++];
                require(event.sample>=at,"Live MIDI scheduler lost an event");
                midi.addEvent(event.message,static_cast<int>(event.sample-at));
            }
            const auto specs=array(track,"plugins");
            for(auto lane:array(track,"automation")) if(text(lane,"plugin_id")==text(specs[static_cast<int>(index)],"id")) {
                auto points=array(lane,"points");
                for(int i=0;i<points.size();++i) if(integer(points[i],"sample")<=at) {
                    double v=number(points[i],"value",0);
                    if(text(lane,"interpolation")=="linear"&&i+1<points.size()&&integer(points[i+1],"sample")>at)
                        v+=(number(points[i+1],"value",0)-v)*static_cast<double>(at-integer(points[i],"sample"))/static_cast<double>(integer(points[i+1],"sample")-integer(points[i],"sample"));
                    parameter(*plugin,text(lane,"parameter_id"))->setValueNotifyingHost(static_cast<float>(v));
                }
            }
            for(int ch=2;ch<channels;++ch) out.clear(ch,0,count);
            AudioBuffer<float> view(out.getArrayOfWritePointers(),std::max({2,plugin->getTotalNumInputChannels(),plugin->getTotalNumOutputChannels()}),count);
            plugin->processBlock(view,midi);
            preceding+=plugin->getLatencySamples();
        }
        require(preceding==latency,"Plugin changed latency; reopen this track");
    }
public:
    LiveTrack() {formats.registerBasicFormats();}
    ~LiveTrack() {for(auto& p:plugins){p->releaseResources();p->setPlayHead(nullptr);}}
    var init(const var& value) {
        project=field(value,"project");track=array(project,"tracks")[0];
        rate=number(project,"sample_rate",48000);blockSize=static_cast<int>(integer(project,"block_size",512));
        require(rate>=8000&&rate<=192000&&blockSize>=16&&blockSize<=8192,"Invalid live audio clock");
        timeline=std::make_unique<Timeline>(project,rate);
        for(auto spec:array(track,"plugins")) {
            auto plugin=load(spec,rate,blockSize);
            require(plugin->getTotalNumInputChannels()<=2,"Live track does not accept sidechains");
            require(hasSupportedStereoMainOutput(*plugin, AudioChannelSet::stereo(),
                                                 needsKeyscapeAuxiliaryOutputs(plugin->getPluginDescription())),
                    "Live track needs a supported stereo main output");
            channels=std::max(channels,plugin->getTotalNumOutputChannels());
            // This child produces PCM ahead of the browser clock; it is not a
            // device callback. Keyscape must finish its sample work per block.
            plugin->setNonRealtime(needsKeyscapeAuxiliaryOutputs(plugin->getPluginDescription()));plugin->setPlayHead(timeline.get());plugin->setRateAndBufferSizeDetails(rate,blockSize);plugin->prepareToPlay(rate,blockSize);
            if(needsKeyscapeAuxiliaryOutputs(plugin->getPluginDescription())) MessageManager::getInstance()->runDispatchLoopUntil(10000);
            latency+=plugin->getLatencySamples();plugins.push_back(std::move(plugin));
        }
        require(latency>=0&&latency<rate*2,"Live plugin latency exceeds two seconds");
        audioClips();buildEvents();
        return object({{"ready",true},{"latency_samples",latency},{"sample_rate",rate}});
    }
    var update(const var& value) {
        project=field(value,"project");track=array(project,"tracks")[0];timeline=std::make_unique<Timeline>(project,rate);
        for(auto& p:plugins)p->setPlayHead(timeline.get());
        auto specs=array(track,"plugins");
        require(specs.size()==static_cast<int>(plugins.size()),"Live plugin chain changed; reopen this track");
        for(int i=0;i<specs.size();++i)if(auto* values=field(specs[i],"parameters").getDynamicObject())
            for(auto& item:values->getProperties()){
                auto* param=parameter(*plugins[static_cast<size_t>(i)],item.name.toString());
                const auto v=static_cast<float>(item.value);require(v>=0&&v<=1,"Invalid live parameter");param->setValueNotifyingHost(v);
            }
        audioClips();buildEvents();resetVoices=true;
        return object({{"ready",true}});
    }
    var patchNotes(const var& value) {
        std::set<String> replace;
        for(auto id:array(value,"removed"))replace.insert(id.toString());
        for(auto note:array(value,"notes"))replace.insert(text(note,"id"));
        Array<var> notes;
        for(auto note:array(track,"notes"))if(!replace.count(text(note,"id")))notes.add(note);
        notes.addArray(array(value,"notes"));
        track.getDynamicObject()->setProperty("notes",notes);
        project.getDynamicObject()->setProperty("revision_id",field(value,"revision_id"));
        buildEvents();resetVoices=true;
        return object({{"ready",true},{"notes",notes.size()}});
    }
    var chunk(const var& request) {
        const int64 sample=integer(request,"sample");const int frames=static_cast<int>(integer(request,"frames"));
        require(sample>=0&&sample<integer(project,"duration_samples")&&frames>0&&frames<=16384,"Invalid live chunk range");
        const bool reset=resetVoices||cursor!=sample;
        if(reset){for(auto& p:plugins)p->reset();cursor=sample;}
        AudioBuffer<float> buffer(channels,blockSize);
        std::vector<float> interleaved(static_cast<size_t>(frames)*2);
        const int discard=reset?latency:0;
        int processed=0;
        const int64 origin=sample+(reset?0:latency);
        while(processed<frames+discard) {
            // Automation and tempo boundaries are evaluated on the source clock.
            auto end=std::min<int64>(origin+processed+blockSize,origin+frames+discard);
            timeline->cursor=origin+processed;
            end=timeline->nextBoundary(end);
            const bool automated=!array(track,"automation").isEmpty();
            if(automated)end=origin+processed+1;
            const int n=static_cast<int>(end-origin-processed);
            process(buffer,origin+processed,n,reset&&processed==0);
            for(int i=0;i<n;++i){const int dest=processed+i-discard;if(dest<0)continue;
                for(int ch=0;ch<2;++ch){auto f=buffer.getSample(ch,i);require(std::isfinite(f),"Non-finite live output");interleaved[static_cast<size_t>(dest)*2+ch]=f;}}
            processed+=n;
        }
        // Internal processing stays ahead by the chain latency after its initial priming.
        cursor=sample+frames;resetVoices=false;
        MessageManager::getInstance()->runDispatchLoopUntil(1);
        MemoryOutputStream encoded;Base64::convertToBase64(encoded,interleaved.data(),interleaved.size()*sizeof(float));
        return object({{"sample",sample},{"frames",frames},{"sample_rate",rate},{"channels",2},{"pcm_f32le",encoded.toString()}});
    }
};
}
int main() {
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    _set_abort_behavior(0, _WRITE_ABORT_MSG | _CALL_REPORTFAULT);
    juce::ScopedJuceInitialiser_GUI initialiser;
    std::unique_ptr<ml::LiveTrack> track;
    std::string line;
    while(std::getline(std::cin,line)) {
        juce::var request;juce::JSON::parse(juce::String::fromUTF8(line.c_str()),request);
        try {
            auto action=ml::text(request,"action");juce::var result;
            if(action=="init"){track=std::make_unique<ml::LiveTrack>();result=track->init(request);}
            else {ml::require(track!=nullptr,"Track not opened");if(action=="update")result=track->update(request);else if(action=="patch_notes")result=track->patchNotes(request);else if(action=="chunk")result=track->chunk(request);else if(action=="close")break;else throw std::runtime_error("Unknown live action");}
            std::cout<<"MLLIVE "<<juce::JSON::toString(ml::object({{"ok",true},{"result",result}}),true).toStdString()<<std::endl;
        } catch(const std::exception& error) {std::cout<<"MLLIVE "<<juce::JSON::toString(ml::object({{"ok",false},{"error",juce::String(error.what())}}),true).toStdString()<<std::endl;}
    }
}
