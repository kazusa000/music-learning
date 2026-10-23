#pragma once

#include "../audio-host/Host.h"
#include "AmpleProbe.h"
#include "PumpCadenceDiagnostics.h"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>

namespace ml::pump_cadence_runtime
{

inline juce::var stereo_stats(const juce::AudioBuffer<float>& buffer, int samples)
{
    double peak = 0.0;
    double sum_squares = 0.0;
    std::uint64_t exact_nonzero = 0;
    for (int channel = 0; channel < std::min(2, buffer.getNumChannels()); ++channel)
        for (int index = 0; index < samples; ++index)
        {
            const auto value = static_cast<double>(buffer.getSample(channel, index));
            peak = std::max(peak, std::abs(value));
            sum_squares += value * value;
            if (value != 0.0) ++exact_nonzero;
        }
    const auto denominator = static_cast<double>(std::max(1, samples)
        * std::max(1, std::min(2, buffer.getNumChannels())));
    return object({{"channels_observed", std::min(2, buffer.getNumChannels())},
                   {"samples_per_channel", samples}, {"peak", peak},
                   {"rms", std::sqrt(sum_squares / denominator)},
                   {"exact_nonzero_samples", static_cast<juce::int64>(exact_nonzero)}});
}

inline juce::var midi_events(const juce::MidiBuffer& midi,
                             std::size_t maximum_events = 64)
{
    juce::Array<juce::var> events;
    std::size_t observed = 0;
    for (const auto metadata : midi)
    {
        ++observed;
        if (events.size() >= static_cast<int>(maximum_events)) continue;
        const auto& message = metadata.getMessage();
        juce::String raw;
        for (int index = 0; index < message.getRawDataSize(); ++index)
            raw += juce::String::toHexString(
                static_cast<int>(static_cast<unsigned char>(message.getRawData()[index]))).paddedLeft('0', 2);
        events.add(object({{"sample_offset", metadata.samplePosition},
                           {"channel", message.getChannel()},
                           {"is_note_on", message.isNoteOn()},
                           {"is_note_off", message.isNoteOff()},
                           {"note_number", message.isNoteOnOrOff() ? message.getNoteNumber() : -1},
                           {"velocity", message.isNoteOnOrOff() ? message.getFloatVelocity() : 0.0f},
                           {"is_controller", message.isController()},
                           {"controller_number", message.isController() ? message.getControllerNumber() : -1},
                           {"controller_value", message.isController() ? message.getControllerValue() : -1},
                           {"raw_hex", raw}}));
    }
    return object({{"events", events}, {"observed_count", static_cast<juce::int64>(observed)},
                   {"omitted_count", static_cast<juce::int64>(observed - static_cast<std::size_t>(events.size()))},
                   {"maximum_serialized_events", static_cast<juce::int64>(maximum_events)}});
}

inline juce::var readiness(juce::AudioPluginInstance& plugin, const char* phase)
{
    return ampleReadiness(plugin, phase);
}

} // namespace ml::pump_cadence_runtime
