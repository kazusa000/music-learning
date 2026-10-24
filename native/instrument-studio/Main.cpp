// A dedicated audition instance with its real VST3 editor. No production project is opened.
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <juce_audio_processors/juce_audio_processors.h>
#include <juce_audio_devices/juce_audio_devices.h>
// Reuse the existing verified complete-state/resource loader with a GUI-capable format.
#define main ml_legacy_entry
#define VST3PluginFormatHeadless VST3PluginFormat
#include "../audio-host/Main.cpp"
#undef VST3PluginFormatHeadless
#undef main
#include "../audio-host/Render.cpp"
#include <atomic>
#include <array>
#include <thread>

using namespace juce;

class Studio final : public AudioIODeviceCallback, public AudioProcessorListener {
    File root; var config; String token;
    std::unique_ptr<AudioPluginInstance> plugin;
    std::unique_ptr<AudioProcessorEditor> editor;
    std::unique_ptr<DocumentWindow> floating;
    AudioDeviceManager device;
    AudioBuffer<float> work,recording;
    AbstractFifo fifo{512}; std::array<MidiMessage,512> messages;
    MidiBuffer midi;
    std::atomic<bool> suspended{false},ack{false},panic{false},record{false};
    std::atomic<int> held{0},recordFrames{0};
    std::atomic<int64> samples{0},callbacks{0},nonzero{0},midiOn{0},midiOff{0},changes{0};
    std::atomic<double> energy{0}; std::atomic<float> peak{0},volume{.6f};
    double rate=48000,lastChange=0,lastSnapshot=0,lastStatus=0,lastContact=0;
    int block=512; String stateHash,parameterHash; bool stopping=false,embedded=false,editorAvailable=false,sustainHeld=false;
    int64 snapshotChanges=-1;
    float viewScale=1;
    HWND parent=nullptr; uint64_t commandSequence=0;
    std::array<bool,128> keys{};
    std::array<int,128> controllers;
    var controllerState(){auto value=ml::object({});for(int i:{1,7,10,11})if(controllers[(size_t)i]>=0)value.getDynamicObject()->setProperty(String(i),controllers[(size_t)i]);return value;}

