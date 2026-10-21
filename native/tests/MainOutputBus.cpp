#include "../include/MainOutputBus.h"
#include <cstdlib>

static void check(bool value) { if (!value) std::abort(); }

struct Bus
{
    bool active = false, enables = true;
    int calls = 0;
    bool isEnabled() const { return active; }
    bool enable(bool value) { ++calls; active = enables && value; return active; }
};
struct Processor
{
    Bus main;
    bool present = true, accepts = true;
    int negotiations = 0;
    Bus* getBus(bool input, int index) { check(!input && index == 0); return present ? &main : nullptr; }
    bool setChannelLayoutOfBus(bool input, int index, int layout)
    {
        check(!input && index == 0 && layout == 2);
        ++negotiations; main.active = accepts; return accepts;
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
    check(ml::activateMainOutput(none, 2) && none.negotiations == 0);
}
