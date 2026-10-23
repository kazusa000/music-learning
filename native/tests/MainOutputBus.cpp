#include "../include/MainOutputBus.h"
#include <cstdio>
#include <cstdlib>

static void checkAt(bool value, int line)
{
    if (!value)
    {
        std::fprintf(stderr, "MainOutputBus assertion failed at line %d\n", line);
        std::abort();
    }
}
#define check(value) checkAt((value), __LINE__)

struct Bus
{
    bool active = false, enables = true;
    int calls = 0, channels = 2, layout = 2;
    bool isEnabled() const { return active; }
    int getNumberOfChannels() const { return channels; }
    int getCurrentLayout() const { return layout; }
    bool enable(bool value)
    {
        ++calls;
        if (!enables)
            return false;
        active = value;
        return true;
    }
};
struct Processor
{
    Bus inputMain, inputSidechain, inputExtra, main, auxiliary, extraOutput;
    bool present = true, accepts = true, inputSidechainPresent = true;
    int negotiations = 0, outputBusCount = 3;
    Bus* getBus(bool input, int index)
    {
        if (input)
        {
            if (index == 0) return &inputMain;
            if (index == 1) return inputSidechainPresent ? &inputSidechain : nullptr;
            if (index == 2) return &inputExtra;
            return nullptr;
        }
        if (index == 0) return present ? &main : nullptr;
        if (index == 1) return &auxiliary;
        if (index == 2) return &extraOutput;
        return nullptr;
    }
    int getBusCount(bool input) const { return input ? 3 : outputBusCount; }
    int getTotalNumInputChannels() const
    {
        return (inputMain.active ? inputMain.channels : 0)
            + (inputSidechain.active ? inputSidechain.channels : 0)
            + (inputExtra.active ? inputExtra.channels : 0);
    }
    int getTotalNumOutputChannels() const
    {
        return (main.active ? main.channels : 0)
            + (auxiliary.active ? auxiliary.channels : 0)
            + (extraOutput.active ? extraOutput.channels : 0);
    }
    bool setChannelLayoutOfBus(bool input, int index, int layout)
    {
        check(index == 0 || (input && index == 1));
        check(layout == 2);
        ++negotiations;
        auto* bus = getBus(input, index);
        if (accepts && bus != nullptr)
        {
            bus->active = true;
            bus->channels = layout;
            bus->layout = layout;
        }
        return accepts;
    }
};
int main()
{
    Processor active; active.main.active = true;
    check(ml::activateMainOutput(active, 2) && active.main.calls == 0 && active.negotiations == 0);
    Processor disabled;
    check(ml::activateMainOutput(disabled, 2) && disabled.main.calls == 1 && disabled.negotiations == 0);
    Processor unset; unset.main.enables = false;
    check(ml::activateMainOutput(unset, 2) && unset.negotiations == 1);
    Processor rejects; rejects.main.enables = false; rejects.accepts = false;
    check(!ml::activateMainOutput(rejects, 2));
    Processor none; none.present = false;
    check(!ml::activateMainOutput(none, 2) && none.negotiations == 0);

    // A state-restored enabled main bus may still be surround/discrete; it
    // must negotiate the host's stereo layout instead of passing as-is.
    Processor restoredWideMain;
    restoredWideMain.main.active = true;
    restoredWideMain.main.channels = 6;
    check(ml::activateMainOutput(restoredWideMain, 2));
    check(restoredWideMain.negotiations == 1);

    Processor restoredMislabelledStereoMain;
    restoredMislabelledStereoMain.main.active = true;
    restoredMislabelledStereoMain.main.layout = 6;
    check(ml::activateMainOutput(restoredMislabelledStereoMain, 2));
    check(restoredMislabelledStereoMain.negotiations == 1);

    // A restored preset may turn on extra outputs after the initial host
    // layout was selected. The normal mixer selects its stereo main bus again.
    Processor presetWithAuxiliary;
    presetWithAuxiliary.main.active = true;
    presetWithAuxiliary.auxiliary.active = true;
    check(ml::selectStereoMainOutput(presetWithAuxiliary, 2));
    check(ml::hasSupportedStereoMainOutput(presetWithAuxiliary, 2));
    check(presetWithAuxiliary.getTotalNumOutputChannels() == 2);

    Processor rejectsAuxiliaryDisable;
    rejectsAuxiliaryDisable.main.active = true;
    rejectsAuxiliaryDisable.auxiliary.active = true;
    rejectsAuxiliaryDisable.auxiliary.enables = false;
    check(!ml::selectStereoMainOutput(rejectsAuxiliaryDisable, 2));
    check(rejectsAuxiliaryDisable.auxiliary.active);

    // An explicitly multi-output client keeps auxiliary outputs while still
    // requiring a valid stereo main bus.
    Processor multiOutput;
    multiOutput.outputBusCount = 2;
    multiOutput.main.active = true;
    multiOutput.auxiliary.active = true;
    multiOutput.auxiliary.channels = 16;
    check(ml::selectStereoMainOutput(multiOutput, 2, true));
    check(multiOutput.getTotalNumOutputChannels() == 18);

    Processor unsupportedExtraOutput;
    unsupportedExtraOutput.main.active = true;
    unsupportedExtraOutput.auxiliary.active = true;
    check(!ml::hasSupportedStereoMainOutput(unsupportedExtraOutput, 2));
    unsupportedExtraOutput.auxiliary.channels = 16;
    check(ml::hasSupportedStereoMainOutput(unsupportedExtraOutput, 2, true));

    Processor monoMain;
    monoMain.main.active = true;
    monoMain.main.channels = 1;
    monoMain.auxiliary.active = true;
    monoMain.accepts = false;
    check(!ml::selectStereoMainOutput(monoMain, 2));

    Processor tooManyOutputs;
    tooManyOutputs.main.active = true;
    tooManyOutputs.auxiliary.channels = 63;
    tooManyOutputs.auxiliary.active = true;
    check(!ml::hasSupportedStereoMainOutput(tooManyOutputs, 2));

    // Output negotiation must leave input-bus state alone; sidechain setup is
    // a separate decision based on the explicit project request.
    Processor sidechainPreserved;
    sidechainPreserved.main.active = true;
    sidechainPreserved.inputSidechain.active = true;
    sidechainPreserved.inputSidechain.channels = 2;
    check(ml::selectStereoMainOutput(sidechainPreserved, 2));
    check(sidechainPreserved.inputSidechain.active);
    check(ml::configureStereoSidechainInput(sidechainPreserved, 2, true));
    check(sidechainPreserved.getTotalNumInputChannels() == 2);
    check(ml::configureStereoSidechainInput(sidechainPreserved, 2, false));
    check(!sidechainPreserved.inputSidechain.active);

    Processor missingSidechain;
    missingSidechain.inputSidechainPresent = false;
    check(!ml::configureStereoSidechainInput(missingSidechain, 2, true));
}