    struct Paused {
        Studio& s;
        explicit Paused(Studio& owner):s(owner){
            s.ack.store(false);s.suspended.store(true);
            const auto until=Time::getMillisecondCounterHiRes()+1500;
            while(!s.ack.load()&&s.device.getCurrentAudioDevice()!=nullptr){
                if(Time::getMillisecondCounterHiRes()>until){s.suspended.store(false);throw std::runtime_error("Audio state barrier timed out");}
                Thread::sleep(1);
            }
        }
        ~Paused(){s.suspended.store(false);}
    };
    void enqueue(const MidiMessage& message){
        int a,n,b,m;fifo.prepareToWrite(1,a,n,b,m);
        if(!n){panic.store(true);throw std::runtime_error("Audition MIDI queue is full");}
        messages[(size_t)a]=message;fifo.finishedWrite(1);
    }
    void releaseKeys(){
        for(int i=0;i<128;++i)if(keys[(size_t)i]){enqueue(MidiMessage::noteOff(1,i));keys[(size_t)i]=false;midiOff.fetch_add(1);}
        enqueue(MidiMessage::controllerEvent(1,64,0));panic.store(true);held.store(0);sustainHeld=false;
    }
    var state(){
        auto result=ml::object({{"status","ready"},{"pid",(int)GetCurrentProcessId()},
            {"session_id",config["session_id"]},{"model_id",config["model_id"]},{"preset_id",config["preset_id"]},
            {"editor_available",editorAvailable},{"editor_open",editor&&editor->isVisible()},
            {"editor_embedded",embedded},{"state_sha256",stateHash},{"state_file",stateHash.isEmpty()?String():stateHash.substring(0,16)+".bin"},
            {"editor_scale",viewScale},
            {"controllers",controllerState()},
            {"held_notes",held.load()},
            {"changes",(int64)changes.load()},{"parameters_sha256",parameterHash},{"sample_rate",rate},{"callbacks",callbacks.load()},
            {"samples",samples.load()},{"nonzero_samples",nonzero.load()},{"energy",energy.load()},
            {"peak",peak.load()},{"midi_on",midiOn.load()},{"midi_off",midiOff.load()}});
        if(editor){result.getDynamicObject()->setProperty("editor_width",editor->getWidth());result.getDynamicObject()->setProperty("editor_height",editor->getHeight());
            if(auto* peer=editor->getPeer()){
                auto window=(HWND)peer->getNativeHandle();
                result.getDynamicObject()->setProperty("editor_handle",String::toHexString((int64)(intptr_t)window));
                result.getDynamicObject()->setProperty("editor_parent_handle",String::toHexString((int64)(intptr_t)GetParent(window)));
                RECT box{};GetClientRect(window,&box);
                result.getDynamicObject()->setProperty("editor_pixels_width",(int)box.right);result.getDynamicObject()->setProperty("editor_pixels_height",(int)box.bottom);
            }}
        return result;
    }
    void snapshot(){
        MemoryBlock bytes;
        {Paused pause(*this);plugin->getStateInformation(bytes);}
        ml::require(bytes.getSize()>0&&bytes.getSize()<64*1024*1024,"Invalid complete plugin state");
        const auto sha=SHA256(bytes).toHexString();
        if(sha!=stateHash){
            auto target=root.getChildFile(sha.substring(0,16)+".bin");
            if(!target.existsAsFile()){
                TemporaryFile pending(target);
                {auto stream=pending.getFile().createOutputStream();ml::require(stream!=nullptr&&stream->write(bytes.getData(),bytes.getSize()),"Cannot write audition draft");stream->flush();ml::require(stream->getStatus().wasOk(),"Cannot flush audition draft");}
                ml::require(pending.overwriteTargetFileWithTemporary(),"Cannot retain audition draft");
            }
            ml::require(SHA256(target).toHexString()==sha,"Audition state identity collision");
            stateHash=sha;
        }
        const auto parameterState=ml::object({{"parameters",ml::parameters(*plugin)},{"controllers",controllerState()}});
        parameterHash=SHA256(JSON::toString(parameterState).toUTF8()).toHexString();
        ml::writeJson(root.getChildFile(sha.substring(0,16)+".params.json"),parameterState);
        lastSnapshot=Time::getMillisecondCounterHiRes();
        snapshotChanges=changes.load();
    }
    void closeView(){
        embedded=false;parent=nullptr;
        if(floating){floating->clearContentComponent();floating.reset();}
        if(editor){editor->setVisible(false);editor->removeFromDesktop();}
    }
    var showView(const var& args){
        const bool visible=(bool)args.getProperty("visible",true);
        if(!visible){closeView();return state();}
        if(!editor)editor.reset(plugin->createEditorAndMakeActive());
        ml::require(editor!=nullptr,"This plugin does not provide a native editor");
        const auto handle=args.getProperty("parent_handle","").toString().getHexValue64();
        if(handle){
            auto host=(HWND)(intptr_t)handle;DWORD pid=0;
            ml::require(IsWindow(host)&&GetWindowThreadProcessId(host,&pid)&&pid==(DWORD)(int)args["parent_pid"],"Native editor parent identity changed");
            if(!embedded||parent!=host){
                closeView();parent=host;editor->addToDesktop(0,host);embedded=true;editor->setVisible(true);
                // JUCE delivers the peer's DPI notification asynchronously.
                // Let it arrive before selecting content scale or size constraints.
                MessageManager::getInstance()->runDispatchLoopUntil(60);
            }
            RECT bounds{};GetClientRect(host,&bounds);
            const double scale=(double)args.getProperty("scale",1.0);
            ml::require(std::isfinite(scale)&&scale>=.5&&scale<=4,"Invalid editor scale");
            // The plugin's own size constraints and native DPI choose a supported
            // layout. Arbitrary zoom factors can select nonexistent raster assets.
            if(viewScale!=1){viewScale=1;editor->setScaleFactor(1);}
            if(editor->isResizable())editor->setBoundsConstrained(juce::Rectangle<int>(0,0,jmax(1,(int)(bounds.right/scale)),jmax(1,(int)(bounds.bottom/scale))));
            editor->setTopLeftPosition(0,0);editor->setVisible(true);
            if(auto* peer=editor->getPeer()){
                auto window=(HWND)peer->getNativeHandle();
                RECT actual{};GetWindowRect(window,&actual);
                const double fit=jmin(bounds.right/(double)jmax(1L,actual.right-actual.left),bounds.bottom/(double)jmax(1L,actual.bottom-actual.top));
                if(fit<.999){
                    // Never request a fractional raster scale below 100%.
                    // The plugin itself renders the selected native 1x/2x tier.
                    const double effective=jmax(1.0,std::floor(scale*fit));
                    viewScale=(float)jmin(1.0,effective/scale);editor->setScaleFactor(viewScale);
                    GetWindowRect(window,&actual);
                }
                const int width=actual.right-actual.left,height=actual.bottom-actual.top;
                SetWindowPos(window,HWND_TOP,jmax(0,((int)bounds.right-width)/2),jmax(0,((int)bounds.bottom-height)/2),0,0,SWP_NOSIZE|SWP_NOACTIVATE|SWP_SHOWWINDOW);
                ml::require(GetParent(window)==host,"Plugin editor could not attach to the application");
            }
        }else{
            closeView();
            class Window final:public DocumentWindow{public:Window():DocumentWindow("Instrument",Colour(0xff171719),DocumentWindow::allButtons){}void closeButtonPressed()override{setVisible(false);}};
            floating=std::make_unique<Window>();floating->setUsingNativeTitleBar(true);
            floating->setContentNonOwned(editor.get(),true);floating->setResizable(editor->isResizable(),false);
            floating->centreWithSize(editor->getWidth(),editor->getHeight());floating->setVisible(true);editor->setVisible(true);
        }
        return state();
    }
    var command(const var& request){
        const auto action=request["action"].toString();const auto args=request["arguments"];
        if(action=="status")return state();
        if(action=="note"){
            const int pitch=(int)args["pitch"];const float velocity=(float)args.getProperty("velocity",.8);
            ml::require(pitch>=0&&pitch<=127&&std::isfinite(velocity)&&velocity>=0&&velocity<=1,"Invalid audition note");
            const bool on=(bool)args["on"];
            if(on){enqueue(MidiMessage::noteOn(1,pitch,velocity));if(!keys[(size_t)pitch])held.fetch_add(1);keys[(size_t)pitch]=true;midiOn.fetch_add(1);}
            else{enqueue(MidiMessage::noteOff(1,pitch));if(keys[(size_t)pitch])held.fetch_sub(1);keys[(size_t)pitch]=false;midiOff.fetch_add(1);lastChange=Time::getMillisecondCounterHiRes();}
        }else if(action=="panic")releaseKeys();
        else if(action=="sustain"){sustainHeld=(bool)args["on"];enqueue(MidiMessage::controllerEvent(1,64,sustainHeld?127:0));lastChange=Time::getMillisecondCounterHiRes();}
        else if(action=="volume"){const float value=(float)args["value"];ml::require(std::isfinite(value)&&value>=0&&value<=1,"Invalid volume");volume.store(value);}
        else if(action=="controller"){
            const int number=(int)args["controller"],value=(int)args["value"];
            ml::require((number==1||number==7||number==10||number==11)&&value>=0&&value<=127,"Unsupported audition controller");
            controllers[(size_t)number]=value;enqueue(MidiMessage::controllerEvent(1,number,value));changes.fetch_add(1);lastChange=Time::getMillisecondCounterHiRes();
        }
        else if(action=="parameter"){
            auto* parameter=ml::parameter(*plugin,args["parameter_id"].toString());float value=(float)args["value"];
            ml::require(parameter->isAutomatable()&&std::isfinite(value)&&value>=0&&value<=1,"Parameter is not writable");
            if(parameter->isDiscrete()&&parameter->getNumSteps()>1)value=std::round(value*(parameter->getNumSteps()-1))/(parameter->getNumSteps()-1);
            parameter->beginChangeGesture();parameter->setValueNotifyingHost(value);parameter->endChangeGesture();
            ml::require(std::abs(parameter->getValue()-value)<1e-4f,"Plugin did not retain parameter");lastChange=Time::getMillisecondCounterHiRes();
        }else if(action=="parameters"){
            auto all=ml::parameters(*plugin);Array<var> found;
            const auto search=args.getProperty("search","").toString();const int offset=(int)args.getProperty("offset",0),limit=(int)args.getProperty("limit",48);
            ml::require(offset>=0&&limit>0&&limit<=256,"Invalid parameter page");
            for(auto item:*all.getArray())if((!(bool)args.getProperty("writable_only",false)||(bool)item["automatable"])&&(search.isEmpty()||item["name"].toString().containsIgnoreCase(search)))found.add(item);
            Array<var> page;for(int i=offset;i<jmin(found.size(),offset+limit);++i)page.add(found[i]);
            return ml::object({{"total",found.size()},{"items",var(page)}});
        }else if(action=="editor")return showView(args);
        else if(action=="describe"){Paused pause(*this);ml::writeJson(root.getChildFile("descriptor.json"),ml::pluginSnapshot(*plugin));}
        else if(action=="snapshot")snapshot();
        else if(action=="capture_start"){recordFrames.store(0);energy.store(0);nonzero.store(0);record.store(true);}
        else if(action=="capture_stop"){
            record.store(false);Paused pause(*this);
            auto stream=root.getChildFile("capture.wav").createOutputStream();WavAudioFormat format;
            auto writer=std::unique_ptr<AudioFormatWriter>(format.createWriterFor(stream.get(),rate,2,24,{},0));
            ml::require(writer!=nullptr,"Cannot create audition capture");stream.release();
            writer->writeFromAudioSampleBuffer(recording,0,jmin(recordFrames.load(),recording.getNumSamples()));writer.reset();
        }else if(action=="close"){releaseKeys();snapshot();stopping=true;}
        else throw std::runtime_error("Unknown instrument studio action");
        return state();
    }
public:
    explicit Studio(const File& file):root(file.getParentDirectory()),config(ml::parse(file)),token(config["token"].toString()){
        controllers.fill(-1);
        lastContact=Time::getMillisecondCounterHiRes();
        ml::require(token.length()>=32,"Missing instrument session token");
        ml::writeJson(root.getChildFile("status.json"),ml::object({{"status","loading"},{"pid",(int)GetCurrentProcessId()},{"session_id",config["session_id"]}}));
        ml::require(device.initialise(0,2,nullptr,true).isEmpty(),"Could not open the audition audio device");
        if(auto* output=device.getCurrentAudioDevice()){rate=output->getCurrentSampleRate();block=output->getCurrentBufferSizeSamples();}
        plugin=ml::load(config["plugin"],rate,block);plugin->setNonRealtime(false);editorAvailable=plugin->hasEditor();
        plugin->setRateAndBufferSizeDetails(rate,block);plugin->prepareToPlay(rate,block);
        work.setSize(jmax(2,jmax(plugin->getTotalNumInputChannels(),plugin->getTotalNumOutputChannels())),jmax(4096,block));
        midi.ensureSize(32768);recording.setSize(2,(int)(rate*12));recording.clear();
        if(auto* saved=config["controllers"].getDynamicObject())for(auto item:saved->getProperties()){
            const int number=item.name.toString().getIntValue(),value=(int)item.value;
            ml::require((number==1||number==7||number==10||number==11)&&value>=0&&value<=127,"Invalid retained controller");
            controllers[(size_t)number]=value;enqueue(MidiMessage::controllerEvent(1,number,value));
        }
        plugin->addListener(this);device.addAudioCallback(this);
        // Same version-bound sample-worker settling rule as the existing renderer.
        // Keep the published status loading during this phase, including restores.
        if(ml::needsKeyscapeAuxiliaryOutputs(plugin->getPluginDescription()))MessageManager::getInstance()->runDispatchLoopUntil(10000);
        snapshot();publish();
    }
    ~Studio()override{
        closeView();editor.reset();device.removeAudioCallback(this);device.closeAudioDevice();
        if(plugin){plugin->removeListener(this);plugin->releaseResources();}
    }
    bool done()const{return stopping;}
    void publish(){ml::writeJson(root.getChildFile("status.json"),state());lastStatus=Time::getMillisecondCounterHiRes();}
    void tick(){
        const auto now=Time::getMillisecondCounterHiRes();
        auto input=root.getChildFile("command.json");
        if(input.existsAsFile()){
            try{
                const auto request=ml::parse(input);const auto seq=(uint64_t)(int64)request["sequence"];
                ml::require(request["token"].toString()==token,"Instrument session token mismatch");
                if(seq>commandSequence){
                    commandSequence=seq;lastContact=now;
                    try{auto result=command(request);publish();ml::writeJson(root.getChildFile("reply.json"),ml::object({{"sequence",(int64)seq},{"ok",true},{"result",result}}));}
                    catch(const std::exception& e){ml::writeJson(root.getChildFile("reply.json"),ml::object({{"sequence",(int64)seq},{"ok",false},{"error",String(e.what())}}));}
                }
            }catch(const std::exception&){/* A partial or foreign request never changes the running instance. */}
        }
        if(embedded&&parent&&!IsWindow(parent))closeView();
        if(now-lastContact>45000){releaseKeys();snapshot();stopping=true;}
        if(!held.load()&&!sustainHeld&&now-lastSnapshot>2500&&now-lastChange>300
            &&(changes.load()!=snapshotChanges||(now-lastSnapshot>15000&&peak.load()<1e-5f)))snapshot();
        if(now-lastStatus>200)publish();
    }
    void audioProcessorParameterChanged(AudioProcessor*,int,float)override{changes.fetch_add(1);}
    void audioProcessorChanged(AudioProcessor*,const ChangeDetails&)override{changes.fetch_add(1);}
    void audioDeviceAboutToStart(AudioIODevice*)override{}
    void audioDeviceStopped()override{}
    void audioDeviceIOCallbackWithContext(const float*const*,int,float*const* outputs,int channels,int count,const AudioIODeviceCallbackContext&)override{
        for(int ch=0;ch<channels;++ch)if(outputs[ch])FloatVectorOperations::clear(outputs[ch],count);
        if(suspended.load()){ack.store(true);return;}
        if(count>work.getNumSamples())return;
        work.clear();midi.clear();
        if(panic.exchange(false)){midi.addEvent(MidiMessage::allSoundOff(1),0);midi.addEvent(MidiMessage::allNotesOff(1),0);}
        int a,n,b,m;fifo.prepareToRead(fifo.getNumReady(),a,n,b,m);
        for(int i=0;i<n;++i)midi.addEvent(messages[(size_t)(a+i)],0);
        for(int i=0;i<m;++i)midi.addEvent(messages[(size_t)(b+i)],0);fifo.finishedRead(n+m);
        AudioBuffer<float> buffer(work.getArrayOfWritePointers(),work.getNumChannels(),count);
        plugin->processBlock(buffer,midi);
        double sum=0;float maximum=0;int64 active=0;const float gain=volume.load();
        const int recordingAt=recordFrames.load();
        for(int ch=0;ch<jmin(2,channels);++ch)if(outputs[ch])for(int i=0;i<count;++i){
            const float raw=work.getSample(ch,i)*gain,value=std::isfinite(raw)?jlimit(-.98f,.98f,raw):0;
            outputs[ch][i]=value;sum+=(double)value*value;maximum=jmax(maximum,std::abs(value));if(value!=0)++active;
            if(record.load()&&recordingAt+i<recording.getNumSamples())recording.setSample(ch,recordingAt+i,value);
        }
        if(record.load())recordFrames.fetch_add(count);
        energy.fetch_add(sum);nonzero.fetch_add(active);peak.store(maximum);samples.fetch_add(count);callbacks.fetch_add(1);
    }
};

int main(int argc,char** argv){
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    _set_abort_behavior(0,_WRITE_ABORT_MSG|_CALL_REPORTFAULT);
    ScopedJuceInitialiser_GUI gui;
    if(argc!=2)return 2;
    const File config(String::fromUTF8(argv[1]));
    try{
        Studio studio(config);
        while(!studio.done()){MessageManager::getInstance()->runDispatchLoopUntil(10);studio.tick();}
        return 0;
    }catch(const std::exception& e){
        ml::writeJson(config.getSiblingFile("status.json"),ml::object({{"status","failed"},{"error",String(e.what())},{"pid",(int)GetCurrentProcessId()}}));
        return 1;
    }
}
