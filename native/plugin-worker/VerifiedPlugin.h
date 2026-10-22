#pragma once

namespace ml {
inline std::unique_ptr<juce::AudioPluginInstance> loadWorkerPlugin(const juce::var& spec,double rate,int frames){
    const auto cached=field(spec,"description_xml").toString();
    if(cached.isEmpty())return load(spec,rate,frames);
    auto xml=juce::parseXML(cached);juce::PluginDescription description;
    require(xml&&description.loadFromXml(*xml),"Invalid verified plugin description");
    require(description.pluginFormatName=="VST3"&&juce::File(description.fileOrIdentifier)==juce::File(text(spec,"path"))
        &&description.createIdentifierString()==text(spec,"identifier")&&description.version==text(spec,"version"),
        "Verified plugin description identity mismatch");
    require(text(spec,"binary_sha256").length()==64,"Verified description requires a binary digest");
    struct Scope {
        const juce::PluginDescription* previous;
        explicit Scope(const juce::PluginDescription* next):previous(workerDescription){workerDescription=next;}
        ~Scope(){workerDescription=previous;}
    } scope(&description);
    auto plugin=load(spec,rate,frames);
    const auto actual=plugin->getPluginDescription();
    require(actual.createIdentifierString()==description.createIdentifierString()&&actual.version==description.version,
        "Loaded plugin differs from verified description");
    return plugin;
}
}
