#define NOMINMAX
#include "VerifiedFormat.h"
// Reuse the verified state/resource/VST3 loader without modifying its source
// or replacing any running verification or legacy executable.
#define main ml_offline_main
#define VST3PluginFormatHeadless ml::WorkerVST3Format
#define FileInputStream ml::WorkerFileInputStream
#include "../audio-host/Main.cpp"
#undef FileInputStream
#undef VST3PluginFormatHeadless
#undef main
#include "../audio-host/Render.cpp"
#include "../engine/SharedPlugin.h"
#include "VerifiedPlugin.h"
#include "TrackRenderer.h"
#include <cstdio>
#include "NodeWorker.h"

int main(int argc,char** argv) {
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    _set_abort_behavior(0,_WRITE_ABORT_MSG|_CALL_REPORTFAULT);
    if(argc==4&&juce::String(argv[1])=="--node") {
        juce::ScopedJuceInitialiser_GUI gui;
        return ml::runNodeWorker(juce::String(argv[2]),juce::File(juce::String(argv[3])));
    }
    if(argc!=3||!juce::String(argv[1]).startsWith("Local\\ml-plugin-"))return 2;
    auto mapping=OpenFileMappingW(FILE_MAP_ALL_ACCESS,FALSE,juce::String(argv[1]).toWideCharPointer());
    if(!mapping)return 3;
    auto* shared=static_cast<mlengine::PluginRing*>(MapViewOfFile(mapping,FILE_MAP_ALL_ACCESS,0,0,sizeof(mlengine::PluginRing)));
    if(!shared){CloseHandle(mapping);return 4;}
    try {
        juce::ScopedJuceInitialiser_GUI gui;
        ml::require(shared->protocol==mlengine::pluginProtocol&&shared->frames>0&&shared->frames<=mlengine::pluginMaxFrames,"Plugin mapping protocol mismatch");
        const juce::File directory{juce::String(argv[2])};
        std::unique_ptr<ml::TrackRenderer> renderer;
        std::unique_ptr<mlengine::AudioScheduling> scheduling;
        std::unique_ptr<juce::ResamplingAudioSource> resampler;
        juce::String loadedHash;
        uint64_t epoch=0,produced=0;
        juce::AudioBuffer<float> output(2,(int)shared->frames);
        while(!shared->quit.load()) {
            const auto requested=shared->requestedEpoch.load(std::memory_order_acquire);
            if(requested!=epoch) {
                std::array<uint64_t,4> words;
                for(size_t i=0;i<words.size();++i)words[i]=shared->configHash[i].load();
                if(shared->requestedEpoch.load(std::memory_order_acquire)!=requested)continue;
                const auto hash=juce::String::toHexString(words.data(),32,0);
                if(hash!=loadedHash) {
                    const auto file=directory.getChildFile(hash+".json");
                    ml::require(file.existsAsFile()&&juce::SHA256(file).toHexString()==hash,"Plugin project manifest identity mismatch");
                    const auto project=juce::JSON::parse(file.loadFileAsString());
                    ml::require(project.isObject()&&ml::array(project,"tracks").size()==1,"Plugin worker needs one compiled track");
                    if(!renderer) {
                        const auto loading=juce::Time::getHighResolutionTicks();
                        renderer=std::make_unique<ml::TrackRenderer>(project,shared);
                        scheduling=std::make_unique<mlengine::AudioScheduling>();
                        shared->loadMicros.store((uint64_t)(juce::Time::highResolutionTicksToSeconds(juce::Time::getHighResolutionTicks()-loading)*1000000));
                        resampler=std::make_unique<juce::ResamplingAudioSource>(renderer.get(),false,2);
                        resampler->setResamplingRatio(shared->sourceRate/shared->deviceRate);
                        resampler->prepareToPlay((int)shared->frames,shared->deviceRate);
                    }else renderer->update(project);
                    loadedHash=hash;
                }
                epoch=requested;produced=shared->consumed.load();
                const auto source=(double)shared->origin.load()+(double)(produced*shared->frames)*shared->sourceRate/shared->deviceRate;
                const auto begin=shared->loopStart.load(),end=shared->loopEnd.load();const bool loop=shared->looping.load()!=0;
                const auto sample=loop&&source>=end?(double)begin+std::fmod(source-begin,(double)(end-begin)):source;
                renderer->seek((juce::int64)sample,loop,begin,end);resampler->flushBuffers();
                shared->produced.store(produced);shared->readyEpoch.store(epoch,std::memory_order_release);
            }
            const auto consumed=shared->consumed.load(std::memory_order_acquire);
            if(produced<consumed) {
                produced=consumed;
                const auto source=(double)shared->origin.load()+(double)(produced*shared->frames)*shared->sourceRate/shared->deviceRate;
                const auto begin=shared->loopStart.load(),end=shared->loopEnd.load();const bool loop=shared->looping.load()!=0;
                renderer->seek((juce::int64)(loop&&source>=end?begin+std::fmod(source-begin,(double)(end-begin)):source),loop,begin,end);
                resampler->flushBuffers();
            }
            if(produced-consumed>=mlengine::pluginCapacity){mlengine::pumpPendingMessages();Sleep(1);continue;}
            const auto started=juce::Time::getHighResolutionTicks();
            juce::AudioSourceChannelInfo info(&output,0,(int)shared->frames);resampler->getNextAudioBlock(info);
            if(shared->requestedEpoch.load()!=epoch)continue;
            auto& slot=shared->slots[produced%mlengine::pluginCapacity];
            for(uint32_t i=0;i<shared->frames;++i)for(int ch=0;ch<2;++ch) {
                const auto value=output.getSample(ch,(int)i);ml::require(std::isfinite(value),"Non-finite mapped plugin output");slot.pcm[i*2+(uint32_t)ch]=value;
            }
            slot.epoch.store(epoch);slot.sequence.store(produced+1,std::memory_order_release);
            ++produced;shared->produced.store(produced,std::memory_order_release);shared->status.store(1,std::memory_order_release);
            shared->renderMicros.store((uint64_t)(juce::Time::highResolutionTicksToSeconds(juce::Time::getHighResolutionTicks()-started)*1000000));
        }
    }catch(const std::exception& error) {
        std::snprintf(shared->error,sizeof(shared->error),"%s",error.what());shared->status.store(2,std::memory_order_release);
        UnmapViewOfFile(shared);CloseHandle(mapping);return 5;
    }
    UnmapViewOfFile(shared);CloseHandle(mapping);return 0;
}
