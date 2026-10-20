#pragma once

namespace music_learning_vst3
{
// Some plugins fill the name characters but leave the remaining String128
// untouched. Never let a bus label read uninitialised stack storage.
template <typename Component, typename Media, typename Direction, typename Index, typename Info>
auto readBusInfo(const Component& component, Media media, Direction direction, Index index, Info& info)
{
    info = {};
    const auto status = component->getBusInfo(media, direction, index, info);
    constexpr auto length = sizeof(info.name) / sizeof(info.name[0]);
    static_assert(length >= 2);
    // Reserve the terminator without cutting a UTF-16 surrogate pair in half.
    if (info.name[length - 1] != 0
        && info.name[length - 2] >= 0xd800 && info.name[length - 2] <= 0xdbff)
        info.name[length - 2] = 0;
    info.name[length - 1] = 0;
    return status;
}
}
