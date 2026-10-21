#pragma once

namespace ml
{
// A VST3 may expose its main output disabled at creation or state restore.
// Activate only that bus, preserving every already-active layout and aux bus.
// Let the plugin negotiate its advertised last layout first; the caller's
// supported fallback is offered only when that leaves the main bus disabled.
template <class Processor, class Layout>
bool activateMainOutput(Processor& processor, const Layout& fallback)
{
    auto* output = processor.getBus(false, 0);
    if (output == nullptr || output->isEnabled())
        return true;
    if (output->enable(true) && output->isEnabled())
        return true;
    return processor.setChannelLayoutOfBus(false, 0, fallback) && output->isEnabled();
}
}
