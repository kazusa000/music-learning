#pragma once
#include "SharedPlugin.h"
#include <juce_cryptography/juce_cryptography.h>
#include <windows.h>
#include <sddl.h>
#include <memory>

namespace mlengine {
struct NativeHandle {
    HANDLE value=nullptr;
    ~NativeHandle(){if(value&&value!=INVALID_HANDLE_VALUE)CloseHandle(value);}
};
struct PrivateSecurity {
    PSECURITY_DESCRIPTOR descriptor=nullptr;
    SECURITY_ATTRIBUTES attributes{};
    PrivateSecurity() {
        NativeHandle token;
        if(!OpenProcessToken(GetCurrentProcess(),TOKEN_QUERY,&token.value))throw std::runtime_error("Cannot read worker owner");
        DWORD size=0;GetTokenInformation(token.value,TokenUser,nullptr,0,&size);std::vector<char> bytes(size);
        if(!GetTokenInformation(token.value,TokenUser,bytes.data(),size,&size))throw std::runtime_error("Cannot read worker SID");
        LPWSTR sid=nullptr;
        if(!ConvertSidToStringSidW(reinterpret_cast<TOKEN_USER*>(bytes.data())->User.Sid,&sid))throw std::runtime_error("Cannot encode worker SID");
        const auto sddl=juce::String("D:P(A;;GA;;;")+juce::String(sid)+")";LocalFree(sid);
        if(!ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl.toWideCharPointer(),SDDL_REVISION_1,&descriptor,nullptr))throw std::runtime_error("Cannot protect worker mapping");
        attributes={sizeof(SECURITY_ATTRIBUTES),descriptor,FALSE};
    }
    ~PrivateSecurity(){if(descriptor)LocalFree(descriptor);}
};

