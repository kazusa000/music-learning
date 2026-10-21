cmake_minimum_required(VERSION 3.22)
project(MusicLearningEngine VERSION 1.0 LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
get_filename_component(ML_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)
set(JUCE_ROOT "${ML_ROOT}/runtime/third-party/JUCE" CACHE PATH "Pinned JUCE sources")
set(ML_RUNTIME_LIBRARY "" CACHE FILEPATH "Validated immutable host runtime")
if(NOT EXISTS "${ML_RUNTIME_LIBRARY}")
    message(FATAL_ERROR "Use scripts/build_engine.py to select and verify the pinned runtime")
endif()
add_executable(ml_engine Main.cpp "${JUCE_ROOT}/modules/juce_audio_devices/juce_audio_devices.cpp"
    "${JUCE_ROOT}/modules/juce_audio_formats/juce_audio_formats.cpp"
    "${JUCE_ROOT}/modules/juce_audio_formats/juce_audio_formats_flac_1.c"
    "${JUCE_ROOT}/modules/juce_audio_formats/juce_audio_formats_flac_2.c"
    "${JUCE_ROOT}/modules/juce_audio_formats/juce_audio_formats_ogg.c")
target_include_directories(ml_engine PRIVATE "${JUCE_ROOT}/modules")
target_compile_definitions(ml_engine PRIVATE
    JUCE_GLOBAL_MODULE_SETTINGS_INCLUDED=1 JUCE_MODAL_LOOPS_PERMITTED=1
    JUCE_WEB_BROWSER=0 JUCE_USE_CURL=0 JUCE_WASAPI=1 JUCE_ASIO=0
    JUCE_DIRECTSOUND=0 JUCE_USE_WINRT_MIDI=0 JUCE_USE_FLAC=1
    JUCE_USE_OGGVORBIS=1 JUCE_USE_MP3AUDIOFORMAT=1 JUCE_USE_WINDOWS_MEDIA_FORMAT=0 NOMINMAX)
target_compile_options(ml_engine PRIVATE /utf-8 /EHsc /W4)
target_link_libraries(ml_engine PRIVATE "${ML_RUNTIME_LIBRARY}"
    kernel32 user32 gdi32 winspool shell32 ole32 oleaut32 uuid comdlg32 advapi32 winmm avrt cfgmgr32)
set_target_properties(ml_engine PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
add_executable(ml_engine_loopback Loopback.cpp)
target_compile_options(ml_engine_loopback PRIVATE /utf-8 /EHsc /W4)
target_link_libraries(ml_engine_loopback PRIVATE ole32 uuid avrt)
set_target_properties(ml_engine_loopback PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
add_executable(ml_plugin_worker "${CMAKE_CURRENT_SOURCE_DIR}/../plugin-worker/Main.cpp")
target_include_directories(ml_plugin_worker PRIVATE "${JUCE_ROOT}/modules" "${ML_ROOT}/native/include")
target_compile_definitions(ml_plugin_worker PRIVATE JUCE_GLOBAL_MODULE_SETTINGS_INCLUDED=1
    JUCE_MODAL_LOOPS_PERMITTED=1 JUCE_PLUGINHOST_VST3=1 JUCE_WEB_BROWSER=0 JUCE_USE_CURL=0
    JUCE_USE_FLAC=0 JUCE_USE_OGGVORBIS=0 JUCE_USE_MP3AUDIOFORMAT=0 JUCE_USE_WINDOWS_MEDIA_FORMAT=0)
target_compile_options(ml_plugin_worker PRIVATE /utf-8 /EHsc /W4)
target_link_libraries(ml_plugin_worker PRIVATE "${ML_RUNTIME_LIBRARY}"
    kernel32 user32 gdi32 winspool shell32 ole32 oleaut32 uuid comdlg32 advapi32)
set_target_properties(ml_plugin_worker PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
