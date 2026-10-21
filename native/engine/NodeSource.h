#pragma once
#include "PluginSource.h"
#include "SharedNode.h"

namespace mlengine {
// Control/producer thread only. Each request retains exclusive ownership of the
// mapped inputs until completion. The device callback never waits on a worker.
class NodeSource final {
    NativeHandle mapping, requestEvent, completeEvent, job, process;
    NodeHeader* shared=nullptr;
    juce::File directory;
    juce::String program;
    uint64_t sequence=0, epoch=0;
    bool pending=false, failed=false;
    double requestedAt=0, configuredAt=0;
    juce::String failure;
    void fail(const juce::String& message) {
        failed=true;failure=message;
        if(job.value)TerminateJobObject(job.value,1);
    }
    void inspect() {
        if(failed)return;
        if(shared->status.load(std::memory_order_acquire)==2) {
            fail(juce::String::fromUTF8(shared->error,sizeof(shared->error)));return;
        }
        DWORD exit=STILL_ACTIVE;
        if(GetExitCodeProcess(process.value,&exit)&&exit!=STILL_ACTIVE){fail("Graph worker exited: "+juce::String((int)exit));return;}
        const auto now=juce::Time::getMillisecondCounterHiRes();
        if(shared->appliedEpoch.load()!=epoch&&now-configuredAt>45000)fail("Graph worker loading timed out");
        if(pending&&now-requestedAt>2000)fail("Graph worker processing stalled");
    }
public:
    NodeSource(const juce::File& file,double rate,int frames,int plugins):directory(file.getParentDirectory()) {
        if(frames<=0||frames>(int)nodeMaxFrames||plugins<0||plugins>(int)nodeMaxPlugins)
            throw std::runtime_error("Invalid graph node dimensions");
        const auto bytes=nodeBytes((uint32_t)frames,(uint32_t)plugins);
        PrivateSecurity security;
        const auto name=juce::String("Local\\ml-node-")+juce::Uuid().toString().removeCharacters("-");
        mapping.value=CreateFileMappingW(INVALID_HANDLE_VALUE,&security.attributes,PAGE_READWRITE,0,(DWORD)bytes,name.toWideCharPointer());
        if(!mapping.value||GetLastError()==ERROR_ALREADY_EXISTS)throw std::runtime_error("Cannot create graph mapping");
        shared=static_cast<NodeHeader*>(MapViewOfFile(mapping.value,FILE_MAP_ALL_ACCESS,0,0,bytes));
        if(!shared)throw std::runtime_error("Cannot map graph node");
        new(shared)NodeHeader();shared->headerBytes=sizeof(NodeHeader);shared->capacity=(uint32_t)frames;shared->sideSlots=(uint32_t)plugins;shared->rate=rate;
        try {
            requestEvent.value=CreateEventW(&security.attributes,FALSE,FALSE,(name+"-request").toWideCharPointer());
            if(!requestEvent.value||GetLastError()==ERROR_ALREADY_EXISTS)throw std::runtime_error("Cannot create graph request event");
            completeEvent.value=CreateEventW(&security.attributes,FALSE,FALSE,(name+"-complete").toWideCharPointer());
            if(!completeEvent.value||GetLastError()==ERROR_ALREADY_EXISTS)throw std::runtime_error("Cannot create graph completion event");
            configure(file);
            job.value=CreateJobObjectW(nullptr,nullptr);
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION info{};info.BasicLimitInformation.LimitFlags=JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if(!job.value||!SetInformationJobObject(job.value,JobObjectExtendedLimitInformation,&info,sizeof(info)))throw std::runtime_error("Cannot own graph worker");
            const auto executable=juce::File::getSpecialLocation(juce::File::currentExecutableFile).getSiblingFile("ml_plugin_worker.exe");
            if(!executable.existsAsFile())throw std::runtime_error("Graph worker missing");
            verifyWorkerBundle(executable);
            const auto command=executable.getFullPathName().quoted()+" --node "+name.quoted()+" "+directory.getFullPathName().quoted();
            const std::wstring wide(command.toWideCharPointer());std::vector<wchar_t> writable(wide.begin(),wide.end());writable.push_back(0);
            STARTUPINFOW startup{};startup.cb=sizeof(startup);PROCESS_INFORMATION child{};
            if(!CreateProcessW(executable.getFullPathName().toWideCharPointer(),writable.data(),nullptr,nullptr,FALSE,CREATE_NO_WINDOW|CREATE_SUSPENDED,nullptr,nullptr,&startup,&child))throw std::runtime_error("Cannot start graph worker");
            process.value=child.hProcess;NativeHandle thread;thread.value=child.hThread;
            if(!AssignProcessToJobObject(job.value,process.value)){TerminateProcess(process.value,1);throw std::runtime_error("Cannot own graph child");}
            ResumeThread(thread.value);
        }catch(...){if(job.value)TerminateJobObject(job.value,1);UnmapViewOfFile(shared);shared=nullptr;throw;}
    }
    NodeSource(const NodeSource&)=delete;
    NodeSource& operator=(const NodeSource&)=delete;
    ~NodeSource(){
        if(shared)shared->quit.store(1);
        if(job.value)TerminateJobObject(job.value,0);
        if(process.value)WaitForSingleObject(process.value,2000);
        if(shared)UnmapViewOfFile(shared);
    }
    void configure(const juce::File& file) {
        if(pending)throw std::runtime_error("Graph program update requires an idle node");
        if(file.getParentDirectory()!=directory||!file.existsAsFile()||file.getSize()>8*1024*1024)
            throw std::runtime_error("Graph program escaped workspace or exceeds limit");
        const juce::SHA256 hash(file);const auto text=hash.toHexString();
        if(file.getFileName()!=text+".json")throw std::runtime_error("Graph program identity mismatch");
        if(text==program)return;
        const auto raw=hash.getRawData();std::array<uint64_t,4> words;std::memcpy(words.data(),raw.getData(),32);
        for(size_t i=0;i<words.size();++i)shared->configHash[i].store(words[i]);
        shared->status.store(0);epoch=shared->programEpoch.fetch_add(1,std::memory_order_release)+1;
        program=text;configuredAt=juce::Time::getMillisecondCounterHiRes();
        if(requestEvent.value)SetEvent(requestEvent.value);
    }
    bool ready(){inspect();return !failed&&shared->status.load(std::memory_order_acquire)==1&&shared->appliedEpoch.load()==epoch;}
    bool isFailed(){inspect();return failed;}
    bool isPending()const{return pending;}
    int pendingFrames()const{return (int)shared->frames;}
    void waitReady(int milliseconds=45000,std::atomic<bool>* cancel=nullptr) {
        const auto until=juce::Time::getMillisecondCounterHiRes()+milliseconds;
        while(!ready()){
            if(cancel&&cancel->load())throw std::runtime_error("Graph preparation cancelled");
            if(failed)throw std::runtime_error(failure.toStdString());
            if(juce::Time::getMillisecondCounterHiRes()>=until)throw std::runtime_error("Graph node not ready within deadline");
            juce::Thread::sleep(1);
        }
    }
    int latency()const{return shared->latency.load();}
    std::vector<int> pluginLatencies()const{
        std::vector<int> result;for(uint32_t i=0;i<shared->sideSlots;++i)result.push_back(shared->pluginLatency[i].load());return result;
    }
    size_t bytes()const{return nodeBytes(shared->capacity,shared->sideSlots);}
    const juce::String& hash()const{return program;}
    juce::String programPath()const{return directory.getChildFile(program+".json").getFullPathName();}
    void submit(int64_t sample,int64_t inputDelay,int64_t origin,bool chase,bool loop,int64_t begin,int64_t end,
                const juce::AudioBuffer<float>& main,const std::vector<juce::AudioBuffer<float>>& sides,int count) {
        if(pending)throw std::runtime_error("Graph input is still owned by a pending request");
        if(!ready())throw std::runtime_error("Graph node is not ready");
        if(count<=0||count>(int)shared->capacity||main.getNumChannels()<2||main.getNumSamples()<count
           ||sides.size()!=shared->sideSlots||inputDelay<0||(loop&&end<=begin))throw std::runtime_error("Invalid graph input");
        for(const auto& side:sides)if(side.getNumChannels()<2||side.getNumSamples()<count)throw std::runtime_error("Invalid graph side input");
        for(int channel=0;channel<2;++channel){
            juce::FloatVectorOperations::copy(nodeChannel(shared,0,(uint32_t)channel),main.getReadPointer(channel),count);
            for(uint32_t i=0;i<shared->sideSlots;++i)juce::FloatVectorOperations::copy(nodeChannel(shared,i+1,(uint32_t)channel),sides[i].getReadPointer(channel),count);
        }
        shared->sample=sample;shared->inputDelay=inputDelay;shared->origin=origin;shared->chase=chase?1u:0u;
        shared->looping=loop?1u:0u;shared->loopStart=begin;shared->loopEnd=end;shared->frames=(uint32_t)count;
        pending=true;requestedAt=juce::Time::getMillisecondCounterHiRes();
        shared->requestedSequence.store(++sequence,std::memory_order_release);SetEvent(requestEvent.value);
    }
    bool receive(juce::AudioBuffer<float>& output,int count,int waitMs=0) {
        if(!pending)throw std::runtime_error("Graph output has no pending request");
        if(count!=(int)shared->frames||output.getNumChannels()<2||output.getNumSamples()<count)throw std::runtime_error("Invalid graph output");
        if(shared->completedSequence.load(std::memory_order_acquire)!=sequence&&waitMs>0)WaitForSingleObject(completeEvent.value,(DWORD)waitMs);
        inspect();if(failed)throw std::runtime_error(failure.toStdString());
        if(shared->completedSequence.load(std::memory_order_acquire)!=sequence)return false;
        if(shared->appliedEpoch.load()!=epoch)throw std::runtime_error("Graph output belongs to another program");
        for(int ch=0;ch<2;++ch)juce::FloatVectorOperations::copy(output.getWritePointer(ch),nodeChannel(shared,shared->sideSlots+1,(uint32_t)ch),count);
        pending=false;return true;
    }
    juce::var status(){
        const bool available=ready();auto* value=new juce::DynamicObject();
        value->setProperty("status",failed?"failed":available?"ready":"loading");
        value->setProperty("pid",process.value?(int)GetProcessId(process.value):0);
        value->setProperty("config_hash",program);value->setProperty("epoch",(juce::int64)epoch);
        value->setProperty("latency_samples",latency());value->setProperty("pending",pending);
        value->setProperty("render_micros",(juce::int64)shared->renderMicros.load());
        value->setProperty("load_micros",(juce::int64)shared->loadMicros.load());
        value->setProperty("audio_buffer_bytes",(juce::int64)bytes());
        if(failed)value->setProperty("error",failure);return juce::var(value);
    }
};
}
