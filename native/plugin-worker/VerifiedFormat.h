#pragma once
#include "../audio-host/Host.h"

namespace ml {
// Worker-only discovery cache. The descriptor comes from the SHA-verified
// project program; the existing loader still verifies the actual binary,
// creates its selected VST3 class and checks/restores the complete state.
inline thread_local const juce::PluginDescription* workerDescription=nullptr;
class WorkerVST3Format final:public juce::VST3PluginFormatHeadless {
public:
    void findAllTypesForFile(juce::OwnedArray<juce::PluginDescription>& types,const juce::String& path)override{
        if(workerDescription&&juce::File(workerDescription->fileOrIdentifier)==juce::File(path)){
            types.add(new juce::PluginDescription(*workerDescription));return;
        }
        juce::VST3PluginFormatHeadless::findAllTypesForFile(types,path);
    }
};
}
