#pragma once

namespace ml
{
// A VST3 may expose its main output disabled or restore a non-stereo layout.
// Explicitly negotiate the host-supported main layout while preserving inputs.
template <class Processor, class Layout>
bool activateMainOutput(Processor& processor, const Layout& fallback)
{
    auto* output = processor.getBus(false, 0);
    if (output == nullptr)
        return false;
    if (output->isEnabled() && output->getNumberOfChannels() == 2
        && output->getCurrentLayout() == fallback)
        return true;

    // A restored VST3 state may leave the main bus enabled with a surround or
    // discrete layout. Enabling an already-enabled bus is not a negotiation;
    // explicitly request the supported stereo layout in that case.
    if (!output->isEnabled())
        output->enable(true);
    if (output->isEnabled() && output->getNumberOfChannels() == 2
        && output->getCurrentLayout() == fallback)
        return true;
    return processor.setChannelLayoutOfBus(false, 0, fallback)
        && output->isEnabled() && output->getNumberOfChannels() == 2;
}

template <class Processor, class Layout>
bool hasSupportedStereoMainOutput(Processor& processor, const Layout& stereo,
                                  bool keepAuxiliaryOutputs = false)
{
    auto* output = processor.getBus(false, 0);
    if (output == nullptr || !output->isEnabled() || output->getNumberOfChannels() != 2
        || !(output->getCurrentLayout() == stereo))
        return false;
    const int total = processor.getTotalNumOutputChannels();
    // Keep the historical host contract: ordinary processors have two total
    // output channels; the explicitly recognized Keyscape multi-bus layout
    // may expose its existing 18-channel graph. Negotiating a stereo main
    // bus must not silently widen the renderer's supported output contract.
    return total == 2 || (keepAuxiliaryOutputs && total == 18);
}

// Re-select the output layout after plugin state/preset restoration, since a
// VST3 preset can re-enable auxiliary buses. The host mixes the main stereo
// bus; auxiliary outputs remain available only when explicitly requested by
// the caller. This is host routing state, not an instrument preset mutation.
template <class Processor, class Layout>
bool selectStereoMainOutput(Processor& processor, const Layout& fallback,
                            bool keepAuxiliaryOutputs = false)
{
    for (int index = 1; index < processor.getBusCount(false); ++index)
    {
        auto* bus = processor.getBus(false, index);
        if (bus == nullptr)
            return false;
        if (bus->isEnabled() != keepAuxiliaryOutputs && !bus->enable(keepAuxiliaryOutputs))
            return false;
    }

    if (!activateMainOutput(processor, fallback))
        return false;
    return hasSupportedStereoMainOutput(processor, fallback, keepAuxiliaryOutputs);
}

template <class Processor, class Layout>
bool configureStereoSidechainInput(Processor& processor, const Layout& stereo,
                                   bool enabled)
{
    if (enabled && processor.getBusCount(true) < 2)
        return false;

    for (int index = 1; index < processor.getBusCount(true); ++index)
    {
        auto* bus = processor.getBus(true, index);
        if (bus == nullptr)
            return false;
        const bool shouldEnable = enabled && index == 1;
        if (!shouldEnable)
        {
            if (bus->isEnabled() && !bus->enable(false))
                return false;
            continue;
        }

        if (!bus->isEnabled())
            bus->enable(true);
        if (bus->isEnabled() && bus->getNumberOfChannels() == 2
            && bus->getCurrentLayout() == stereo)
            continue;
        if (!processor.setChannelLayoutOfBus(true, index, stereo)
            || !bus->isEnabled() || bus->getNumberOfChannels() != 2)
            return false;
    }
    return true;
}
}
