#pragma once
#include <atomic>
#include <array>
#include <cstdint>
#include <cstddef>

namespace mlengine {
constexpr uint32_t nodeProtocol=2,nodeMaxFrames=4096,nodeMaxPlugins=64;
// One request owns the inputs until completedSequence acknowledges its output.
// Audio is planar float32 immediately after this aligned header.
struct alignas(64) NodeHeader {
    uint32_t protocol=nodeProtocol,headerBytes=0,capacity=0,sideSlots=0;
    double rate=0;
    std::atomic<uint64_t> requestedSequence{0},completedSequence{0};
    std::atomic<uint64_t> programEpoch{1},appliedEpoch{0};
    std::array<std::atomic<uint64_t>,4> configHash{};
    std::atomic<uint32_t> status{0},quit{0},stage{0};
    std::atomic<uint64_t> renderMicros{0},loadMicros{0};
    std::atomic<uint64_t> chaseCount{0};
    std::atomic<int32_t> latency{0};
    std::array<std::atomic<int32_t>,nodeMaxPlugins> pluginLatency{};
    int64_t sample=0,origin=0,inputDelay=0,loopStart=0,loopEnd=0;
    uint32_t frames=0,chase=0,looping=0;
    char error[512]{};
};
static_assert(std::atomic<uint64_t>::is_always_lock_free);
static_assert(sizeof(NodeHeader)%64==0);
inline size_t nodeBytes(uint32_t frames,uint32_t sideSlots){return sizeof(NodeHeader)+(size_t)(sideSlots+2)*2*frames*sizeof(float);}
inline float* nodeChannel(NodeHeader* shared,uint32_t group,uint32_t channel){
    return reinterpret_cast<float*>(reinterpret_cast<char*>(shared)+shared->headerBytes)+(size_t)(group*2+channel)*shared->capacity;
}
}
