#include <juce_audio_basics/juce_audio_basics.h>
#include "MidiDeliveryDiagnostics.h"
#include <cmath>
#include <iostream>
#include <limits>

int main() {
    mlengine::PluginRing shared{};
    juce::MidiBuffer midi;
    midi.addEvent(juce::MidiMessage::noteOn(1, 48, 0.35f), 7);
    midi.addEvent(juce::MidiMessage::controllerEvent(1, 64, 127), 73);
    midi.addEvent(juce::MidiMessage::noteOff(1, 48), 511);
    ml::recordMidiDelivery(&shared, 2, 44100, 512, midi);

    const auto& diagnostics = shared.midiDelivery;
    if (diagnostics.processBlockCalls.load() != 1
        || diagnostics.eventBearingBlocks.load() != 1
        || diagnostics.eventsSubmitted.load() != 3
        || diagnostics.recordsPublished.load() != 3
        || diagnostics.recordsDropped.load() != 0) {
        std::cerr << "MIDI block/event counts differ\n";
        return 1;
    }
    const auto& noteOn = diagnostics.records[0];
    const auto& controller = diagnostics.records[1];
    const auto& noteOff = diagnostics.records[2];
    if (noteOn.sequence.load() != 1 || noteOn.messageType != 1 || noteOn.data1 != 48
        || noteOn.channel != 1 || noteOn.sampleOffset != 7 || noteOn.sourceStartSample != 44100
        || noteOn.blockSamples != 512 || noteOn.pluginIndex != 2
        || std::abs(noteOn.velocity - (44.0f / 127.0f)) > 1e-6f) {
        std::cerr << "Note-on receipt differs: seq=" << noteOn.sequence.load()
                  << " type=" << (int)noteOn.messageType << " pitch=" << noteOn.data1
                  << " channel=" << (int)noteOn.channel << " offset=" << noteOn.sampleOffset
                  << " source=" << noteOn.sourceStartSample << " block=" << noteOn.blockSamples
                  << " plugin=" << noteOn.pluginIndex << " velocity=" << noteOn.velocity << "\n";
        return 2;
    }
    if (controller.sequence.load() != 2 || controller.messageType != 3
        || controller.data1 != 64 || controller.data2 != 127 || controller.sampleOffset != 73) {
        std::cerr << "Controller receipt differs\n";
        return 3;
    }
    if (noteOff.sequence.load() != 3 || noteOff.messageType != 2 || noteOff.data1 != 48
        || noteOff.sampleOffset != 511) {
        std::cerr << "Note-off receipt differs\n";
        return 4;
    }

    juce::AudioBuffer<float> audio(2, 512);
    audio.clear();
    audio.setSample(0, 11, 0.5f);
    audio.setSample(1, 12, -0.25f);
    audio.setSample(0, 13, std::numeric_limits<float>::quiet_NaN());
    audio.setSample(1, 14, 0.125f);
    ml::recordProcessBlockAudio(&shared, 1, 2, 44100, audio);
    const auto& audioBlock = diagnostics.audioBlocks[0];
    const auto expectedRms = std::sqrt((0.25 + 0.0625 + 0.015625) / (1024.0 - 1.0));
    if (diagnostics.audioBlocksPublished.load() != 1 || audioBlock.sequence.load() != 1
        || audioBlock.processBlock != 1 || audioBlock.sourceStartSample != 44100
        || audioBlock.blockSamples != 512 || audioBlock.pluginIndex != 2
        || audioBlock.nonzeroSamples != 3 || audioBlock.nonfiniteSamples != 1
        || audioBlock.firstNonzeroOffset != 11 || audioBlock.lastNonzeroOffset != 14
        || std::abs(audioBlock.peak - 0.5f) > 1e-6f
        || std::abs(audioBlock.rms - expectedRms) > 1e-6f) {
        std::cerr << "Post-processBlock audio receipt differs\n";
        return 7;
    }

    juce::AudioBuffer<float> silence(2, 512);
    silence.clear();
    ml::recordProcessBlockAudio(&shared, 2, 2, 44612, silence);
    const auto& silentBlock = diagnostics.audioBlocks[1];
    if (silentBlock.nonzeroSamples != 0 || silentBlock.firstNonzeroOffset != -1
        || silentBlock.lastNonzeroOffset != -1 || silentBlock.peak != 0.0f
        || silentBlock.rms != 0.0f) {
        std::cerr << "Silent processBlock offsets were not represented as empty\n";
        return 8;
    }

    juce::MidiBuffer empty;
    ml::recordMidiDelivery(&shared, 2, 44612, 512, empty);
    if (diagnostics.processBlockCalls.load() != 2 || diagnostics.eventBearingBlocks.load() != 1) {
        std::cerr << "Empty processBlock call was not counted independently\n";
        return 5;
    }

    juce::MidiBuffer overflow;
    for (int i = 0; i < static_cast<int>(mlengine::midiDeliveryRecordCapacity) + 4; ++i)
        overflow.addEvent(juce::MidiMessage::noteOn(1, i % 120, 0.5f), i % 512);
    ml::recordMidiDelivery(&shared, 2, 45124, 512, overflow);
    if (diagnostics.processBlockCalls.load() != 3
        || diagnostics.eventBearingBlocks.load() != 2
        || diagnostics.eventsSubmitted.load() != 3 + mlengine::midiDeliveryRecordCapacity + 4
        || diagnostics.recordsPublished.load() != mlengine::midiDeliveryRecordCapacity
        || diagnostics.recordsDropped.load() != 7) {
        std::cerr << "Bounded ring overflow was not reported: submitted="
                  << diagnostics.eventsSubmitted.load() << " published="
                  << diagnostics.recordsPublished.load() << " dropped="
                  << diagnostics.recordsDropped.load() << "\n";
        return 6;
    }

    if (mlengine::midiDeliveryAudioCapacity != 4096
        || mlengine::midiDeliveryRecordCapacity != 512) {
        std::cerr << "Diagnostic capacities differ from the build08 contract\n";
        return 9;
    }
    for (uint64_t block = 3; block <= 1366; ++block)
        ml::recordProcessBlockAudio(&shared, block, 0,
            static_cast<int64_t>((block - 1) * 512), silence);
    if (diagnostics.audioBlocksPublished.load() != 1366
        || mlengine::midiDeliveryAudioBlockCount(diagnostics) != 1366
        || diagnostics.audioBlocks[1365].sequence.load() != 1366
        || diagnostics.audioBlocks[1365].processBlock != 1366
        || diagnostics.audioBlocksDropped.load() != 0) {
        std::cerr << "Audio producer/consumer lost a 1366-block trace\n";
        return 10;
    }
    for (uint64_t block = 1367; block <= 4097; ++block)
        ml::recordProcessBlockAudio(&shared, block, 0,
            static_cast<int64_t>((block - 1) * 512), silence);
    if (diagnostics.audioBlocksPublished.load() != 4096
        || mlengine::midiDeliveryAudioBlockCount(diagnostics) != 4096
        || diagnostics.audioBlocks[4095].sequence.load() != 4096
        || diagnostics.audioBlocks[4095].processBlock != 4096
        || diagnostics.audioBlocksDropped.load() != 1) {
        std::cerr << "Audio ring failed its exact-capacity overflow boundary\n";
        return 11;
    }
    return 0;
}
