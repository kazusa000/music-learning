#include <juce_audio_devices/juce_audio_devices.h>
#include <array>
#include <vector>
#include <stdexcept>
#include "GraphKernel.h"
#include "GraphSession.h"

// Captures the exact background-producer/device-consumer path without opening
// a physical device. Device/loopback acceptance is measured separately.
static int capture(const juce::File& manifestFile,const juce::File& target,const juce::File& reportFile){
    using namespace juce;auto* report=new DynamicObject();var result(report);
    try{
        if(target.exists())throw std::runtime_error("Choose a new capture output path");
        const auto manifest=JSON::parse(manifestFile.loadFileAsString());const double rate=(double)manifest["sample_rate"];
        const auto frames=(int64)manifest["duration_samples"]+(int64)manifest.getProperty("tail_samples",0);
        mlengine::GraphSession session(manifest,rate,512,0,false,0,(double)frames);
        std::unique_ptr<OutputStream> stream=target.createOutputStream();WavAudioFormat format;
        auto writer=format.createWriterFor(stream,AudioFormatWriterOptions{}.withSampleRate(rate).withNumChannels(2)
            .withBitsPerSample(32).withSampleFormat(AudioFormatWriterOptions::SampleFormat::floatingPoint));
        if(!writer)throw std::runtime_error("Cannot create native producer capture");
        AudioBuffer<float> block(2,512);int64 produced=0;const auto deadline=Time::getMillisecondCounterHiRes()+60000;
        while(produced<frames){
            if(session.isFailed())throw std::runtime_error(session.failure().toStdString());
            if(Time::getMillisecondCounterHiRes()>deadline)throw std::runtime_error("Native producer capture timed out");
            if(!session.available()){Thread::sleep(1);continue;}
            block.clear();float peak=0;session.consume(block.getArrayOfWritePointers(),2,512,peak);
            const int count=(int)jmin((int64)512,frames-produced);
            if(!writer->writeFromAudioSampleBuffer(block,0,count))throw std::runtime_error("Native capture write failed");produced+=count;
        }
        writer.reset();bool ready=false;report->setProperty("plugins",session.pluginValues(ready));
        if(!ready)throw std::runtime_error("Captured graph has an incomplete plugin state");
        report->setProperty("ok",true);report->setProperty("frames",produced);report->setProperty("underruns",(int64)session.misses());
        report->setProperty("audio_buffer_bytes",(int64)session.bytes());report->setProperty("scope","Native producer/consumer capture; no physical audio device");
    }catch(const std::exception& e){report->setProperty("ok",false);report->setProperty("error",String(e.what()));}
    reportFile.replaceWithText(JSON::toString(result,false));return (bool)result["ok"]?0:1;
}

int main(int argc,char** argv){
    using namespace juce;
    ScopedJuceInitialiser_GUI gui;
    if(argc!=5||(String(argv[1])!="--render"&&String(argv[1])!="--capture"))return 2;
    const File manifestFile(String::fromUTF8(argv[2])),target(String::fromUTF8(argv[3])),reportFile(String::fromUTF8(argv[4]));
    if(String(argv[1])=="--capture")return capture(manifestFile,target,reportFile);
    auto* report=new DynamicObject();var result(report);
    File partial=target.getSiblingFile(target.getFileName()+"."+Uuid().toString()+".partial");
    try{
        if(target.exists())throw std::runtime_error("Choose a new export output path");
        const auto manifest=JSON::parse(manifestFile.loadFileAsString());
        const auto rate=(double)manifest["sample_rate"];
        const auto origin=(int64)manifest.getProperty("start_sample",(int64)0);
        const auto frames=(int64)manifest.getProperty("end_sample",manifest["duration_samples"])-origin+(int64)manifest.getProperty("tail_samples",0);
        if(frames<=0)throw std::runtime_error("Empty native export range");
        mlengine::GraphKernel kernel(manifest,origin,false,0,1,true);
        target.getParentDirectory().createDirectory();
        std::unique_ptr<OutputStream> stream=partial.createOutputStream();
        WavAudioFormat format;
        auto writer=format.createWriterFor(stream,AudioFormatWriterOptions{}.withSampleRate(rate).withNumChannels(2)
            .withBitsPerSample(32).withSampleFormat(AudioFormatWriterOptions::SampleFormat::floatingPoint));
        if(!writer)throw std::runtime_error("Cannot open native export output");
        AudioBuffer<float> block(2,kernel.quantum());int64 produced=0;float peak=0;double square=0;
        while(produced<frames){
            const int count=(int)jmin((int64)kernel.quantum(),frames-produced);
            // Pre-roll in full or short blocks; removing latency is a common
            // operation for offline and device playback.
            const int requested=(int)jmin((int64)kernel.quantum(),frames-produced+(int64)kernel.latency());
            kernel.process(block,requested);const int skip=kernel.consumeDiscard(requested);
            const int available=jmin(count,requested-skip);if(available==0)continue;
            if(!writer->writeFromAudioSampleBuffer(block,skip,available))throw std::runtime_error("Native export write failed");
            for(int ch=0;ch<2;++ch)for(int i=skip;i<skip+available;++i){const auto x=block.getSample(ch,i);peak=jmax(peak,std::abs(x));square+=x*x;}
            produced+=available;
        }
        writer.reset();
        bool ready=false;report->setProperty("plugins",kernel.pluginValues(ready));
        if(!ready)throw std::runtime_error("Export graph did not finish in a valid state");
        if(target.exists()||!partial.moveFileTo(target))throw std::runtime_error("Cannot publish native export");
        report->setProperty("ok",true);report->setProperty("frames",produced);report->setProperty("sample_rate",rate);
        report->setProperty("latency_samples",kernel.latency());report->setProperty("audio_buffer_bytes",(int64)kernel.bytes());
        report->setProperty("peak",peak);report->setProperty("rms",std::sqrt(square/(double)(produced*2)));
        report->setProperty("revision_id",manifest["revision_id"]);report->setProperty("mixing_version",manifest["mixing_version"]);
        report->setProperty("sha256",SHA256(target).toHexString());
    }catch(const std::exception& error){report->setProperty("ok",false);report->setProperty("error",String(error.what()));}
    reportFile.replaceWithText(JSON::toString(result,false));
    return (bool)result["ok"]?0:1;
}
