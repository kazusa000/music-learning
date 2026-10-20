#include "Vst3BusInfo.h"
#include <algorithm>
#include <string>
#include <cstdlib>

struct Info
{
    int channels;
    char16_t name[128];
};

struct Component
{
    std::u16string label;
    int status = 0;
    int getBusInfo(int media, int direction, int index, Info& info)
    {
        if (media != 7 || direction != 8 || index != 9) std::abort();
        // Deliberately reproduce the plugin's missing terminator.
        std::copy(label.begin(), label.end(), info.name);
        info.channels = 2;
        return status;
    }
};

int main()
{
    Info info;
    std::fill(std::begin(info.name), std::end(info.name), char16_t(0xd834));
    Component component {u"A Output Audio Bus"};
    const auto read = [&] { return music_learning_vst3::readBusInfo(&component, 7, 8, 9, info); };
    if (read() != 0 || info.channels != 2 || std::u16string(info.name) != component.label) return 1;
    component.label = u"\u94a2\u7434 \U0001d11e";
    if (read() != 0 || std::u16string(info.name) != component.label) return 2;
    component.label.assign(128, u'x');
    if (read() != 0 || std::u16string(info.name).size() != 127) return 3;
    component.label.replace(126, 2, u"\U0001d11e");
    if (read() != 0 || std::u16string(info.name).size() != 126) return 4;
    component.status = -1;
    if (read() != -1) return 5; // A failed SDK call must stay failed.
}
