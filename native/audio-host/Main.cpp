#include "Host.h"
#include <iostream>
#include <stdexcept>
#include <cmath>
#if JUCE_WINDOWS
#include <windows.h>
#include <stdlib.h>
#endif

namespace ml
{
using namespace juce;

var object(std::initializer_list<std::pair<String, var>> fields)
{
    auto result = std::make_unique<DynamicObject>();
    for (const auto& [key, value] : fields)
        result->setProperty(Identifier(key), value);
    return var(result.release());
}

void require(bool condition, const String& message)
{
    if (!condition)
        throw std::runtime_error(message.toStdString());
}

void checkKeys(const var& value, std::initializer_list<const char*> keys)
{
    require(value.isObject(), "Expected JSON object");
    for (const auto& property : value.getDynamicObject()->getProperties())
    {
        bool found = false;
        for (const auto* key : keys) if (property.name.toString() == key) found = true;
        require(found, "Unsupported field: " + property.name.toString());
    }
}

var field(const var& value, const char* key, const var& fallback)
{
    return value.getProperty(Identifier(key), fallback);
}

String text(const var& value, const char* key)
{
    const auto result = field(value, key);
    require(result.isString() && result.toString().isNotEmpty(), String("Missing string: ") + key);
    return result.toString();
}

var parse(const File& file)
{
    require(file.existsAsFile(), "Request does not exist: " + file.getFullPathName());
    var value;
    const auto result = JSON::parse(file.loadFileAsString(), value);
    require(result.wasOk(), "Invalid JSON: " + result.getErrorMessage());
    require(value.isObject(), "Request must be an object");
    return value;
}

void writeJson(const File& file, const var& value)
{
    require(file.getParentDirectory().createDirectory().wasOk(), "Cannot create output directory");
    TemporaryFile temporary(file);
    require(temporary.getFile().replaceWithText(JSON::toString(value)), "Cannot write JSON");
    require(temporary.overwriteTargetFileWithTemporary(), "Cannot publish JSON");
}

var descriptionJson(const PluginDescription& d)
{
    return object({{"name", d.name}, {"manufacturer", d.manufacturerName},
                   {"version", d.version}, {"format", d.pluginFormatName},
                   {"identifier", d.createIdentifierString()}, {"unique_id", d.uniqueId},
                   {"is_instrument", d.isInstrument}, {"inputs", d.numInputChannels},
                   {"outputs", d.numOutputChannels}, {"path", d.fileOrIdentifier},
                   {"description_xml", d.createXml()->toString()}});
}

std::vector<PluginDescription> scan(const String& path)
{
    require(File(path).exists(), "Plugin does not exist: " + path);
    VST3PluginFormatHeadless format;
    OwnedArray<PluginDescription> types;
    format.findAllTypesForFile(types, path);
    require(!types.isEmpty(), "No VST3 classes found: " + path);
    std::vector<PluginDescription> result;
    for (const auto* type : types)
        result.push_back(*type);
    return result;
}

PluginDescription selectDescription(const var& specification)
{
    const auto types = scan(text(specification, "path"));
    const auto identifier = field(specification, "identifier").toString();
    require(types.size() == 1 || identifier.isNotEmpty(), "Multiple classes: select identifier explicitly");
    for (const auto& type : types)
        if (identifier.isEmpty() || type.createIdentifierString() == identifier)
            return type;
    throw std::runtime_error("Plugin identifier not found");
}

String parameterId(AudioProcessorParameter* parameter)
{
    if (const auto* hosted = dynamic_cast<HostedAudioProcessorParameter*>(parameter))
        return hosted->getParameterID();
    throw std::runtime_error("Plugin parameter has no stable host ID");
}

AudioProcessorParameter* parameter(AudioPluginInstance& plugin, const String& id)
{
    for (auto* p : plugin.getParameters())
        if (parameterId(p) == id)
            return p;
    throw std::runtime_error(("Unknown parameter ID: " + id).toStdString());
}

var parameters(AudioPluginInstance& plugin)
{
    Array<var> result;
    for (auto* p : plugin.getParameters())
        result.add(object({{"id", parameterId(p)}, {"name", p->getName(256)},
                           {"label", p->getLabel()}, {"value", p->getValue()},
                           {"default", p->getDefaultValue()}, {"text", p->getCurrentValueAsText()},
                           {"steps", p->getNumSteps()}, {"discrete", p->isDiscrete()},
                           {"automatable", p->isAutomatable()}, {"boolean", p->isBoolean()}}));
    return result;
}

std::unique_ptr<AudioPluginInstance> load(const var& spec, double rate, int blockSize)
{
    checkKeys(spec, {"id", "path", "identifier", "state_base64", "state_encoding", "state_sha256",
        "binary_sha256", "vst3_class_id", "version", "parameters", "preset_path", "sfizz_sfz_path",
        "name", "manufacturer", "format", "unique_id", "is_instrument", "inputs", "outputs",
        "description_xml", "parameters_readback", "latency_samples", "tail_seconds", "buses", "sidechain"});
    const auto description = selectDescription(spec);
    const auto expectedHash = field(spec, "binary_sha256").toString();
    if (expectedHash.isNotEmpty())
    {
        FileInputStream input(File(description.fileOrIdentifier));
        require(input.openedOk() && SHA256(input).toHexString() == expectedHash, "Plugin binary digest mismatch");
    }
    const auto expectedVersion = field(spec, "version").toString();
    require(expectedVersion.isEmpty() || description.version == expectedVersion, "Plugin version mismatch");
    AudioPluginFormatManager manager;
    manager.addFormat(std::make_unique<VST3PluginFormatHeadless>());
    String error;
    auto plugin = manager.createPluginInstance(description, rate, blockSize, error);
    require(plugin != nullptr, "Cannot instantiate VST3: " + error);
    plugin->setNonRealtime(true);
    plugin->disableNonMainBuses();
    if (static_cast<bool>(field(spec, "sidechain", false)))
    {
        auto* bus = plugin->getBus(true, 1);
        require(bus != nullptr && bus->enable(true), "Plugin does not support an enabled sidechain bus");
        require(bus->getNumberOfChannels() == 2, "Only stereo sidechains supported");
    }
    const auto encoded = field(spec, "state_base64").toString();
    if (encoded.isNotEmpty())
    {
        MemoryBlock state;
        {
            MemoryOutputStream decoded(state, false);
            require(Base64::convertFromBase64(decoded, encoded), "Invalid plugin state encoding");
        }
        require(field(spec, "state_encoding", "rfc4648-juce-vst3-state").toString() == "rfc4648-juce-vst3-state",
                "Unsupported state encoding");
        const auto stateHash = field(spec, "state_sha256").toString();
        require(stateHash.isEmpty() || SHA256(state).toHexString() == stateHash, "Plugin state digest mismatch");
        const auto xml = AudioProcessor::getXmlFromBinary(state.getData(), static_cast<int>(state.getSize()));
        require(xml != nullptr && xml->hasTagName("VST3PluginState") && xml->getChildByName("IComponent") != nullptr,
                "Invalid JUCE VST3 component/controller state");
        plugin->setStateInformation(state.getData(), static_cast<int>(state.getSize()));
    }
    const auto sfzPath = field(spec, "sfizz_sfz_path").toString();
    if (sfzPath.isNotEmpty())
    {
        require(description.name.equalsIgnoreCase("sfizz") && description.version == "1.2.3",
                "SFZ adapter is verified only for sfizz VST3 1.2.3");
        const File sfz(sfzPath);
        require(sfz.existsAsFile() && sfz.hasFileExtension("sfz"), "SFZ file is missing");
        // Version-specific adapter, based on sfizz-ui 1.2.3 SfizzVstState::store:
        // LE uint64 state version, LE int32 UTF-8 string size including NUL,
        // path, then the remaining untouched settings. Do not apply to other plugins.
        MemoryBlock binary;
        plugin->getStateInformation(binary);
        auto xml = AudioProcessor::getXmlFromBinary(binary.getData(), static_cast<int>(binary.getSize()));
        require(xml != nullptr, "Cannot inspect sfizz state");
        auto* component = xml->getChildByName("IComponent");
        require(component != nullptr, "sfizz component state missing");
        MemoryBlock componentData;
        require(componentData.fromBase64Encoding(component->getAllSubText()), "Invalid sfizz component encoding");
        MemoryInputStream input(componentData, false);
        require(input.readInt64() == 5, "Unsupported sfizz state version");
        const int oldSize = input.readInt();
        require(oldSize > 0 && static_cast<size_t>(12 + oldSize) <= componentData.getSize(), "Invalid sfizz path state");
        const auto path = sfz.getFullPathName().replaceCharacter('\\', '/');
        MemoryBlock updated;
        {
            MemoryOutputStream output(updated, false);
            output.writeInt64(5);
            require(path.getNumBytesAsUTF8() < 262144, "SFZ path is too long");
            const int bytes = static_cast<int>(path.getNumBytesAsUTF8()) + 1;
            output.writeInt(bytes);
            output.write(path.toRawUTF8(), static_cast<size_t>(bytes));
            output.write(static_cast<const char*>(componentData.getData()) + 12 + oldSize,
                         componentData.getSize() - static_cast<size_t>(12 + oldSize));
        }
        component->deleteAllChildElements();
        component->addTextElement(updated.toBase64Encoding());
        AudioProcessor::copyXmlToBinary(*xml, binary);
        plugin->setStateInformation(binary.getData(), static_cast<int>(binary.getSize()));
        MessageManager::getInstance()->runDispatchLoopUntil(2);
    }
    const auto presetPath = field(spec, "preset_path").toString();
    if (presetPath.isNotEmpty())
    {
        MemoryBlock data;
        require(File(presetPath).loadFileAsData(data), "Cannot read VST3 preset");
        auto* client = plugin->getVST3Client();
        require(client != nullptr && client->setPreset(data),
                "VST3 preset rejected");
    }
    if (auto* overrides = field(spec, "parameters").getDynamicObject())
        for (const auto& item : overrides->getProperties())
        {
            require(item.value.isDouble() || item.value.isInt(), "Parameter value must be a number");
            const float value = static_cast<float>(item.value);
            require(std::isfinite(value) && value >= 0.0f && value <= 1.0f, "Parameter value outside [0,1]");
            auto* p = parameter(*plugin, item.name.toString());
            require(p->isAutomatable() || std::abs(p->getValue() - value) <= 1e-5f,
                    "Nonautomatable parameter writes require a verified plugin adapter");
            p->beginChangeGesture();
            p->setValueNotifyingHost(value);
            p->endChangeGesture();
            require(std::abs(p->getValue() - value) <= 1e-4f,
                    "Plugin did not retain the requested normalized parameter value");
        }
    return plugin;
}

var pluginSnapshot(AudioPluginInstance& plugin)
{
    MemoryBlock state;
    plugin.getStateInformation(state);
    auto result = descriptionJson(plugin.getPluginDescription());
    result.getDynamicObject()->setProperty("state_base64", Base64::toBase64(state.getData(), state.getSize()));
    result.getDynamicObject()->setProperty("state_encoding", "rfc4648-juce-vst3-state");
    result.getDynamicObject()->setProperty("state_sha256", SHA256(state).toHexString());
    FileInputStream input(File(plugin.getPluginDescription().fileOrIdentifier));
    require(input.openedOk(), "Cannot hash plugin binary");
    result.getDynamicObject()->setProperty("binary_sha256", SHA256(input).toHexString());
    result.getDynamicObject()->setProperty("parameters_readback", parameters(plugin));
    result.getDynamicObject()->setProperty("latency_samples", plugin.getLatencySamples());
    result.getDynamicObject()->setProperty("tail_seconds", plugin.getTailLengthSeconds());
    Array<var> buses;
    for (bool isInput : {true, false})
        for (int index = 0; index < plugin.getBusCount(isInput); ++index)
        {
            const auto* bus = plugin.getBus(isInput, index);
            buses.add(object({{"direction", isInput ? "input" : "output"}, {"index", index},
                               {"name", bus->getName()}, {"channels", bus->getNumberOfChannels()},
                               {"enabled", bus->isEnabled()}}));
        }
    result.getDynamicObject()->setProperty("buses", buses);
    if (auto* client = plugin.getVST3Client())
    {
        const auto preset = client->getPreset();
        // VST3 preset header: magic(4), version(4), ASCII class ID(32).
        if (preset.getSize() >= 48 && std::memcmp(preset.getData(), "VST3", 4) == 0)
            result.getDynamicObject()->setProperty("vst3_class_id",
                String::fromUTF8(static_cast<const char*>(preset.getData()) + 8, 32));
    }
    return result;
}

var capabilities()
{
    return object({{"schema_version", 1}, {"host", "music-learning-native"},
        {"juce_version", SystemStats::getJUCEVersion()}, {"format", "VST3"}, {"editor_required", false},
        {"actions", var(Array<var>{"capabilities", "scan", "inspect", "render"})},
        {"automation", "sample-aligned step and linear points"},
        {"routing", "offline acyclic stereo graph, post-fader sends, buses and stereo sidechains"},
        {"project_service", "immutable revision input; publications owned by domain service"},
        {"state_encoding", "rfc4648-juce-vst3-state"}});
}

var execute(const var& request)
{
    checkKeys(request, {"schema_version", "action", "plugin_path", "plugin", "project", "output_path"});
    require(static_cast<int>(field(request, "schema_version", 0)) == 1, "Unsupported schema_version");
    const auto action = text(request, "action");
    if (action == "capabilities") return capabilities();
    if (action == "scan")
    {
        Array<var> types;
        for (const auto& description : scan(text(request, "plugin_path")))
            types.add(descriptionJson(description));
        return object({{"plugins", types}});
    }
    if (action == "inspect")
    {
        auto plugin = load(field(request, "plugin"), 48000.0, 256);
        return object({{"plugin", pluginSnapshot(*plugin)}});
    }
    if (action == "render")
        return render(request);
    throw std::runtime_error(("Unsupported action: " + action).toStdString());
}
} // namespace ml

int main(int argc, char** argv)
{
#if JUCE_WINDOWS
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    _set_abort_behavior(0, _WRITE_ABORT_MSG | _CALL_REPORTFAULT);
#endif
    juce::ScopedJuceInitialiser_GUI initialiser;
    if (argc == 2 && std::string(argv[1]) == "--capabilities")
    {
        std::cout << juce::JSON::toString(ml::capabilities()).toStdString() << std::endl;
        return 0;
    }
    if (argc != 3)
    {
        std::cerr << "Usage: ml_audio_host REQUEST.json RESPONSE.json" << std::endl;
        return 2;
    }
    const juce::File response(juce::String::fromUTF8(argv[2]));
    try
    {
        const auto request = ml::parse(juce::File(juce::String::fromUTF8(argv[1])));
        const auto result = ml::execute(request);
        ml::writeJson(response, ml::object({{"schema_version", 1}, {"ok", true}, {"result", result}}));
        return 0;
    }
    catch (const std::exception& error)
    {
        try
        {
            ml::writeJson(response, ml::object({{"schema_version", 1}, {"ok", false},
                                              {"error", juce::String(error.what())}}));
        }
        catch (...) {}
        std::cerr << error.what() << std::endl;
        return 1;
    }
}
