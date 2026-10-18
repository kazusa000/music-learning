#pragma once
#include <juce_audio_processors_headless/juce_audio_processors_headless.h>
#include <juce_audio_formats/juce_audio_formats.h>
#include <juce_cryptography/juce_cryptography.h>
#include <vector>
#include <memory>

namespace ml
{
using namespace juce;
var object(std::initializer_list<std::pair<String, var>>);
void require(bool, const String&);
void checkKeys(const var&, std::initializer_list<const char*>);
var field(const var&, const char*, const var& fallback = {});
String text(const var&, const char*);
void writeJson(const File&, const var&);
AudioProcessorParameter* parameter(AudioPluginInstance&, const String&);
var pluginSnapshot(AudioPluginInstance&);
std::unique_ptr<AudioPluginInstance> load(const var&, double, int);
var render(const var&);
}
