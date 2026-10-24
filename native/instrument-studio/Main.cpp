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
    float viewScale=1;bool legacyDpi=false,nativeZoomSupported=true;
    var timings=ml::object({});
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
            {"editor_scale",viewScale},{"editor_legacy_dpi",legacyDpi},{"editor_native_zoom_supported",nativeZoomSupported},{"native_timings_ms",timings},
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
                RECT box{};GetWindowRect(window,&box);
                result.getDynamicObject()->setProperty("editor_pixels_width",(int)(box.right-box.left));result.getDynamicObject()->setProperty("editor_pixels_height",(int)(box.bottom-box.top));
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
    var showView(const var& args,double maximumPlatformScale=4.0){
        const auto began=Time::getMillisecondCounterHiRes();
        const bool visible=(bool)args.getProperty("visible",true);
        if(!visible){closeView();return state();}
        if(!editor)editor.reset(plugin->createEditorAndMakeActive());
        ml::require(editor!=nullptr,"This plugin does not provide a native editor");
        const auto handle=args.getProperty("parent_handle","").toString().getHexValue64();
        if(handle){
            auto host=(HWND)(intptr_t)handle;DWORD pid=0;
            ml::require(IsWindow(host)&&GetWindowThreadProcessId(host,&pid)&&pid==(DWORD)(int)args["parent_pid"],"Native editor parent identity changed");
            if(!embedded||parent!=host){
                closeView();parent=host;
                if(legacyDpi){ScopedDPIAwarenessDisabler compatibility;editor->addToDesktop(0,host);}
                else editor->addToDesktop(0,host);
                embedded=true;editor->setVisible(true);
                // Avoid fractional platform DPI selecting a missing legacy
                // raster tier. Only this plugin wrapper uses an integer tier;
                // the desktop/WebView keep their real per-monitor DPI.
                if(!legacyDpi){
                    RECT slot{};GetClientRect(host,&slot);
                    const double available=jmin(slot.right/(double)jmax(1,editor->getWidth()),slot.bottom/(double)jmax(1,editor->getHeight()));
                    const double wrapper=jmax(1.0,std::floor(jmin(maximumPlatformScale,jmin((double)args.getProperty("scale",1.0),available))));
                    editor->getPeer()->setCustomPlatformScaleFactor(wrapper);
                }
                // JUCE delivers the peer's DPI notification asynchronously.
                // Let it arrive before selecting content scale or size constraints.
                MessageManager::getInstance()->runDispatchLoopUntil(60);
            }
            RECT bounds{};GetClientRect(host,&bounds);
            const double scale=(double)args.getProperty("scale",1.0);
            ml::require(std::isfinite(scale)&&scale>=.5&&scale<=4,"Invalid editor scale");
            // The plugin's own size constraints and native DPI choose a supported
            // layout. Arbitrary zoom factors can select nonexistent raster assets.
            const double zoom=(double)args.getProperty("zoom",0.0);
            ml::require(std::isfinite(zoom)&&zoom>=0&&zoom<=4,"Invalid editor zoom");
            if(legacyDpi&&zoom==1){closeView();editor.reset();legacyDpi=false;return showView(args,maximumPlatformScale);}
            if(!legacyDpi&&viewScale!=1){viewScale=1;editor->setScaleFactor(1);}
            if(!legacyDpi){
                const double available=jmin(bounds.right/(double)jmax(1,editor->getWidth()),bounds.bottom/(double)jmax(1,editor->getHeight()));
                const double wrapper=jmax(1.0,std::floor(jmin(maximumPlatformScale,jmin(scale,available))));
                if(std::abs(editor->getPeer()->getPlatformScaleFactor()-wrapper)>.001){
                    editor->getPeer()->setCustomPlatformScaleFactor(wrapper);
                    MessageManager::getInstance()->runDispatchLoopUntil(60);
                }
            }
            if(!legacyDpi&&editor->isResizable())editor->setBoundsConstrained(juce::Rectangle<int>(0,0,jmax(1,(int)bounds.right),jmax(1,(int)bounds.bottom)));
            editor->setTopLeftPosition(0,0);editor->setVisible(true);
            if(auto* peer=editor->getPeer()){
                auto window=(HWND)peer->getNativeHandle();
                RECT actual{};GetWindowRect(window,&actual);
                const double fit=jmin(bounds.right/(double)jmax(1L,actual.right-actual.left),bounds.bottom/(double)jmax(1L,actual.bottom-actual.top));
                const int originalWidth=actual.right-actual.left,originalHeight=actual.bottom-actual.top;
                if(!legacyDpi&&(std::abs(fit-1.0)>.001||zoom>0)){
                    // Never request a fractional raster scale below 100%.
                    // The plugin itself renders the selected native 1x/2x tier.
                    const double effective=jmax(1.0,std::floor(jmin(fit,zoom>0?zoom:fit)*20)/20);
                    for(const double tier:{effective,4.0,3.0,2.0,1.0}){
                        if(tier>effective)continue;
                        viewScale=(float)tier;editor->setScaleFactor(viewScale);
                        MessageManager::getInstance()->runDispatchLoopUntil(10);
                        GetWindowRect(window,&actual);
                        const int w=actual.right-actual.left,h=actual.bottom-actual.top;
                        if(w<=bounds.right&&h<=bounds.bottom&&(w>originalWidth||h>originalHeight||fit<=1.0))break;
                    }
                }
                // Some older editors ignore the VST3 content-scale interface.
                // Windows mixed-DPI hosting scales their actual native windows,
                // retaining native mouse/keyboard controls (no screenshot proxy).
                if(!legacyDpi&&zoom!=1&&fit>=scale&&scale>1.0
                    &&actual.right-actual.left==originalWidth&&actual.bottom-actual.top==originalHeight
                    &&GetWindowDpiHostingBehavior(host)==DPI_HOSTING_BEHAVIOR_MIXED){
                    closeView();editor.reset();
                    {
                        ScopedDPIAwarenessDisabler compatibility;
                        editor.reset(plugin->createEditorAndMakeActive());
                        ml::require(editor!=nullptr,"Cannot reopen legacy plugin editor");
                        parent=host;editor->addToDesktop(0,host);embedded=true;editor->setVisible(true);
                    }
                    legacyDpi=true;nativeZoomSupported=false;viewScale=(float)scale;
                    MessageManager::getInstance()->runDispatchLoopUntil(60);
                    window=(HWND)editor->getPeer()->getNativeHandle();GetWindowRect(window,&actual);
                }
                if(legacyDpi&&(actual.right-actual.left>bounds.right||actual.bottom-actual.top>bounds.bottom)){
                    closeView();editor.reset();legacyDpi=false;
                    auto smaller=args.clone();smaller.getDynamicObject()->setProperty("zoom",1.0);return showView(smaller);
                }
                // An editor can restore its own saved size while receiving DPI
                // notifications. If the actual window still overflows, retry a
                // smaller integer platform tier, never a sub-100% raster scale.
                // The cap decreases on each retry, so it cannot oscillate back
                // to the oversized tier or wait indefinitely for a vendor UI.
                if(!legacyDpi&&zoom==0&&(actual.right-actual.left>bounds.right||actual.bottom-actual.top>bounds.bottom)){
                    const double current=peer->getPlatformScaleFactor();
                    if(current>1.0)return showView(args,jmax(1.0,std::floor(current)-1.0));
                }
                const int width=actual.right-actual.left,height=actual.bottom-actual.top;
                SetWindowPos(window,HWND_TOP,jmax(0,((int)bounds.right-width)/2),jmax(0,((int)bounds.bottom-height)/2),0,0,SWP_NOSIZE|SWP_NOACTIVATE|SWP_SHOWWINDOW);
                ml::require(GetParent(window)==host,"Plugin editor could not attach to the application");
            }
        }else{
            closeView();
            if(legacyDpi){editor.reset();legacyDpi=false;viewScale=1;editor.reset(plugin->createEditorAndMakeActive());ml::require(editor!=nullptr,"Cannot reopen plugin editor");}
            class Window final:public DocumentWindow{public:Window():DocumentWindow("Instrument",Colour(0xff171719),DocumentWindow::allButtons){}void closeButtonPressed()override{setVisible(false);}};
            floating=std::make_unique<Window>();floating->setUsingNativeTitleBar(true);
            floating->setContentNonOwned(editor.get(),true);floating->setResizable(editor->isResizable(),false);
            floating->centreWithSize(editor->getWidth(),editor->getHeight());floating->setVisible(true);editor->setVisible(true);
        }
        timings.getDynamicObject()->setProperty("editor",Time::getMillisecondCounterHiRes()-began);
        return state();
    }
    void loadPreset(){
        const auto began=Time::getMillisecondCounterHiRes();
        auto next=ml::parse(root.getChildFile("selection.json"));
        ml::require(next["token"].toString()==token&&next["session_id"]==config["session_id"],"Preset selection session changed");
        const auto spec=next["plugin"];
        for(const auto* key:{"path","identifier","binary_sha256","version"})
            ml::require(spec[key]==config["plugin"][key],"Preset belongs to another plugin instance");
        std::array<int,128> retained;retained.fill(-1);
        if(auto* saved=next["controllers"].getDynamicObject())for(auto item:saved->getProperties()){
            const int number=item.name.toString().getIntValue(),value=(int)item.value;
            ml::require((number==1||number==7||number==10||number==11)&&value>=0&&value<=127,"Invalid retained controller");
            retained[(size_t)number]=value;
        }
        // Only the complete-state and VST3-preset routes used by this library
        // can reuse an instance. Other formats keep their existing cold loader.
        MemoryBlock desired;
        const auto encoded=spec["state_base64"].toString(),presetPath=spec["preset_path"].toString();
        if(encoded.isNotEmpty()){
            MemoryOutputStream output(desired,false);
            ml::require(Base64::convertFromBase64(output,encoded),"Invalid plugin state encoding");
            ml::require(spec.getProperty("state_encoding","rfc4648-juce-vst3-state").toString()=="rfc4648-juce-vst3-state","Unsupported state encoding");
            const auto sha=spec["state_sha256"].toString();
            ml::require(sha.isEmpty()||SHA256(desired).toHexString()==sha,"Plugin state digest mismatch");
            const auto xml=AudioProcessor::getXmlFromBinary(desired.getData(),(int)desired.getSize());
            ml::require(xml&&xml->hasTagName("VST3PluginState")&&xml->getChildByName("IComponent"),"Invalid complete VST3 state");
        }else ml::require(presetPath.isNotEmpty()&&File(presetPath).loadFileAsData(desired),"Cannot read VST3 preset");
        releaseKeys();
        const auto previousControllers=controllers;
        {
            Paused pause(*this);MemoryBlock previous;plugin->getStateInformation(previous);
            try{
                if(encoded.isNotEmpty())plugin->setStateInformation(desired.getData(),(int)desired.getSize());
                else{auto* client=plugin->getVST3Client();ml::require(client&&client->setPreset(desired),"VST3 preset rejected");}
                if(auto* overrides=spec["parameters"].getDynamicObject())for(auto item:overrides->getProperties()){
                    auto* p=ml::parameter(*plugin,item.name.toString());const float value=(float)item.value;
                    ml::require(std::isfinite(value)&&value>=0&&value<=1,"Invalid parameter value");
                    ml::require(p->isAutomatable()||std::abs(p->getValue()-value)<=1e-5f,"Parameter is not writable");
                    p->beginChangeGesture();p->setValueNotifyingHost(value);p->endChangeGesture();
                    ml::require(std::abs(p->getValue()-value)<1e-4f,"Plugin did not retain parameter");
                }
                ml::require(ml::selectStereoMainOutput(*plugin,AudioChannelSet::stereo(),ml::needsKeyscapeAuxiliaryOutputs(plugin->getPluginDescription())),"Could not retain stereo output after preset change");
                work.setSize(jmax(2,jmax(plugin->getTotalNumInputChannels(),plugin->getTotalNumOutputChannels())),jmax(4096,block));
            }catch(...){plugin->setStateInformation(previous.getData(),(int)previous.getSize());throw;}
            controllers=retained;
            for(int i:{1,7,10,11}){
                const int reset=i==7?100:i==10?64:i==11?127:0;
                if(retained[(size_t)i]>=0||previousControllers[(size_t)i]>=0)
                    enqueue(MidiMessage::controllerEvent(1,i,retained[(size_t)i]>=0?retained[(size_t)i]:reset));
            }
            config=next;stateHash.clear();parameterHash.clear();changes.fetch_add(1);
        }
        snapshot();timings.getDynamicObject()->setProperty("preset_apply",Time::getMillisecondCounterHiRes()-began);
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
        else if(action=="load_preset")loadPreset();
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
        const auto began=Time::getMillisecondCounterHiRes();
        controllers.fill(-1);
        lastContact=Time::getMillisecondCounterHiRes();
        ml::require(token.length()>=32,"Missing instrument session token");
        ml::writeJson(root.getChildFile("status.json"),ml::object({{"status","loading"},{"pid",(int)GetCurrentProcessId()},{"session_id",config["session_id"]}}));
        ml::require(device.initialise(0,2,nullptr,true).isEmpty(),"Could not open the audition audio device");
        timings.getDynamicObject()->setProperty("device",Time::getMillisecondCounterHiRes()-began);
        if(auto* output=device.getCurrentAudioDevice()){rate=output->getCurrentSampleRate();block=output->getCurrentBufferSizeSamples();}
        const auto loading=Time::getMillisecondCounterHiRes();
        auto spec=config["plugin"].clone();
        // This private mailbox is prepared by InstrumentStudio.validate(), which
        // already verified this exact binary. Do not hash it a second time here.
        // Direct native fixtures without that receipt still use the cold loader.
        if(config["binary_verified"].isObject()){
            const auto receipt=config["binary_verified"];File binary(spec["path"].toString());
            if(binary.isDirectory()){
                const auto files=binary.getChildFile("Contents/x86_64-win").findChildFiles(File::findFiles,false,"*.vst3");
                ml::require(files.size()==1,"Ambiguous VST3 binary");binary=files[0];
            }
            ml::require(binary==File(receipt["path"].toString())&&binary.existsAsFile()
                &&receipt["sha256"]==spec["binary_sha256"]&&binary.getSize()==(int64)receipt["bytes"]
                &&binary.getLastModificationTime().toMilliseconds()==(int64)receipt["mtime_ms"],"Verified plugin binary changed before launch");
            spec.getDynamicObject()->removeProperty("binary_sha256");
        }
        plugin=ml::load(spec,rate,block);plugin->setNonRealtime(false);editorAvailable=plugin->hasEditor();
        timings.getDynamicObject()->setProperty("plugin_load",Time::getMillisecondCounterHiRes()-loading);
        plugin->setRateAndBufferSizeDetails(rate,block);plugin->prepareToPlay(rate,block);
        work.setSize(jmax(2,jmax(plugin->getTotalNumInputChannels(),plugin->getTotalNumOutputChannels())),jmax(4096,block));
        midi.ensureSize(32768);recording.setSize(2,(int)(rate*12));recording.clear();
        if(auto* saved=config["controllers"].getDynamicObject())for(auto item:saved->getProperties()){
            const int number=item.name.toString().getIntValue(),value=(int)item.value;
            ml::require((number==1||number==7||number==10||number==11)&&value>=0&&value<=127,"Invalid retained controller");
            controllers[(size_t)number]=value;enqueue(MidiMessage::controllerEvent(1,number,value));
        }
        plugin->addListener(this);device.addAudioCallback(this);
        // Interactive hosts accept input as soon as the processor is prepared.
        // Sample streaming remains the plugin's job; an offline render's fixed
        // worker-settling delay must not gate the editor or live MIDI input.
        snapshot();timings.getDynamicObject()->setProperty("startup",Time::getMillisecondCounterHiRes()-began);publish();
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
