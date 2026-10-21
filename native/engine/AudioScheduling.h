#pragma once
#include <windows.h>
#include <avrt.h>

namespace mlengine {
// Applies only to our current audio-producing thread. Never changes system
// timer policy, another process, the endpoint or the user's master volume.
class AudioScheduling final {
    HANDLE task=nullptr;
public:
    AudioScheduling(){DWORD index=0;task=AvSetMmThreadCharacteristicsW(L"Pro Audio",&index);if(task)AvSetMmThreadPriority(task,AVRT_PRIORITY_HIGH);}
    ~AudioScheduling(){if(task)AvRevertMmThreadCharacteristics(task);}
    AudioScheduling(const AudioScheduling&)=delete;
    AudioScheduling& operator=(const AudioScheduling&)=delete;
    bool active()const{return task!=nullptr;}
};
inline void pumpPendingMessages(){
    MSG message{};
    for(int count=0;count<32&&PeekMessageW(&message,nullptr,0,0,PM_REMOVE);++count)
        if(message.message!=WM_QUIT){TranslateMessage(&message);DispatchMessageW(&message);}
}
}
