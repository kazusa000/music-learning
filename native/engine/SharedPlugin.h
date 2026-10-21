#pragma once
#include <atomic>
#include <cstdint>
#include <array>

namespace mlengine {
constexpr uint32_t pluginProtocol = 1;
constexpr uint32_t pluginCapacity = 8;
constexpr uint32_t pluginMaxFrames = 4096;
static_assert(std::atomic<uint64_t>::is_always_lock_free);
static_assert(std::atomic<double>::is_always_lock_free);

// Same compiler/architecture in both native processes. Session identity and
// graph metadata are established before the child opens this private mapping.
struct alignas(64) PluginSlot {
    std::atomic<uint64_t> epoch{0}, sequence{0};
    std::array<float, pluginMaxFrames * 2> pcm{};
};
struct alignas(64) PluginRing {
    uint32_t protocol = pluginProtocol, frames = 0;
    double deviceRate = 0, sourceRate = 0;
    std::atomic<uint64_t> requestedEpoch{1}, readyEpoch{0}, consumed{0}, produced{0};
    std::atomic<int64_t> origin{0}, loopStart{0}, loopEnd{0};
    std::atomic<uint32_t> looping{0}, status{0}, quit{0}; // 0 loading, 1 ready, 2 failed
    std::atomic<uint64_t> renderMicros{0};
    std::atomic<uint64_t> loadMicros{0};
    std::atomic<uint32_t> loadStage{0};
    std::array<std::atomic<uint64_t>, 4> configHash{};
    char error[512]{};
    std::array<PluginSlot, pluginCapacity> slots;
};
}
