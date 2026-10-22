#include "LoadBudget.h"
#include <stdexcept>

int main(){
    using mlengine::LoadBudget;
    auto require=[](bool valid){if(!valid)throw std::runtime_error("Loading budget invariant failed");};
    LoadBudget idle;require(!idle.expired(45000));require(idle.expired(45001));
    LoadBudget io;io.observe(44000,1024,1,0);require(!io.expired(46000));require(io.expired(50001));
    LoadBudget cpu;cpu.observe(44000,0,0,10000);require(!cpu.expired(46000));
    for(int at=48000;at<=120000;at+=4000)cpu.observe(at,0,0,(uint64_t)at);
    require(!cpu.expired(120000));require(cpu.expired(120001));
    LoadBudget stalled;stalled.observe(44000,100,2,100);stalled.observe(50000,100,2,100);
    require(stalled.expired(50000));
    return 0;
}
