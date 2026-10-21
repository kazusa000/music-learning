cmake_minimum_required(VERSION 3.22)
project(MusicLearningDynamicFixture VERSION 1.0 LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
get_filename_component(ML_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)
set(JUCE_ROOT "${ML_ROOT}/runtime/third-party/JUCE")
add_subdirectory("${JUCE_ROOT}" "${CMAKE_BINARY_DIR}/juce" EXCLUDE_FROM_ALL)
juce_add_plugin(MLDynamicFixture
    COMPANY_NAME "Music Learning" BUNDLE_ID "local.music-learning.dynamic-fixture"
    PLUGIN_MANUFACTURER_CODE Mlrv PLUGIN_CODE Mldf FORMATS VST3
    PRODUCT_NAME "MLDynamicFixture" IS_SYNTH FALSE NEEDS_MIDI_INPUT TRUE
    NEEDS_MIDI_OUTPUT FALSE COPY_PLUGIN_AFTER_BUILD FALSE NEEDS_WEB_BROWSER FALSE NEEDS_CURL FALSE)
target_sources(MLDynamicFixture PRIVATE Fixture.cpp)
target_compile_definitions(MLDynamicFixture PUBLIC JUCE_WEB_BROWSER=0 JUCE_USE_CURL=0 JUCE_VST3_CAN_REPLACE_VST2=0)
target_compile_options(MLDynamicFixture PRIVATE /utf-8)
target_link_libraries(MLDynamicFixture PRIVATE juce::juce_audio_processors)
