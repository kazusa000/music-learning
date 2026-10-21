#pragma once
#include "../engine/SharedNode.h"

namespace ml {
struct NodeMapping {
    HANDLE mapping=nullptr,request=nullptr,complete=nullptr;
    mlengine::NodeHeader* shared=nullptr;
    ~NodeMapping(){if(shared)UnmapViewOfFile(shared);if(mapping)CloseHandle(mapping);if(request)CloseHandle(request);if(complete)CloseHandle(complete);}
};

inline int runNodeWorker(const String& name,const File& directory) {
    if(!name.startsWith("Local\\ml-node-"))return 2;
    NodeMapping memory;
    memory.mapping=OpenFileMappingW(FILE_MAP_ALL_ACCESS,FALSE,name.toWideCharPointer());
    if(!memory.mapping)return 3;
    memory.shared=static_cast<mlengine::NodeHeader*>(MapViewOfFile(memory.mapping,FILE_MAP_ALL_ACCESS,0,0,0));
    if(!memory.shared)return 4;
    auto* shared=memory.shared;
    try {
        require(shared->protocol==mlengine::nodeProtocol&&shared->headerBytes==sizeof(mlengine::NodeHeader)
            &&shared->capacity>0&&shared->capacity<=mlengine::nodeMaxFrames&&shared->sideSlots<=mlengine::nodeMaxPlugins
            &&shared->rate>=8000&&shared->rate<=192000,"Invalid graph worker protocol");
        MEMORY_BASIC_INFORMATION region{};
        require(VirtualQuery(shared,&region,sizeof(region))&&region.RegionSize>=mlengine::nodeBytes(shared->capacity,shared->sideSlots),"Graph mapping truncated");
        memory.request=OpenEventW(SYNCHRONIZE,FALSE,(name+"-request").toWideCharPointer());
        memory.complete=OpenEventW(EVENT_MODIFY_STATE,FALSE,(name+"-complete").toWideCharPointer());
        require(memory.request&&memory.complete,"Missing private graph events");
        std::unique_ptr<TrackRenderer> renderer;
        String loaded;
        uint64_t applied=0;
        AudioBuffer<float> main(2,(int)shared->capacity),output(2,(int)shared->capacity);
        std::vector<AudioBuffer<float>> sides;
        for(uint32_t i=0;i<shared->sideSlots;++i)sides.emplace_back(2,(int)shared->capacity);
        while(!shared->quit.load()) {
            const auto epoch=shared->programEpoch.load(std::memory_order_acquire);
            if(epoch!=applied) {
                std::array<uint64_t,4> words;
                for(size_t i=0;i<words.size();++i)words[i]=shared->configHash[i].load();
                if(epoch!=shared->programEpoch.load(std::memory_order_acquire))continue;
                const auto hash=String::toHexString(words.data(),32,0);
                if(hash!=loaded) {
                    const auto file=directory.getChildFile(hash+".json");
                    require(file.existsAsFile()&&file.getSize()<=8*1024*1024&&SHA256(file).toHexString()==hash,"Graph node program identity mismatch");
                    const auto project=JSON::parse(file.loadFileAsString());
                    require(project.isObject()&&array(project,"tracks").size()==1,"Graph worker needs one node");
                    require(array(array(project,"tracks")[0],"plugins").size()==(int)shared->sideSlots,"Graph plugin slots changed");
                    shared->stage.store(1);
                    if(!renderer) {
                        const auto started=Time::getHighResolutionTicks();
                        renderer=std::make_unique<TrackRenderer>(project,nullptr,true);
                        shared->loadMicros.store((uint64_t)(Time::highResolutionTicksToSeconds(Time::getHighResolutionTicks()-started)*1000000));
                    }else renderer->update(project);
                    loaded=hash;
                    const auto latencies=renderer->reportedLatencies();
                    for(size_t i=0;i<latencies.size();++i)shared->pluginLatency[i].store(latencies[i]);
                    shared->latency.store(renderer->reportedLatency());
                }
                if(epoch!=shared->programEpoch.load(std::memory_order_acquire))continue;
                applied=epoch;shared->appliedEpoch.store(applied,std::memory_order_release);shared->stage.store(4);
                shared->status.store(1,std::memory_order_release);
            }
            const auto sequence=shared->requestedSequence.load(std::memory_order_acquire);
            if(sequence==shared->completedSequence.load()) {
                if(WaitForSingleObject(memory.request,5)==WAIT_TIMEOUT)MessageManager::getInstance()->runDispatchLoopUntil(1);
                continue;
            }
            if(shared->programEpoch.load()!=applied)continue;
            const auto count=shared->frames;
            require(count>0&&count<=shared->capacity&&shared->inputDelay>=0,"Invalid graph request dimensions");
            for(uint32_t channel=0;channel<2;++channel) {
                FloatVectorOperations::copy(main.getWritePointer((int)channel),mlengine::nodeChannel(shared,0,channel),(int)count);
                for(uint32_t i=0;i<shared->sideSlots;++i)
                    FloatVectorOperations::copy(sides[i].getWritePointer((int)channel),mlengine::nodeChannel(shared,i+1,channel),(int)count);
            }
            for(int ch=0;ch<2;++ch)for(uint32_t i=0;i<count;++i) {
                require(std::isfinite(main.getSample(ch,(int)i)),"Non-finite graph input");
                for(const auto& side:sides)require(std::isfinite(side.getSample(ch,(int)i)),"Non-finite graph sidechain");
            }
            const auto started=Time::getHighResolutionTicks();
            renderer->graphBlock(shared->sample,shared->inputDelay,shared->origin,shared->chase!=0,shared->looping!=0,
                shared->loopStart,shared->loopEnd,main,sides,output,(int)count);
            const auto latencies=renderer->reportedLatencies();
            for(size_t i=0;i<latencies.size();++i)shared->pluginLatency[i].store(latencies[i]);
            shared->latency.store(renderer->reportedLatency());
            for(uint32_t ch=0;ch<2;++ch)for(uint32_t i=0;i<count;++i) {
                const auto value=output.getSample((int)ch,(int)i);require(std::isfinite(value),"Non-finite graph node output");
                mlengine::nodeChannel(shared,shared->sideSlots+1,ch)[i]=value;
            }
            shared->renderMicros.store((uint64_t)(Time::highResolutionTicksToSeconds(Time::getHighResolutionTicks()-started)*1000000));
            shared->completedSequence.store(sequence,std::memory_order_release);SetEvent(memory.complete);
        }
        return 0;
    }catch(const std::exception& error) {
        std::snprintf(shared->error,sizeof(shared->error),"%s",error.what());shared->status.store(2,std::memory_order_release);
        if(memory.complete)SetEvent(memory.complete);
        return 5;
    }
}
}
