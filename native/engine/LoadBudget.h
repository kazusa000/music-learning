#pragma once
#include <cstdint>

namespace mlengine {
// Loading is not a DSP deadline. Preserve the original idle failure, permit
// measurable resource work, and always stop at the absolute hard bound.
struct LoadBudget {
    double started=0,progress=0;
    uint64_t readBytes=0,readOperations=0,cpu100ns=0;
    explicit LoadBudget(double now=0):started(now),progress(now){}
    void observe(double now,uint64_t bytes,uint64_t reads,uint64_t cpu){
        if(bytes>readBytes||reads>readOperations||cpu>cpu100ns)progress=now;
        readBytes=bytes;readOperations=reads;cpu100ns=cpu;
    }
    bool expired(double now)const{return now-started>120000||(now-started>45000&&now-progress>5000);}
};
}