class PluginSource {
    NativeHandle mapping,job,process;
    PluginRing* shared=nullptr;
    juce::File directory;
    std::atomic<uint64_t> epoch{0};
    std::atomic<bool> updating{false}, reader{false};
    juce::String config;
    std::atomic<uint64_t> missed{0};
    bool failed=false;
    double configuredAt=0, readySeenAt=0, progressAt=0;
    uint64_t observedProduced=0;
    juce::SHA256 verifyManifest(const juce::File& file) {
        if(file.getParentDirectory()!=directory||!file.existsAsFile()||file.getSize()>8*1024*1024)throw std::runtime_error("Plugin manifest escaped session or exceeds limit");
        const juce::SHA256 hash(file);
        if(file.getFileName()!=hash.toHexString()+".json")throw std::runtime_error("Plugin manifest cache identity mismatch");
        return hash;
    }
public:
    bool retryRequired() { return status()["status"].toString()=="failed"; }
    PluginSource(const juce::var& spec,const juce::File& base,double sourceRate,double deviceRate,int frames)
        :directory(base.getChildFile(spec["storage_key"].toString())) {
        if(frames<=0||frames>(int)pluginMaxFrames)throw std::runtime_error("Invalid plugin worker quantum");
        if(!directory.createDirectory().wasOk())throw std::runtime_error("Cannot create plugin worker state directory");
        PrivateSecurity security;
        const auto name=juce::String("Local\\ml-plugin-")+juce::Uuid().toString().removeCharacters("-");
        mapping.value=CreateFileMappingW(INVALID_HANDLE_VALUE,&security.attributes,PAGE_READWRITE,0,sizeof(PluginRing),name.toWideCharPointer());
        if(!mapping.value||GetLastError()==ERROR_ALREADY_EXISTS)throw std::runtime_error("Cannot create private plugin mapping");
        shared=static_cast<PluginRing*>(MapViewOfFile(mapping.value,FILE_MAP_ALL_ACCESS,0,0,sizeof(PluginRing)));
        if(!shared)throw std::runtime_error("Cannot map plugin audio ring");
        new(shared)PluginRing();shared->frames=(uint32_t)frames;shared->sourceRate=sourceRate;shared->deviceRate=deviceRate;
        try {
            configure(juce::File(spec["plugin_project_path"].toString()),0,false,0,1);
            job.value=CreateJobObjectW(nullptr,nullptr);
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION info{};info.BasicLimitInformation.LimitFlags=JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if(!job.value||!SetInformationJobObject(job.value,JobObjectExtendedLimitInformation,&info,sizeof(info)))throw std::runtime_error("Cannot own plugin worker");
            const auto executable=juce::File::getSpecialLocation(juce::File::currentExecutableFile).getSiblingFile("ml_plugin_worker.exe");
            if(!executable.existsAsFile())throw std::runtime_error("Plugin worker executable missing");
            auto command=executable.getFullPathName().quoted()+" "+name.quoted()+" "+directory.getFullPathName().quoted();
            const std::wstring wide(command.toWideCharPointer());std::vector<wchar_t> writable(wide.begin(),wide.end());writable.push_back(0);
            STARTUPINFOW startup{};startup.cb=sizeof(startup);PROCESS_INFORMATION child{};
            if(!CreateProcessW(executable.getFullPathName().toWideCharPointer(),writable.data(),nullptr,nullptr,FALSE,CREATE_NO_WINDOW|CREATE_SUSPENDED,nullptr,nullptr,&startup,&child))throw std::runtime_error("Cannot start plugin worker");
            process.value=child.hProcess;NativeHandle thread;thread.value=child.hThread;
            if(!AssignProcessToJobObject(job.value,process.value)){TerminateProcess(process.value,1);throw std::runtime_error("Cannot assign plugin worker ownership");}
            ResumeThread(thread.value);
        }catch(...) {if(job.value)TerminateJobObject(job.value,1);UnmapViewOfFile(shared);shared=nullptr;throw;}
    }
    ~PluginSource() {
        if(shared)shared->quit.store(1);
        if(job.value)TerminateJobObject(job.value,0);
        if(shared)UnmapViewOfFile(shared);
    }
    void configure(const juce::File& file,int64_t start,bool loop,int64_t begin,int64_t end) {
        const auto hash=verifyManifest(file);const auto text=hash.toHexString();
        const auto raw=hash.getRawData();std::array<uint64_t,4> words;std::memcpy(words.data(),raw.getData(),32);
        for(size_t i=0;i<words.size();++i)shared->configHash[i].store(words[i]);
        shared->origin.store(start);shared->looping.store(loop?1:0);shared->loopStart.store(begin);shared->loopEnd.store(end);
        shared->consumed.store(0);shared->produced.store(0);shared->status.store(0);
        epoch.store(shared->requestedEpoch.fetch_add(1,std::memory_order_release)+1);config=text;missed.store(0);
        configuredAt=juce::Time::getMillisecondCounterHiRes();readySeenAt=0;progressAt=configuredAt;observedProduced=0;
    }
    void update(const juce::File& file) {
        const auto hash=verifyManifest(file);const auto text=hash.toHexString();if(text==config)return;
        const auto raw=hash.getRawData();std::array<uint64_t,4> words;std::memcpy(words.data(),raw.getData(),32);
        updating.store(true);while(reader.load())juce::Thread::sleep(1);
        for(size_t i=0;i<words.size();++i)shared->configHash[i].store(words[i]);
        shared->status.store(0);
        epoch.store(shared->requestedEpoch.fetch_add(1,std::memory_order_release)+1);config=text;
        configuredAt=juce::Time::getMillisecondCounterHiRes();readySeenAt=0;progressAt=configuredAt;observedProduced=shared->produced.load();
        updating.store(false);
    }
    const float* block(uint64_t sequence,int frames) {
        if(updating.load())return nullptr;
        reader.store(true);if(updating.load()){reader.store(false);return nullptr;}
        const auto expected=epoch.load();
        if(shared->readyEpoch.load(std::memory_order_acquire)!=expected||shared->status.load(std::memory_order_acquire)!=1)return nullptr;
        const auto& slot=shared->slots[sequence%pluginCapacity];
        if(frames==(int)shared->frames&&slot.sequence.load(std::memory_order_acquire)==sequence+1&&slot.epoch.load()==expected)return slot.pcm.data();
        missed.fetch_add(1);return nullptr;
    }
    void release(uint64_t sequence){shared->consumed.store(sequence+1,std::memory_order_release);reader.store(false);}
    juce::var status() {
        const auto now=juce::Time::getMillisecondCounterHiRes();
        const auto produced=shared->produced.load();
        if(produced!=observedProduced){observedProduced=produced;progressAt=now;}
        if(shared->readyEpoch.load()==epoch.load()&&readySeenAt==0)readySeenAt=now;
        const auto state=shared->status.load();
        const bool loadingTimeout=state==0&&((readySeenAt>0&&now-readySeenAt>2000)||now-configuredAt>45000);
        const bool stalled=state==1&&shared->consumed.load()>produced&&now-progressAt>2000;
        if(!failed&&(loadingTimeout||stalled)){failed=true;if(job.value)TerminateJobObject(job.value,1);}
        DWORD exit=STILL_ACTIVE;
        if(process.value&&GetExitCodeProcess(process.value,&exit)&&exit!=STILL_ACTIVE)failed=true;
        const auto code=shared->status.load(std::memory_order_acquire);
        auto* value=new juce::DynamicObject();
        value->setProperty("status",failed||code==2?"failed":shared->readyEpoch.load()==epoch.load()&&code==1?"ready":"loading");
        value->setProperty("pid",process.value?(int)GetProcessId(process.value):0);
        value->setProperty("missed_blocks",(juce::int64)missed.load());
        value->setProperty("render_micros",(juce::int64)shared->renderMicros.load());
        value->setProperty("load_micros",(juce::int64)shared->loadMicros.load());
        value->setProperty("load_stage",(int)shared->loadStage.load());
        value->setProperty("epoch",(juce::int64)epoch.load());value->setProperty("config_hash",config);
        if(failed||code==2)value->setProperty("error",code==2?juce::String::fromUTF8(shared->error):juce::String("Plugin worker exited"));
        return juce::var(value);
    }
};
}
