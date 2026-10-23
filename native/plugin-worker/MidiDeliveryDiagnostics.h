#pragma once

#include "../engine/SharedPlugin.h"
#include <algorithm>
#include <cmath>

#if defined(ML_ENABLE_MIDI_DELIVERY_DIAGNOSTICS)
namespace ml {
inline uint8_t midiDeliveryType(const juce::MidiMessage& message) noexcept {
    if (message.isNoteOn()) return 1;
    if (message.isNoteOff()) return 2;
    if (message.isController()) return 3;
    if (message.isProgramChange()) return 4;
    if (message.isPitchWheel()) return 5;
    return 0;
}

// Called immediately before AudioProcessor::processBlock. The ring is private
// to the diagnostic engine/worker pair; the audio worker remains the sole writer.
inline uint64_t recordMidiDelivery(mlengine::PluginRing* shared, uint32_t pluginIndex,
                                   int64_t sourceStartSample, uint32_t blockSamples,
                                   const juce::MidiBuffer& midi) noexcept {
    if (shared == nullptr) return 0;
    auto& diagnostics = shared->midiDelivery;
    const auto blockOrdinal = diagnostics.processBlockCalls.fetch_add(1, std::memory_order_relaxed) + 1;
    if (midi.isEmpty()) return blockOrdinal;
    diagnostics.eventBearingBlocks.fetch_add(1, std::memory_order_relaxed);
    for (const auto metadata : midi) {
        const auto message = metadata.getMessage();
        const auto sampleOffset = metadata.samplePosition;
        diagnostics.eventsSubmitted.fetch_add(1, std::memory_order_relaxed);
        const auto index = diagnostics.recordsPublished.load(std::memory_order_relaxed);
        if (index >= mlengine::midiDeliveryRecordCapacity) {
            diagnostics.recordsDropped.fetch_add(1, std::memory_order_relaxed);
            continue;
        }
        auto& record = diagnostics.records[static_cast<size_t>(index)];
        record.processBlock = blockOrdinal;
        record.sourceStartSample = sourceStartSample;
        record.blockSamples = blockSamples;
        record.sampleOffset = static_cast<uint32_t>(sampleOffset);
        record.pluginIndex = pluginIndex;
        record.messageType = midiDeliveryType(message);
        record.channel = static_cast<uint8_t>(message.getChannel());
        record.data1 = 0;
        record.data2 = 0;
        record.velocity = 0.0f;
        if (message.isNoteOn() || message.isNoteOff()) {
            record.data1 = static_cast<int16_t>(message.getNoteNumber());
            record.velocity = message.getFloatVelocity();
        } else if (message.isController()) {
            record.data1 = static_cast<int16_t>(message.getControllerNumber());
            record.data2 = static_cast<int16_t>(message.getControllerValue());
        } else if (message.isProgramChange()) {
            record.data1 = static_cast<int16_t>(message.getProgramChangeNumber());
        } else if (message.isPitchWheel()) {
            record.data1 = static_cast<int16_t>(message.getPitchWheelValue());
        }
        record.sequence.store(index + 1, std::memory_order_release);
        diagnostics.recordsPublished.store(index + 1, std::memory_order_release);
    }
    return blockOrdinal;
}

inline void recordProcessBlockAudio(mlengine::PluginRing* shared, uint64_t blockOrdinal,
                                    uint32_t pluginIndex, int64_t sourceStartSample,
                                    const juce::AudioBuffer<float>& audio) noexcept {
    if (shared == nullptr || blockOrdinal == 0) return;
    auto& diagnostics = shared->midiDelivery;
    const auto index = diagnostics.audioBlocksPublished.load(std::memory_order_relaxed);
    if (index >= mlengine::midiDeliveryRecordCapacity) {
        diagnostics.audioBlocksDropped.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    auto& record = diagnostics.audioBlocks[static_cast<size_t>(index)];
    const auto channels = std::min(2, audio.getNumChannels());
    const auto samples = audio.getNumSamples();
    double sumSquares = 0.0;
    uint32_t nonzero = 0, nonfinite = 0;
    int32_t firstNonzeroOffset = -1, lastNonzeroOffset = -1;
    float peak = 0.0f;
    for (int channel = 0; channel < channels; ++channel) {
        const auto* data = audio.getReadPointer(channel);
        for (int sample = 0; sample < samples; ++sample) {
            const auto value = data[sample];
            if (!std::isfinite(value)) {
                ++nonfinite;
                continue;
            }
            const auto magnitude = std::abs(value);
            peak = std::max(peak, magnitude);
            if (value != 0.0f) {
                ++nonzero;
                if (firstNonzeroOffset < 0) firstNonzeroOffset = sample;
                lastNonzeroOffset = sample;
            }
            sumSquares += static_cast<double>(value) * static_cast<double>(value);
        }
    }
    const auto valid = static_cast<uint32_t>(channels * samples) - nonfinite;
    record.processBlock = blockOrdinal;
    record.sourceStartSample = sourceStartSample;
    record.blockSamples = static_cast<uint32_t>(samples);
    record.pluginIndex = pluginIndex;
    record.nonzeroSamples = nonzero;
    record.nonfiniteSamples = nonfinite;
    record.firstNonzeroOffset = firstNonzeroOffset;
    record.lastNonzeroOffset = lastNonzeroOffset;
    record.peak = peak;
    record.rms = valid == 0 ? 0.0f : static_cast<float>(std::sqrt(sumSquares / valid));
    record.sequence.store(index + 1, std::memory_order_release);
    diagnostics.audioBlocksPublished.store(index + 1, std::memory_order_release);
}
} // namespace ml
#endif
