#include <juce_audio_devices/juce_audio_devices.h>
#include <windows.h>
#include <sddl.h>
#include <atomic>
#include <cmath>
#include <future>
#include <thread>
#include <vector>
#include <set>
#include "SessionGraph.h"

using namespace juce;
static var obj(std::initializer_list<std::pair<String, var>> fields) {
    auto* o = new DynamicObject(); for (const auto& f : fields) o->setProperty(f.first, f.second); return var(o);
}
static var get(const var& v, const char* key, var fallback = {}) { return v.getProperty(key, fallback); }

class DeviceEngine final : public AudioIODeviceCallback {
    AudioDeviceManager device;
    std::atomic<int64> position{0}, callbacks{0}, remaining{0}, toneCommand{0};
    std::atomic<float> peak{0};
    double rate = 48000.0;
    std::unique_ptr<mlengine::SessionGraph> graph;
    std::unique_ptr<mlengine::SessionGraph> preview;
    AudioBuffer<float> previewBuffer;
    std::atomic<bool> previewPlaying{false};
    String previewClient,previewId;
    int previewSequence=0;
    std::atomic<float> previewVolume{.7f};
    mlengine::PluginPool pluginPool;
    std::set<String> warmKeys;
    std::atomic<bool> filePlaying{false};
    std::atomic<bool> callbackBusy{false};
    int generation = 0;
    String controlOwner;
public:
    size_t availableGraphBudget()const{
        size_t used=graph?(size_t)graph->cacheBytes():0;
        for(const auto& source:pluginPool)if(source.second.use_count()==1)used+=source.second->bytes();
        if(preview)used+=(size_t)preview->cacheBytes()+(size_t)previewBuffer.getNumSamples()*previewBuffer.getNumChannels()*sizeof(float);
        constexpr size_t limit=256ull*1024*1024;return used<limit?limit-used:0;
    }
    ~DeviceEngine() override { device.removeAudioCallback(this); device.closeAudioDevice(); }
    void audioDeviceAboutToStart(AudioIODevice* d) override { rate = d->getCurrentSampleRate(); }
    void audioDeviceStopped() override { remaining.store(0); }
    void audioDeviceIOCallbackWithContext(const float* const*, int, float* const* out,
        int channels, int frames, const AudioIODeviceCallbackContext&) override {
        callbackBusy.store(true);
        struct Completion {std::atomic<bool>& flag;~Completion(){flag.store(false);}} completion{callbackBusy};
        for (int ch = 0; ch < channels; ++ch) if (out[ch]) FloatVectorOperations::clear(out[ch], frames);
        if (graph && filePlaying.load()) {
            float maximum = 0;
            graph->consume(out, channels, frames, maximum);
            if (graph->ended() || graph->failed.load()) filePlaying.store(false);
            peak.store(maximum);
        }else {
        auto p = position.load(std::memory_order_relaxed), left = remaining.load(std::memory_order_relaxed);
        const auto command = toneCommand.exchange(0);
        if (command < 0) left = 0;
        else if (command > 0) { p = 0; left = command; }
        float maximum = 0;
        for (int i = 0; i < frames; ++i) {
            float value = 0;
            if (left > 0) {
                // A bounded -42 dBFS probe; normal transport will replace this in N2.
                const double edge = jmin(1.0, jmin((double)p, (double)left) / (rate * .005));
                value = (float)(.007943282 * edge * std::sin(2.0 * MathConstants<double>::pi * 9973.0 * p / rate));
                ++p; --left;
            }
            maximum = jmax(maximum, std::abs(value));
            for (int ch = 0; ch < channels; ++ch) if (out[ch]) out[ch][i] = value;
        }
        position.store(p, std::memory_order_relaxed); remaining.store(left, std::memory_order_relaxed);
        peak.store(maximum, std::memory_order_relaxed);
        }
        if(preview&&previewPlaying.load()) {
            previewBuffer.clear();float maximum=0;
            preview->consume(previewBuffer.getArrayOfWritePointers(),2,frames,maximum);
            const auto volume=previewVolume.load();
            float mixedPeak=0;
            for(int ch=0;ch<channels;++ch)if(out[ch])for(int i=0;i<frames;++i) {
                out[ch][i]=jlimit(-.97f,.97f,out[ch][i]*.16f+previewBuffer.getSample(jmin(ch,1),i)*volume);
                mixedPeak=jmax(mixedPeak,std::abs(out[ch][i]));
            }
            peak.store(mixedPeak);
            if(preview->ended()||preview->failed.load())previewPlaying.store(false);
        }
        callbacks.fetch_add(1,std::memory_order_relaxed);
    }
    var status() {
        auto* d = device.getCurrentAudioDevice();
        auto result = obj({{"protocol_version", 1}, {"pid", (int)GetCurrentProcessId()},
            {"status", d ? "ready" : "closed"}, {"device", d ? d->getName() : String()},
            {"sample_rate", d ? d->getCurrentSampleRate() : 0.0},
            {"buffer_frames", d ? d->getCurrentBufferSizeSamples() : 0},
            {"position_sample", position.load()}, {"callbacks", callbacks.load()},
            {"playing", remaining.load() > 0}, {"peak", peak.load()},
            {"xruns", device.getXRunCount()}});
        auto* fields = result.getDynamicObject();
        fields->setProperty("preview",obj({{"playing",previewPlaying.load()},{"client_id",previewClient},{"sequence",previewSequence},
            {"preview_id",previewId},{"volume",previewVolume.load()}}));
        fields->setProperty("generation", generation);
        fields->setProperty("owner_id",controlOwner);
        Array<var> retained;int64 warmBytes=0;
        for(const auto& source:pluginPool){retained.add(source.second->programPath());if(source.second.use_count()==1)warmBytes+=(int64)source.second->bytes();}
        if(preview)warmBytes+=preview->cacheBytes()+(int64)previewBuffer.getNumSamples()*previewBuffer.getNumChannels()*sizeof(float);
        if(graph&&graph->routed)for(const auto& program:graph->routed->programs())retained.add(program);
        fields->setProperty("retained_programs",var(retained));
        fields->setProperty("cache_bytes",warmBytes);
        if (graph) {
            fields->setProperty("project_id", graph->manifest["project_id"]);
            fields->setProperty("revision_id", graph->manifest["revision_id"]);
            fields->setProperty("view", graph->manifest["view"]);
            fields->setProperty("playing", filePlaying.load());
            fields->setProperty("position_sample", (int64)jmin(graph->songDuration,graph->playedSample.load()));
            fields->setProperty("render_position_sample", (int64)graph->playedSample.load());
            fields->setProperty("source_sample_rate", graph->sourceRate);
            fields->setProperty("duration_samples", (int64)graph->songDuration);
            fields->setProperty("cache_bytes", graph->cacheBytes()+warmBytes);
            fields->setProperty("consumed_device_frames", (int64)(graph->read.load()*graph->frames));
            fields->setProperty("underruns", (int64)graph->underruns.load());
            fields->setProperty("loop", graph->looping);
            fields->setProperty("loop_start_sample",(int64)graph->loopStart);
            fields->setProperty("loop_end_sample",(int64)graph->loopEnd);
            fields->setProperty("monitor", graph->monitorValues());
            fields->setProperty("meters", graph->meterValues());
            fields->setProperty("file_error", graph->failed.load());
            bool ready=false;const auto plugins=graph->pluginValues(ready);fields->setProperty("plugins",plugins);
            bool pluginFailed=false;
            if(auto* items=plugins.getDynamicObject())for(const auto& item:items->getProperties())if(item.value["status"].toString()=="failed")pluginFailed=true;
            fields->setProperty("desired_revision",graph->manifest["revision_id"]);
            fields->setProperty("applied_revision",ready?(graph->routed?var(graph->routed->appliedRevision()):graph->manifest["revision_id"]):var());
            if(graph->routed&&graph->routed->failure().isNotEmpty())fields->setProperty("control_error",graph->routed->failure());
            if(!ready)fields->setProperty("status",pluginFailed||(graph->routed&&graph->routed->failure().isNotEmpty())?"partial":"loading");
        }
        return result;
    }
    var command(const var& request) {
        const auto action = get(request, "action").toString();
        if(request.hasProperty("owner_id")&&action!="open"&&action!="claim"&&action!="release"&&get(request,"owner_id").toString()!=controlOwner)
            throw std::runtime_error("Playback window no longer owns output");
        if(request.hasProperty("expected_generation") && (int)get(request,"expected_generation")!=generation)
            throw std::runtime_error("Engine generation changed");
        if (action == "status" || action == "hello") return status();
        if(action=="claim"){controlOwner=get(request,"owner_id").toString();return status();}
        if(action=="release") {
            if(controlOwner!=get(request,"owner_id").toString())return status();
            filePlaying.store(false);device.removeAudioCallback(this);graph.reset();pluginPool.clear();controlOwner.clear();
            if(previewPlaying.load())device.addAudioCallback(this);else device.closeAudioDevice();
            return status();
        }
        if (action == "devices") {
            Array<var> result;
            for (auto* type : device.getAvailableDeviceTypes()) {
                if (type->getTypeName() != "Windows Audio") continue;
                type->scanForDevices();
                for (auto name : type->getDeviceNames(false))
                    result.add(obj({{"backend", type->getTypeName()}, {"name", name}}));
            }
            return result;
        }
        if (action == "configure") {
            toneCommand.store(-1); filePlaying.store(false);previewPlaying.store(false); device.removeAudioCallback(this); graph.reset();preview.reset();pluginPool.clear(); device.closeAudioDevice();
            controlOwner.clear();
            device.setCurrentAudioDeviceType("Windows Audio", false);
            const auto error = device.initialise(0, 2, nullptr, true);
            if (error.isNotEmpty()) throw std::runtime_error(error.toStdString());
            AudioDeviceManager::AudioDeviceSetup setup; device.getAudioDeviceSetup(setup);
            const auto name = get(request, "device").toString(); if (name.isNotEmpty()) setup.outputDeviceName = name;
            setup.sampleRate = (double)get(request, "sample_rate", 48000.0);
            setup.bufferSize = (int)get(request, "buffer_frames", 512);
            const auto failure = device.setAudioDeviceSetup(setup, true);
            if (failure.isNotEmpty()) throw std::runtime_error(failure.toStdString());
            previewBuffer.setSize(2,device.getCurrentAudioDevice()->getCurrentBufferSizeSamples());
            device.addAudioCallback(this); return status();
        }
        if(action=="preview") {
            auto* d=device.getCurrentAudioDevice();if(!d)throw std::runtime_error("Configure an audio device first");
            const auto client=get(request,"client_id").toString();const auto sequence=(int)get(request,"client_sequence");
            if(client==previewClient&&sequence<=previewSequence)throw std::runtime_error("Stale preview command");
            const bool stop=(bool)get(request,"stop",false);
            if(stop&&client!=previewClient)return status();
            if(request.hasProperty("manifest")) {
                const auto manifest=get(request,"manifest");
                auto ready=std::make_unique<mlengine::SessionGraph>(manifest,d->getCurrentSampleRate(),d->getCurrentBufferSizeSamples(),0,false,0,(double)manifest["duration_samples"],nullptr,availableGraphBudget());
                device.removeAudioCallback(this);preview.swap(ready);previewId=get(request,"preview_id").toString();
                previewClient=client;previewSequence=sequence;previewVolume.store((float)(double)get(request,"volume",.7));
                previewPlaying.store(true);device.addAudioCallback(this);return status();
            }
            if(client!=previewClient)return status();
            previewSequence=sequence;
            if(request.hasProperty("volume"))previewVolume.store((float)(double)get(request,"volume"));
            if(stop){previewPlaying.store(false);device.removeAudioCallback(this);preview.reset();device.addAudioCallback(this);}
            return status();
        }
        if(action=="update") {
            if(!graph)throw std::runtime_error("Open a project first");
            const auto value=get(request,"manifest");
            if(graph->routed&&!filePlaying.load()){while(callbackBusy.load())Thread::sleep(1);graph->routed->setPlaying(false);}
            if(graph->routed&&value.hasProperty("routing_graph")&&graph->routed->update(value)){
                graph->manifest=value;++generation;return status();
            }
            bool compatible=value["project_id"]==graph->manifest["project_id"]&&value["view"]==graph->manifest["view"]
                &&value["sample_rate"]==graph->manifest["sample_rate"]&&value["duration_samples"]==graph->manifest["duration_samples"]
                &&value["tail_samples"]==graph->manifest["tail_samples"]&&!graph->routed&&!value.hasProperty("routing_graph");
            const auto* next=value["tracks"].getArray();const auto* previous=graph->manifest["tracks"].getArray();
            compatible=compatible&&next&&previous&&next->size()==previous->size();
            if(compatible)for(int i=0;i<next->size();++i) {
                const auto& node=(*next)[i];const auto& old=(*previous)[i];
                if(node["id"]!=old["id"]){compatible=false;break;}
                if(graph->tracks[(size_t)i]->plugin) {
                    if(graph->tracks[(size_t)i]->plugin->retryRequired()){compatible=false;break;}
                    if(node["runtime_signature"]!=old["runtime_signature"]){compatible=false;break;}
                }else if(JSON::toString(node,true)!=JSON::toString(old,true)){compatible=false;break;}
            }
            if(!compatible) {
                auto reopen=request.clone();auto* fields=reopen.getDynamicObject();fields->setProperty("action","open");
                fields->setProperty("position_sample",graph->playedSample.load());fields->setProperty("playing",filePlaying.load());
                return command(reopen);
            }
            for(int i=0;i<next->size();++i)if(graph->tracks[(size_t)i]->plugin)
                graph->tracks[(size_t)i]->plugin->update(File((*next)[i]["plugin_project_path"].toString()));
            graph->manifest=value;++generation;return status();
        }
        if (action == "open" || action == "transport") {
            auto* d = device.getCurrentAudioDevice(); if (!d) throw std::runtime_error("Configure an audio device first");
            if (action == "transport" && !graph) throw std::runtime_error("Open a project first");
            const bool play = (bool)get(request,"playing", action=="open" ? false : filePlaying.load());
            const bool replay = action=="transport" && play && graph->ended();
            const bool rebuild = action == "open" || request.hasProperty("position_sample") || request.hasProperty("loop")
                || request.hasProperty("loop_start_sample") || request.hasProperty("loop_end_sample") || replay;
            if (rebuild) {
                const bool previousPlaying=filePlaying.load();bool committed=false;
                struct Restore{std::function<void()> action;~Restore(){action();}} restore{[&]{if(!committed){if(graph&&graph->routed)graph->routed->resumeProducer();filePlaying.store(previousPlaying);}}};
                filePlaying.store(false);while(callbackBusy.load())Thread::sleep(1);
                const auto value = action == "open" ? get(request,"manifest") : graph->manifest;
                const auto start = (double)get(request,"position_sample",graph && !replay ? graph->playedSample.load() : 0.0);
                const auto begin = (double)get(request,"loop_start_sample",graph ? graph->loopStart : 0.0);
                const auto end = (double)get(request,"loop_end_sample",graph ? graph->loopEnd : (double)value["duration_samples"]);
                const bool loop = (bool)get(request,"loop",graph ? graph->looping : false);
                if(action=="transport"&&graph->routed){
                    graph->routed->seek(start,loop,begin,end);graph->origin=start;graph->playedSample.store(start);
                    graph->looping=loop;graph->loopStart=begin;graph->loopEnd=end;graph->read.store(0);++generation;
                    graph->routed->setPlaying(play);filePlaying.store(play);committed=true;return status();
                }
                if(action=="open"&&graph&&graph->routed)graph->routed->pauseProducer();
                auto ready = std::make_unique<mlengine::SessionGraph>(value,d->getCurrentSampleRate(),d->getCurrentBufferSizeSamples(),start,loop,begin,end,&pluginPool,availableGraphBudget());
                if(graph && (action=="transport" || (graph->manifest["project_id"]==value["project_id"]&&graph->manifest["view"]==value["view"]))) {
                    const auto previous=graph->monitorValues();auto* matching=new DynamicObject();
                    for(const auto& track:ready->tracks)if(previous.hasProperty(track->id))matching->setProperty(track->id,previous.getProperty(track->id,{}));
                    ready->monitor(var(matching));
                }
                filePlaying.store(false); device.removeAudioCallback(this);
                graph.swap(ready); ++generation; toneCommand.store(-1);
                device.addAudioCallback(this);
                ready.reset();
                if(value["view"].toString()=="arrangement") {
                    warmKeys.clear();
                    if(auto* items=value["tracks"].getArray())for(const auto& item:*items)if(item.hasProperty("plugin_project_path"))
                        warmKeys.insert(value["project_id"].toString()+":"+item["id"].toString()+":"+item["runtime_signature"].toString());
                }
                for(auto it=pluginPool.begin();it!=pluginPool.end();) {
                    const bool sameProject=it->first.startsWith(value["project_id"].toString()+":");
                    if(it->second.use_count()==1&&(!sameProject||!warmKeys.count(it->first)))it=pluginPool.erase(it);else ++it;
                }
                committed=true;
            }
            if(action=="open")controlOwner=get(request,"owner_id").toString();
            if(graph->routed){if(!play){filePlaying.store(false);while(callbackBusy.load())Thread::sleep(1);}graph->routed->setPlaying(play);}
            filePlaying.store(play); return status();
        }
        if (action == "monitor") {
            if (!graph) throw std::runtime_error("Open a project first");
            graph->monitor(get(request,"tracks"));return status();
        }
        if (action == "probe") {
            if (!device.getCurrentAudioDevice()) throw std::runtime_error("Configure an audio device first");
            const auto seconds = (double)get(request, "seconds", .5);
            if (!(seconds > 0 && seconds <= 3)) throw std::runtime_error("Probe duration must be 0..3 seconds");
            filePlaying.store(false); toneCommand.store((int64)(seconds * rate)); return status();
        }
        if (action == "stop") { filePlaying.store(false);while(callbackBusy.load())Thread::sleep(1);if(graph&&graph->routed)graph->routed->setPlaying(false);toneCommand.store(-1); return status(); }
        if (action == "close") { filePlaying.store(false);previewPlaying.store(false); toneCommand.store(-1); device.removeAudioCallback(this); graph.reset();preview.reset();pluginPool.clear(); device.closeAudioDevice(); return status(); }
        throw std::runtime_error("Unsupported engine action");
    }
};

