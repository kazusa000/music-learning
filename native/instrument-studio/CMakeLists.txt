cmake_minimum_required(VERSION 3.22)
project(MusicLearningInstrumentStudio VERSION 1.0 LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 20)
get_filename_component(ML_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)
set(JUCE_ROOT "${ML_ROOT}/runtime/third-party/JUCE" CACHE PATH "Installed JUCE source")
add_subdirectory("${JUCE_ROOT}" "${CMAKE_BINARY_DIR}/juce" EXCLUDE_FROM_ALL)
add_executable(ml_instrument_studio Main.cpp)
target_include_directories(ml_instrument_studio PRIVATE "${ML_ROOT}/native/include")
target_compile_definitions(ml_instrument_studio PRIVATE JUCE_GLOBAL_MODULE_SETTINGS_INCLUDED=1
    JUCE_PLUGINHOST_VST3=1 JUCE_WEB_BROWSER=0 JUCE_USE_CURL=0 JUCE_MODAL_LOOPS_PERMITTED=1
    JUCE_USE_FLAC=0 JUCE_USE_OGGVORBIS=0 JUCE_USE_MP3AUDIOFORMAT=0 JUCE_USE_WINDOWS_MEDIA_FORMAT=0
    JUCE_WASAPI=1 JUCE_ASIO=0 JUCE_DIRECTSOUND=0 JUCE_USE_WINRT_MIDI=0 NOMINMAX)
target_compile_options(ml_instrument_studio PRIVATE /utf-8 /EHsc /W4)
target_link_libraries(ml_instrument_studio PRIVATE juce::juce_audio_processors juce::juce_audio_devices
    juce::juce_audio_formats juce::juce_cryptography)
set_target_properties(ml_instrument_studio PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
