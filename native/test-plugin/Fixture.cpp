#include <juce_audio_processors/juce_audio_processors.h>
#include <array>
#include <chrono>
#include <cstdlib>
#include <thread>

namespace
{
struct StartupProbe
{
    StartupProbe()
    {
        if (const auto* mode = std::getenv("ML_FIXTURE_STARTUP"))
        {
            if (std::string(mode) == "crash") std::abort();
            if (std::string(mode) == "hang") std::this_thread::sleep_for(std::chrono::minutes(10));
        }
    }
} startupProbe;
}

// A deterministic *real VST3* fixture, never a substitute for third-party tests.
class Fixture final : public juce::AudioProcessor
{
public:
    Fixture() : AudioProcessor(BusesProperties()
        .withInput("Input", juce::AudioChannelSet::stereo())
        .withInput("Sidechain", juce::AudioChannelSet::stereo(), false)
        .withOutput("Output", juce::AudioChannelSet::stereo()))
    {
        addParameter(gain = new juce::AudioParameterFloat({"gain", 1}, "Gain", juce::NormalisableRange<float>(0.0f, 1.0f, 0.0f), 0.5f));
        addParameter(mode = new juce::AudioParameterChoice({"mode", 1}, "Mode",
            juce::StringArray{"pass", "midi-gate", "transport", "crash", "hang"}, 0));
        addParameter(sidechainGain = new juce::AudioParameterFloat({"sidechain_gain", 1}, "Sidechain Gain", 0.0f, 1.0f, 0.0f));
        addParameter(ppqProbe = new juce::AudioParameterBool({"ppq_probe", 1}, "PPQ Probe", false));
        setLatencySamples(delaySamples);
    }

    void prepareToPlay(double, int) override
    {
        for (auto& channel : delay) channel.fill(0.0f);
        position = 0;
        gate = false;
    }
    void releaseResources() override {}
    void processBlock(juce::AudioBuffer<float>& audio, juce::MidiBuffer& midi) override
    {
        if (mode->getIndex() == 3) std::abort();
        if (mode->getIndex() == 4) std::this_thread::sleep_for(std::chrono::minutes(10));
        double bpm = -1;
        double ppq = 0;
        double barStart = 0;
        int numerator = -1;
        if (const auto* playHead = getPlayHead())
            if (const auto info = playHead->getPosition())
            {
                bpm = info->getBpm().orFallback(-1);
                ppq = info->getPpqPosition().orFallback(0);
                barStart = info->getPpqPositionOfLastBarStart().orFallback(0);
                if (const auto meter = info->getTimeSignature()) numerator = meter->numerator;
            }
        for (int sample = 0; sample < audio.getNumSamples(); ++sample)
        {
            for (const auto event : midi)
                if (event.samplePosition == sample)
                {
                    if (event.getMessage().isNoteOn()) gate = true;
                    if (event.getMessage().isNoteOff()) gate = false;
                }
            for (int channel = 0; channel < 2; ++channel)
            {
                auto input = audio.getSample(channel, sample);
                if (audio.getNumChannels() == 4) input += audio.getSample(channel + 2, sample) * sidechainGain->get();
                if (mode->getIndex() == 1) input = gate ? 0.25f : 0.0f;
                if (mode->getIndex() == 2) input = static_cast<float>(channel == 0 ? bpm / 1000.0 : numerator / 100.0);
                if (ppqProbe->get()) input = static_cast<float>(channel == 0 ? ppq : barStart);
                auto& queue = delay[static_cast<size_t>(channel)];
                const auto output = queue[static_cast<size_t>(position)];
                queue[static_cast<size_t>(position)] = input * gain->get();
                audio.setSample(channel, sample, output);
            }
            position = (position + 1) % delaySamples;
        }
    }
    bool isBusesLayoutSupported(const BusesLayout& layout) const override
    {
        return layout.getMainInputChannelSet() == juce::AudioChannelSet::stereo()
            && layout.getMainOutputChannelSet() == juce::AudioChannelSet::stereo()
            && (layout.inputBuses.size() < 2 || layout.inputBuses[1].isDisabled()
                || layout.inputBuses[1] == juce::AudioChannelSet::stereo());
    }
    juce::AudioProcessorEditor* createEditor() override { return nullptr; }
    bool hasEditor() const override { return false; }
    const juce::String getName() const override { return "MLFixture"; }
    bool acceptsMidi() const override { return true; }
    bool producesMidi() const override { return false; }
    double getTailLengthSeconds() const override { return 0.0; }
    int getNumPrograms() override { return 1; }
    int getCurrentProgram() override { return 0; }
    void setCurrentProgram(int) override {}
    const juce::String getProgramName(int) override { return "Default"; }
    void changeProgramName(int, const juce::String&) override {}
    void getStateInformation(juce::MemoryBlock& state) override
    {
        juce::MemoryOutputStream stream(state, false);
        stream.writeFloat(gain->get());
        stream.writeInt(mode->getIndex());
        stream.writeFloat(sidechainGain->get());
        stream.writeBool(ppqProbe->get());
    }
    void setStateInformation(const void* data, int size) override
    {
        if (size != 8 && size != 13) return;
        juce::MemoryInputStream stream(data, static_cast<size_t>(size), false);
        gain->setValueNotifyingHost(stream.readFloat());
        const auto value = stream.readInt();
        mode->setValueNotifyingHost(mode->convertTo0to1(value));
        if (size == 13)
        {
            sidechainGain->setValueNotifyingHost(stream.readFloat());
            ppqProbe->setValueNotifyingHost(stream.readBool() ? 1.0f : 0.0f);
        }
    }
private:
    static constexpr int delaySamples = 128;
    std::array<std::array<float, delaySamples>, 2> delay{};
    int position = 0;
    bool gate = false;
    juce::AudioParameterFloat* gain;
    juce::AudioParameterChoice* mode;
    juce::AudioParameterFloat* sidechainGain;
    juce::AudioParameterBool* ppqProbe;
};

juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter() { return new Fixture; }
