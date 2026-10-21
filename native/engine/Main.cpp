#include <juce_audio_devices/juce_audio_devices.h>
#include <windows.h>
#include <sddl.h>
#include <atomic>
#include <cmath>
#include <future>
#include <thread>
#include <vector>
#include "FileGraph.h"

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
    std::unique_ptr<mlengine::FileGraph> graph;
    std::atomic<bool> filePlaying{false};
    int generation = 0;
public:
    ~DeviceEngine() override { device.removeAudioCallback(this); device.closeAudioDevice(); }
    void audioDeviceAboutToStart(AudioIODevice* d) override { rate = d->getCurrentSampleRate(); }
    void audioDeviceStopped() override { remaining.store(0); }
    void audioDeviceIOCallbackWithContext(const float* const*, int, float* const* out,
        int channels, int frames, const AudioIODeviceCallbackContext&) override {
        for (int ch = 0; ch < channels; ++ch) if (out[ch]) FloatVectorOperations::clear(out[ch], frames);
        if (graph && filePlaying.load()) {
            float maximum = 0;
            graph->consume(out, channels, frames, maximum);
            if (graph->ended() || graph->failed.load()) filePlaying.store(false);
            peak.store(maximum); callbacks.fetch_add(1); return;
        }
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
        peak.store(maximum, std::memory_order_relaxed); callbacks.fetch_add(1, std::memory_order_relaxed);
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
        fields->setProperty("generation", generation);
        if (graph) {
            fields->setProperty("project_id", graph->manifest["project_id"]);
            fields->setProperty("revision_id", graph->manifest["revision_id"]);
            fields->setProperty("view", graph->manifest["view"]);
            fields->setProperty("playing", filePlaying.load());
            fields->setProperty("position_sample", (int64)jmin(graph->songDuration,graph->playedSample.load()));
            fields->setProperty("render_position_sample", (int64)graph->playedSample.load());
            fields->setProperty("source_sample_rate", graph->sourceRate);
            fields->setProperty("duration_samples", (int64)graph->songDuration);
            fields->setProperty("cache_bytes", graph->cacheBytes());
            fields->setProperty("consumed_device_frames", (int64)(graph->read.load()*graph->frames));
            fields->setProperty("underruns", (int64)graph->underruns.load());
            fields->setProperty("loop", graph->looping);
            fields->setProperty("monitor", graph->monitorValues());
            fields->setProperty("meters", graph->meterValues());
            fields->setProperty("file_error", graph->failed.load());
        }
        return result;
    }
    var command(const var& request) {
        const auto action = get(request, "action").toString();
        if(request.hasProperty("expected_generation") && (int)get(request,"expected_generation")!=generation)
            throw std::runtime_error("Engine generation changed");
        if (action == "status" || action == "hello") return status();
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
            toneCommand.store(-1); filePlaying.store(false); device.removeAudioCallback(this); graph.reset(); device.closeAudioDevice();
            device.setCurrentAudioDeviceType("Windows Audio", false);
            const auto error = device.initialise(0, 2, nullptr, true);
            if (error.isNotEmpty()) throw std::runtime_error(error.toStdString());
            AudioDeviceManager::AudioDeviceSetup setup; device.getAudioDeviceSetup(setup);
            const auto name = get(request, "device").toString(); if (name.isNotEmpty()) setup.outputDeviceName = name;
            setup.sampleRate = (double)get(request, "sample_rate", 48000.0);
            setup.bufferSize = (int)get(request, "buffer_frames", 512);
            const auto failure = device.setAudioDeviceSetup(setup, true);
            if (failure.isNotEmpty()) throw std::runtime_error(failure.toStdString());
            device.addAudioCallback(this); return status();
        }
        if (action == "open" || action == "transport") {
            auto* d = device.getCurrentAudioDevice(); if (!d) throw std::runtime_error("Configure an audio device first");
            if (action == "transport" && !graph) throw std::runtime_error("Open a project first");
            const bool play = (bool)get(request,"playing", action=="open" ? false : filePlaying.load());
            const bool replay = action=="transport" && play && graph->ended();
            const bool rebuild = action == "open" || request.hasProperty("position_sample") || request.hasProperty("loop")
                || request.hasProperty("loop_start_sample") || request.hasProperty("loop_end_sample") || replay;
            if (rebuild) {
                const auto value = action == "open" ? get(request,"manifest") : graph->manifest;
                const auto start = (double)get(request,"position_sample",graph && !replay ? graph->playedSample.load() : 0.0);
                const auto begin = (double)get(request,"loop_start_sample",graph ? graph->loopStart : 0.0);
                const auto end = (double)get(request,"loop_end_sample",graph ? graph->loopEnd : (double)value["duration_samples"]);
                const bool loop = (bool)get(request,"loop",graph ? graph->looping : false);
                auto ready = std::make_unique<mlengine::FileGraph>(value,d->getCurrentSampleRate(),d->getCurrentBufferSizeSamples(),start,loop,begin,end);
                if(action=="transport")ready->monitor(graph->monitorValues());
                filePlaying.store(false); device.removeAudioCallback(this);
                graph.swap(ready); ++generation; toneCommand.store(-1);
                device.addAudioCallback(this);
            }
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
        if (action == "stop") { filePlaying.store(false); toneCommand.store(-1); return status(); }
        if (action == "close") { filePlaying.store(false); toneCommand.store(-1); device.removeAudioCallback(this); graph.reset(); device.closeAudioDevice(); return status(); }
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
