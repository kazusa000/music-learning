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
inline void verifyWorkerBundle(const juce::File& executable){
    const auto expected=juce::SystemStats::getEnvironmentVariable("ML_PLUGIN_WORKER_SHA256",{});
    if(expected.isNotEmpty()&&juce::SHA256(executable).toHexString()!=expected)
        throw std::runtime_error("Plugin worker differs from the running engine bundle");
}

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
    size_t decoderBytes=0;
    juce::SHA256 verifyManifest(const juce::File& file) {
        if(file.getParentDirectory()!=directory||!file.existsAsFile()||file.getSize()>8*1024*1024)throw std::runtime_error("Plugin manifest escaped session or exceeds limit");
        const juce::SHA256 hash(file);
        if(file.getFileName()!=hash.toHexString()+".json")throw std::runtime_error("Plugin manifest cache identity mismatch");
        return hash;
    }
public:
    juce::String programPath() const {return directory.getChildFile(config+".json").getFullPathName();}
    static size_t bytesBound(int frames,double sourceRate,double deviceRate,size_t clips){
        const auto input=(size_t)std::ceil(frames*sourceRate/deviceRate)+64;
        return sizeof(PluginRing)+20*512*sizeof(float)+(size_t)frames*2*sizeof(float)+input*2*sizeof(float)+clips*1024*1024;
    }
    size_t bytes()const{return bytesBound((int)shared->frames,shared->sourceRate,shared->deviceRate,decoderBytes/(1024*1024));}
    bool retryRequired() { return status()["status"].toString()=="failed"; }
    PluginSource(const juce::var& spec,const juce::File& base,double sourceRate,double deviceRate,int frames)
        :directory(base.getChildFile(spec["storage_key"].toString())) {
        decoderBytes=(size_t)(int)spec.getProperty("audio_clip_count",0)*1024*1024;
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
            verifyWorkerBundle(executable);
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
        if(process.value)WaitForSingleObject(process.value,2000);
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
    uint64_t align(const juce::File& file,double start,bool loop,int64_t begin,int64_t end){
        const auto hash=verifyManifest(file).toHexString();
        const auto consumed=shared->consumed.load(std::memory_order_acquire);
        double source=(double)shared->origin.load()+(double)(consumed*shared->frames)*shared->sourceRate/shared->deviceRate;
        if(loop&&source>=end)source=(double)begin+std::fmod(source-begin,(double)(end-begin));
        if(!failed&&hash==config&&loop==(shared->looping.load()!=0)&&(!loop||(begin==shared->loopStart.load()&&end==shared->loopEnd.load()))
           &&std::abs(source-start)<=1.0)return consumed;
        configure(file,(int64_t)start,loop,begin,end);return 0;
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
#if defined(ML_ENABLE_MIDI_DELIVERY_DIAGNOSTICS)
        auto* midi = new juce::DynamicObject();
        midi->setProperty("process_block_calls", (juce::int64)shared->midiDelivery.processBlockCalls.load(std::memory_order_acquire));
        midi->setProperty("event_bearing_blocks", (juce::int64)shared->midiDelivery.eventBearingBlocks.load(std::memory_order_acquire));
        midi->setProperty("events_submitted", (juce::int64)shared->midiDelivery.eventsSubmitted.load(std::memory_order_acquire));
        midi->setProperty("records_dropped", (juce::int64)shared->midiDelivery.recordsDropped.load(std::memory_order_acquire));
        midi->setProperty("audio_blocks_dropped", (juce::int64)shared->midiDelivery.audioBlocksDropped.load(std::memory_order_acquire));
        midi->setProperty("midi_record_capacity", (int)midiDeliveryRecordCapacity);
        midi->setProperty("audio_blocks_capacity", (int)midiDeliveryAudioCapacity);
        midi->setProperty("ample_readiness_capacity", (int)ampleReadinessRecordCapacity);
        juce::Array<juce::var> records;
        const auto count = std::min<uint64_t>(shared->midiDelivery.recordsPublished.load(std::memory_order_acquire),
                                               midiDeliveryRecordCapacity);
        for (uint64_t i = 0; i < count; ++i) {
            const auto& item = shared->midiDelivery.records[static_cast<size_t>(i)];
            if (item.sequence.load(std::memory_order_acquire) != i + 1) continue;
            auto* record = new juce::DynamicObject();
            record->setProperty("process_block", (juce::int64)item.processBlock);
            record->setProperty("source_start_sample", (juce::int64)item.sourceStartSample);
            record->setProperty("block_samples", (int)item.blockSamples);
            record->setProperty("sample_offset", (int)item.sampleOffset);
            record->setProperty("plugin_index", (int)item.pluginIndex);
            const char* type = item.messageType == 1 ? "note_on" : item.messageType == 2 ? "note_off"
                : item.messageType == 3 ? "controller" : item.messageType == 4 ? "program_change"
                : item.messageType == 5 ? "pitch_wheel" : "other";
            record->setProperty("type", type);
            record->setProperty("channel", (int)item.channel);
            record->setProperty("data1", (int)item.data1);
            record->setProperty("data2", (int)item.data2);
            record->setProperty("velocity", item.velocity);
            records.add(juce::var(record));
        }
        midi->setProperty("records", records);
        juce::Array<juce::var> audioBlocks;
        const auto audioCount = midiDeliveryAudioBlockCount(shared->midiDelivery);
        for (uint64_t i = 0; i < audioCount; ++i) {
            const auto& item = shared->midiDelivery.audioBlocks[static_cast<size_t>(i)];
            if (item.sequence.load(std::memory_order_acquire) != i + 1) continue;
            auto* block = new juce::DynamicObject();
            block->setProperty("process_block", (juce::int64)item.processBlock);
            block->setProperty("source_start_sample", (juce::int64)item.sourceStartSample);
            block->setProperty("block_samples", (int)item.blockSamples);
            block->setProperty("plugin_index", (int)item.pluginIndex);
            block->setProperty("nonzero_samples", (int)item.nonzeroSamples);
            block->setProperty("nonfinite_samples", (int)item.nonfiniteSamples);
            block->setProperty("first_nonzero_offset", (int)item.firstNonzeroOffset);
            block->setProperty("last_nonzero_offset", (int)item.lastNonzeroOffset);
            block->setProperty("peak", item.peak);
            block->setProperty("rms", item.rms);
            audioBlocks.add(juce::var(block));
        }
        midi->setProperty("audio_blocks", audioBlocks);
        juce::Array<juce::var> readiness;
        const auto readinessCount = std::min<uint64_t>(
            shared->midiDelivery.ampleReadinessPublished.load(
                std::memory_order_acquire), ampleReadinessRecordCapacity);
        for (uint64_t i = 0; i < readinessCount; ++i) {
            const auto& item = shared->midiDelivery.ampleReadiness[
                static_cast<size_t>(i)];
            if (item.sequence.load(std::memory_order_acquire) != i + 1)
                continue;
            auto* record = new juce::DynamicObject();
            record->setProperty("source_sample", (juce::int64)item.sourceSample);
            record->setProperty("plugin_index", (int)item.pluginIndex);
            const char* phase = item.phase == 1 ? "after_state_restore"
                : item.phase == 2 ? "before_first_process_block"
                : item.phase == 3 ? "after_final_process_block" : "unknown";
            record->setProperty("phase", phase);
            record->setProperty("status", juce::String::fromUTF8(item.status));
            record->setProperty("product", juce::String::fromUTF8(item.product));
            record->setProperty("version", juce::String::fromUTF8(item.version));
            record->setProperty("module_path", juce::String::fromUTF8(item.modulePath));
            record->setProperty("module_sha256", juce::String::fromUTF8(item.moduleSha256));
            record->setProperty("fields_read", item.fieldsRead != 0);
            record->setProperty("ready", item.ready != 0);
            record->setProperty("loading_byte_read", item.loadingByteRead != 0);
            record->setProperty("loader_error_count_read",
                item.loaderErrorCountRead != 0);
            record->setProperty("status_read", item.statusRead != 0);
            record->setProperty("loading_byte", item.loadingByteRead != 0
                ? juce::var((int)item.loadingByte) : juce::var());
            record->setProperty("loader_error_count",
                item.loaderErrorCountRead != 0
                    ? juce::var((int)item.loaderErrorCount) : juce::var());
            record->setProperty("status_string", item.statusRead != 0
                ? juce::var(juce::String::fromUTF8(item.statusString))
                : juce::var());
            record->setProperty("component_vtable", juce::String::toHexString(
                static_cast<juce::int64>(item.componentVtable)));
            record->setProperty("normalized_component_vtable",
                item.normalizedComponentVtable == 0 ? juce::var()
                    : juce::var(juce::String::toHexString(
                        static_cast<juce::int64>(item.normalizedComponentVtable))));
            record->setProperty("normalized_component_wrapper",
                item.normalizedComponentWrapper == 0 ? juce::var()
                    : juce::var(juce::String::toHexString(
                        static_cast<juce::int64>(item.normalizedComponentWrapper))));
            record->setProperty("component_normalized_offset",
                (juce::int64)item.componentNormalizedOffset);
            record->setProperty("component_pointer_reads", (int)item.componentPointerReads);
            record->setProperty("component_bytes_read", (int)item.componentBytesRead);
            record->setProperty("processor_subobject", juce::String::toHexString(
                static_cast<juce::int64>(item.processorSubobject)));
            record->setProperty("processor_subobject_vtable",
                juce::String::toHexString(
                    static_cast<juce::int64>(item.processorSubobjectVtable)));
            record->setProperty("primary_processor", juce::String::toHexString(
                static_cast<juce::int64>(item.primaryProcessor)));
            readiness.add(juce::var(record));
        }
        midi->setProperty("ample_readiness", readiness);
        midi->setProperty("ample_readiness_dropped", (juce::int64)
            shared->midiDelivery.ampleReadinessDropped.load(
                std::memory_order_acquire));
        value->setProperty("midi_delivery", juce::var(midi));
#endif
        value->setProperty("epoch",(juce::int64)epoch.load());value->setProperty("config_hash",config);
        if(failed||code==2)value->setProperty("error",code==2?juce::String::fromUTF8(shared->error):juce::String("Plugin worker exited"));
        return juce::var(value);
    }
};
}