static bool transfer(HANDLE pipe, void* memory, DWORD bytes, bool writing) {
    auto* p = static_cast<char*>(memory);
    while (bytes) { DWORD done = 0;
        if (!(writing ? WriteFile(pipe, p, bytes, &done, nullptr) : ReadFile(pipe, p, bytes, &done, nullptr)) || !done) return false;
        p += done; bytes -= done;
    } return true;
}
static String currentUserSDDL() {
    HANDLE token = nullptr;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) throw std::runtime_error("Cannot read process identity");
    DWORD bytes = 0; GetTokenInformation(token, TokenUser, nullptr, 0, &bytes);
    std::vector<char> storage(bytes);
    if (!GetTokenInformation(token, TokenUser, storage.data(), bytes, &bytes)) { CloseHandle(token); throw std::runtime_error("Cannot read user SID"); }
    LPWSTR sid = nullptr;
    if (!ConvertSidToStringSidW(reinterpret_cast<TOKEN_USER*>(storage.data())->User.Sid, &sid)) { CloseHandle(token); throw std::runtime_error("Cannot encode user SID"); }
    const String result = "D:P(A;;GA;;;" + String(sid) + ")"; LocalFree(sid); CloseHandle(token); return result;
}
int main(int argc, char** argv) {
    if (argc != 2 || !String(argv[1]).startsWith("ml-engine-")) return 2;
    const auto token = SystemStats::getEnvironmentVariable("ML_ENGINE_TOKEN", {});
    if (token.length() < 32) return 3;
    ScopedJuceInitialiser_GUI juce;
    DeviceEngine engine;
    const auto name = String("\\\\.\\pipe\\") + String(argv[1]);
    PSECURITY_DESCRIPTOR descriptor = nullptr;
    if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(currentUserSDDL().toWideCharPointer(), SDDL_REVISION_1, &descriptor, nullptr)) return 4;
    SECURITY_ATTRIBUTES attributes{sizeof(SECURITY_ATTRIBUTES), descriptor, FALSE};
    HANDLE pipe = CreateNamedPipeW(name.toWideCharPointer(), PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE,
        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS, 1, 1024 * 1024, 1024 * 1024, 0, &attributes);
    LocalFree(descriptor); if (pipe == INVALID_HANDLE_VALUE) return 5;
    std::thread controller([&] {
        int64 lastSequence = 0;
        if (ConnectNamedPipe(pipe, nullptr) || GetLastError() == ERROR_PIPE_CONNECTED) {
            for (;;) {
                unsigned char prefix[4]; if (!transfer(pipe, prefix, 4, false)) break;
                const auto size = (static_cast<unsigned int>(prefix[0]) << 24) | (static_cast<unsigned int>(prefix[1]) << 16) | (static_cast<unsigned int>(prefix[2]) << 8) | prefix[3];
                if (size == 0 || size > 1024 * 1024) break;
                std::vector<char> bytes(size + 1, 0); if (!transfer(pipe, bytes.data(), size, false)) break;
                const auto request = JSON::parse(String::fromUTF8(bytes.data(), (int)size));
                var reply;
                if (get(request, "token").toString() != token) reply = obj({{"ok", false}, {"error", "Invalid engine identity"}});
                else if ((int)get(request, "protocol_version", 0) != 1) reply = obj({{"ok", false}, {"error", "Unsupported protocol"}});
                else if ((int64)get(request, "sequence", 0) <= lastSequence) reply = obj({{"ok", false}, {"error", "Stale engine sequence"}});
                else {
                    lastSequence = (int64)get(request, "sequence");
                    auto promise = std::make_shared<std::promise<var>>(); auto future = promise->get_future();
                    MessageManager::callAsync([&, request, promise] {
                        try { promise->set_value(obj({{"ok", true}, {"result", engine.command(request)}, {"sequence", get(request, "sequence")}, {"session_id", get(request, "session_id")}, {"request_id", get(request, "request_id")}, {"state", "applied"}})); }
                        catch (const std::exception& e) { promise->set_value(obj({{"ok", false}, {"error", String(e.what())}})); }
                    });
                    reply = future.get();
                }
                const auto json = JSON::toString(reply, true); const auto length = (unsigned int)json.getNumBytesAsUTF8();
                unsigned char header[] = {(unsigned char)(length >> 24), (unsigned char)(length >> 16), (unsigned char)(length >> 8), (unsigned char)length};
                if (!transfer(pipe, header, 4, true) || !transfer(pipe, (void*)json.toRawUTF8(), length, true)) break;
                if (get(request, "action").toString() == "close" && (bool)get(reply, "ok", false)) break;
            }
        }
        FlushFileBuffers(pipe); DisconnectNamedPipe(pipe); CloseHandle(pipe);
        MessageManager::callAsync([] { MessageManager::getInstance()->stopDispatchLoop(); });
    });
    MessageManager::getInstance()->runDispatchLoop(); controller.join(); return 0;
}
