// Exercise the actual engine lifecycle with a real WASAPI endpoint. Calling
// audioDeviceStopped below simulates its callback; it does not unplug hardware.
#define main ml_engine_program_main
#include "Main.cpp"
#undef main

int main(int argc,char** argv){
    if(argc!=2)return 2;
    juce::ScopedJuceInitialiser_GUI gui;
    auto* report=new juce::DynamicObject();juce::var result(report);
    try{
        mlengine::AudioScheduling scheduling;
        if(!scheduling.active())throw std::runtime_error("Windows Pro Audio thread registration failed");
        report->setProperty("pro_audio_thread_registration",true);
        DeviceEngine engine;
        const auto configured=engine.command(obj({{"action","configure"},{"sample_rate",48000},{"buffer_frames",512}}));
        engine.command(obj({{"action","probe"},{"seconds",.5}}));juce::Thread::sleep(80);
        const auto before=engine.status();
        if((double)before["peak"]<=0)throw std::runtime_error("Real device did not consume probe audio");
        engine.audioDeviceStopped();
        const auto stopped=engine.status();
        if(stopped["status"].toString()!="stopped"||(bool)stopped["playing"])throw std::runtime_error("Device stop retained a false playback state");
        bool invalidRejected=false;
        try{engine.command(obj({{"action","configure"},{"device","__missing_native_probe_device__"}}));}catch(const std::exception&){invalidRejected=true;}
        if(!invalidRejected)throw std::runtime_error("Missing output device silently selected another device");
        const auto recovered=engine.command(obj({{"action","configure"},{"device",configured["device"]},{"sample_rate",48000},{"buffer_frames",512}}));
        engine.command(obj({{"action","probe"},{"seconds",.2}}));juce::Thread::sleep(80);
        const auto after=engine.status();
        if(after["status"].toString()!="ready"||(double)after["peak"]<=0)throw std::runtime_error("Reconfigured device did not resume real callbacks");
        report->setProperty("ok",true);report->setProperty("configured",configured);report->setProperty("before",before);
        report->setProperty("simulated_stop",stopped);report->setProperty("invalid_device_rejected",invalidRejected);
        report->setProperty("recovered",recovered);report->setProperty("after",after);
        report->setProperty("scope","Real WASAPI before/after; simulated stop callback and real missing-device setup failure; no hardware/default-device changes");
        engine.command(obj({{"action","close"}}));
    }catch(const std::exception& e){report->setProperty("ok",false);report->setProperty("error",juce::String(e.what()));}
    juce::File(juce::String::fromUTF8(argv[1])).replaceWithText(juce::JSON::toString(result,false));
    return (bool)result["ok"]?0:1;
}
