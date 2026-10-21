#include <juce_audio_devices/juce_audio_devices.h>
#include <array>
#include <vector>
#include <stdexcept>
#include "NodeSource.h"

// Independent deterministic node acceptance driver. It never opens a device or
// a project database. Programs are already compiled into an isolated workspace.
int main(int argc,char** argv) {
    using namespace juce;
    ScopedJuceInitialiser_GUI gui;
    if(argc!=3)return 2;
    const File input(String::fromUTF8(argv[1])),directory(String::fromUTF8(argv[2]));
    directory.createDirectory();
    auto* report=new DynamicObject();var result(report);
    try {
        const auto request=JSON::parse(input.loadFileAsString());
        const auto rate=(double)request.getProperty("sample_rate",48000);
        const int frames=(int)request.getProperty("block_size",512),slots=(int)request["plugins"];
        mlengine::NodeSource node(File(request["program"].toString()),rate,frames,slots);
        node.waitReady();
        AudioBuffer<float> main(2,frames),output(2,frames);std::vector<AudioBuffer<float>> sides;
        for(int i=0;i<slots;++i)sides.emplace_back(2,frames);
        Array<var> checks;
        FileOutputStream pcm(directory.getChildFile("output.f32"));
        if(!pcm.openedOk())throw std::runtime_error("Cannot open probe output");
        const auto initial=node.status();
        for(const auto& item:*request["requests"].getArray()) {
            if(item.hasProperty("program")){node.configure(File(item["program"].toString()));node.waitReady();}
            const auto count=(int)item.getProperty("frames",frames);
            for(int ch=0;ch<2;++ch){
                FloatVectorOperations::fill(main.getWritePointer(ch),(float)(double)item.getProperty("main",0.0),count);
                for(auto& side:sides)FloatVectorOperations::fill(side.getWritePointer(ch),(float)(double)item.getProperty("side",0.0),count);
            }
            node.submit((int64)item["sample"],(int64)item.getProperty("input_delay",(int64)0),(int64)item.getProperty("origin",(int64)0),
                (bool)item.getProperty("chase",false),(bool)item.getProperty("loop",false),
                (int64)item.getProperty("loop_start",(int64)0),(int64)item.getProperty("loop_end",(int64)1),main,sides,count);
            bool overlapRejected=false;
            try{node.submit(0,0,0,false,false,0,1,main,sides,count);}catch(const std::exception&){overlapRejected=true;}
            const auto deadline=Time::getMillisecondCounterHiRes()+2200;
            while(!node.receive(output,count,1))if(Time::getMillisecondCounterHiRes()>deadline)throw std::runtime_error("Probe output timeout");
            for(int i=0;i<count;++i)for(int ch=0;ch<2;++ch)pcm.writeFloat(output.getSample(ch,i));
            auto* check=new DynamicObject();check->setProperty("sample",item["sample"]);check->setProperty("frames",count);
            check->setProperty("overlap_rejected",overlapRejected);check->setProperty("status",node.status());checks.add(var(check));
        }
        report->setProperty("initial",initial);report->setProperty("checks",checks);report->setProperty("ok",true);
        pcm.flush();
    }catch(const std::exception& error){report->setProperty("ok",false);report->setProperty("error",String(error.what()));}
    directory.getChildFile("report.json").replaceWithText(JSON::toString(result,false));
    return (bool)result["ok"]?0:1;
}
