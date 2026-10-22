#pragma once
#include "../audio-host/Host.h"

namespace ml {
// JUCE's SHA256(InputStream&) consumes 64 bytes per read. Buffer the existing
// full binary verification, rather than issuing a Windows file read per block.
class WorkerFileInputStream final:public juce::BufferedInputStream {
    juce::FileInputStream* file;
    explicit WorkerFileInputStream(juce::FileInputStream* source):juce::BufferedInputStream(source,65536,true),file(source){}
public:
    explicit WorkerFileInputStream(const juce::File& source):WorkerFileInputStream(new juce::FileInputStream(source)){}
    bool openedOk()const{return file->openedOk();}
};
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
