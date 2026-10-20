# Reproducible, narrowly pinned host-side patch; never patch plugin binaries.
set(_vst3_impl "${JUCE_ROOT}/modules/juce_audio_processors_headless/format_types/juce_VST3PluginFormatImpl.h")
file(READ "${_vst3_impl}" _vst3_source)
string(REPLACE "\r\n" "\n" _vst3_source "${_vst3_source}")
string(SHA256 _vst3_sha "${_vst3_source}")
if(_vst3_sha STREQUAL "3a009ce8ec595b96480f095ac75ef44460106bbb34eb07b8512c985bea3e2d94")
    string(REPLACE "#pragma once" "#pragma once\n#include \"Vst3BusInfo.h\"" _vst3_source "${_vst3_source}")
    string(REPLACE "holder->component->getBusInfo ("
        "music_learning_vst3::readBusInfo (holder->component, " _vst3_source "${_vst3_source}")
    string(REPLACE "component->getBusInfo ("
        "music_learning_vst3::readBusInfo (component, " _vst3_source "${_vst3_source}")
    string(SHA256 _vst3_patched_sha "${_vst3_source}")
    if(NOT _vst3_patched_sha STREQUAL "69e762fa2b9331f1f859bdbd2dcd9e52996a19ed0eee44d95ba0195598ed8cce")
        message(FATAL_ERROR "Unexpected JUCE bus-info patch output")
    endif()
    file(WRITE "${_vst3_impl}" "${_vst3_source}")
elseif(NOT _vst3_sha STREQUAL "69e762fa2b9331f1f859bdbd2dcd9e52996a19ed0eee44d95ba0195598ed8cce")
    message(FATAL_ERROR "JUCE host source changed; review the pinned bus-info patch before building")
endif()
