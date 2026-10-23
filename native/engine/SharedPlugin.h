#pragma once
#include <atomic>
#include <cstdint>
#include <array>

namespace mlengine {
constexpr uint32_t pluginProtocol = 1;
constexpr uint32_t pluginCapacity = 8;
constexpr uint32_t pluginMaxFrames = 4096;
#if defined(ML_ENABLE_MIDI_DELIVERY_DIAGNOSTICS)
constexpr uint32_t midiDeliveryRecordCapacity = 512;
struct MidiDeliveryRecord {
    std::atomic<uint64_t> sequence{0};
    uint64_t processBlock = 0;
    int64_t sourceStartSample = 0;
    uint32_t blockSamples = 0, sampleOffset = 0, pluginIndex = 0;
    uint8_t messageType = 0, channel = 0;
    int16_t data1 = 0, data2 = 0;
    float velocity = 0;
};
struct ProcessBlockAudioRecord {
    std::atomic<uint64_t> sequence{0};
    uint64_t processBlock = 0;
    int64_t sourceStartSample = 0;
    uint32_t blockSamples = 0, pluginIndex = 0;
    uint32_t nonzeroSamples = 0, nonfiniteSamples = 0;
    int32_t firstNonzeroOffset = -1, lastNonzeroOffset = -1;
    float peak = 0, rms = 0;
};
constexpr uint32_t ampleReadinessRecordCapacity = 8;
struct AmpleReadinessRecord {
    std::atomic<uint64_t> sequence{0};
    int64_t sourceSample = 0;
    uint32_t pluginIndex = 0, phase = 0;
    int32_t fieldsRead = 0, ready = 0, loadingByteRead = 0;
    int32_t loaderErrorCountRead = 0, statusRead = 0;
    int32_t loadingByte = -1, loaderErrorCount = -1;
    uint64_t componentVtable = 0, processorSubobject = 0;
    uint64_t processorSubobjectVtable = 0, primaryProcessor = 0;
    char product[64]{};
    char version[32]{};
    char modulePath[512]{};
    char moduleSha256[65]{};
    char status[48]{};
    char statusString[96]{};
};
struct MidiDeliveryDiagnostics {
    std::atomic<uint64_t> processBlockCalls{0}, eventBearingBlocks{0};
    std::atomic<uint64_t> eventsSubmitted{0}, recordsPublished{0}, recordsDropped{0};
    std::atomic<uint64_t> audioBlocksPublished{0}, audioBlocksDropped{0};
    std::atomic<uint64_t> ampleReadinessPublished{0}, ampleReadinessDropped{0};
    std::array<MidiDeliveryRecord, midiDeliveryRecordCapacity> records;
    std::array<ProcessBlockAudioRecord, midiDeliveryRecordCapacity> audioBlocks;
    std::array<AmpleReadinessRecord, ampleReadinessRecordCapacity> ampleReadiness;
};
#endif
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
#if defined(ML_ENABLE_MIDI_DELIVERY_DIAGNOSTICS)
    MidiDeliveryDiagnostics midiDelivery;
#endif
    std::array<std::atomic<uint64_t>, 4> configHash{};
    char error[512]{};
    std::array<PluginSlot, pluginCapacity> slots;
};
}
