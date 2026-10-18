cmake_minimum_required(VERSION 3.22)
project(MusicLearningNative VERSION 0.1.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(JUCE_ROOT "${CMAKE_SOURCE_DIR}/runtime/third-party/JUCE" CACHE PATH "Pinned JUCE checkout")
if(NOT EXISTS "${JUCE_ROOT}/CMakeLists.txt")
    message(FATAL_ERROR "JUCE is missing. Run scripts/bootstrap_native.py first.")
endif()
set(JUCE_MODULES_ONLY OFF CACHE BOOL "Build JUCE helpers for the VST3 test fixture" FORCE)
if(MSVC)
    add_compile_options(/utf-8)
endif()
add_subdirectory("${JUCE_ROOT}" "${CMAKE_BINARY_DIR}/juce" EXCLUDE_FROM_ALL)

add_library(ml_audio_runtime STATIC)
target_compile_definitions(ml_audio_runtime PUBLIC
    JUCE_GLOBAL_MODULE_SETTINGS_INCLUDED=1
    JUCE_PLUGINHOST_VST3=1
    JUCE_WEB_BROWSER=0
    JUCE_USE_CURL=0
    JUCE_MODAL_LOOPS_PERMITTED=1
    JUCE_USE_FLAC=0
    JUCE_USE_OGGVORBIS=0
    JUCE_USE_MP3AUDIOFORMAT=0
    JUCE_USE_WINDOWS_MEDIA_FORMAT=0)
target_include_directories(ml_audio_runtime PUBLIC "${JUCE_ROOT}/modules")
target_link_libraries(ml_audio_runtime PRIVATE
    juce::juce_audio_processors_headless
    juce::juce_audio_formats
    juce::juce_cryptography)
add_executable(ml_audio_host native/audio-host/Main.cpp native/audio-host/Render.cpp)
target_link_libraries(ml_audio_host PRIVATE ml_audio_runtime)
if(MSVC)
    target_compile_options(ml_audio_runtime PRIVATE /utf-8)
    target_compile_options(ml_audio_host PRIVATE /utf-8 /W4)
endif()
set_target_properties(ml_audio_host PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
enable_testing()
add_test(NAME host_capabilities COMMAND ml_audio_host --capabilities)

juce_add_plugin(MLFixture
    COMPANY_NAME "Music Learning"
    BUNDLE_ID "local.music-learning.fixture"
    PLUGIN_MANUFACTURER_CODE Mlrv
    PLUGIN_CODE Mltf
    FORMATS VST3
    PRODUCT_NAME "MLFixture"
    IS_SYNTH FALSE
    NEEDS_MIDI_INPUT TRUE
    NEEDS_MIDI_OUTPUT FALSE
    COPY_PLUGIN_AFTER_BUILD FALSE
    NEEDS_WEB_BROWSER FALSE
    NEEDS_CURL FALSE)
target_sources(MLFixture PRIVATE native/test-plugin/Fixture.cpp)
target_compile_definitions(MLFixture PUBLIC
    JUCE_WEB_BROWSER=0 JUCE_USE_CURL=0 JUCE_VST3_CAN_REPLACE_VST2=0)
target_link_libraries(MLFixture PRIVATE juce::juce_audio_processors)
