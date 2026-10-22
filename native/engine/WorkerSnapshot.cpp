#define NOMINMAX
#include <windows.h>
#include <tlhelp32.h>
#include <dbghelp.h>
#include <fstream>
#include <string>
#include <vector>
#include <iostream>

static std::string quoted(const char* value){
    std::string result="\"";
    for(;*value;++value){if(*value=='\\'||*value=='\"')result+='\\';if((unsigned char)*value>=32)result+=*value;}
    return result+'\"';
}
int main(int argc,char** argv){
    if(argc!=3)return 2;
    const DWORD pid=(DWORD)std::stoul(argv[1]);
    HANDLE process=OpenProcess(PROCESS_QUERY_INFORMATION|PROCESS_VM_READ,FALSE,pid);
    if(!process)return 3;
    SymSetOptions(SYMOPT_DEFERRED_LOADS|SYMOPT_UNDNAME|SYMOPT_FAIL_CRITICAL_ERRORS|SYMOPT_NO_PROMPTS);
    if(!SymInitialize(process,"",TRUE)){std::cerr<<"SymInitialize: "<<GetLastError();CloseHandle(process);return 4;}
    HANDLE snapshot=CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD,0);
    THREADENTRY32 entry{};entry.dwSize=sizeof(entry);
    std::vector<DWORD> ids;
    if(Thread32First(snapshot,&entry))do{if(entry.th32OwnerProcessID==pid)ids.push_back(entry.th32ThreadID);}while(Thread32Next(snapshot,&entry));
    CloseHandle(snapshot);
    std::ofstream output(argv[2]);output<<"{\"pid\":"<<pid<<",\"threads\":[";
    bool firstThread=true;
    for(auto id:ids){
        HANDLE thread=OpenThread(THREAD_GET_CONTEXT|THREAD_SUSPEND_RESUME|THREAD_QUERY_INFORMATION,FALSE,id);
        if(!thread)continue;
        if(SuspendThread(thread)==(DWORD)-1){CloseHandle(thread);continue;}
        CONTEXT context{};context.ContextFlags=CONTEXT_FULL;
        const bool captured=GetThreadContext(thread,&context)!=0;
        std::vector<DWORD64> addresses;
        if(captured){
            STACKFRAME64 frame{};frame.AddrPC.Offset=context.Rip;frame.AddrFrame.Offset=context.Rbp;frame.AddrStack.Offset=context.Rsp;
            frame.AddrPC.Mode=frame.AddrFrame.Mode=frame.AddrStack.Mode=AddrModeFlat;
            addresses.push_back(context.Rip);
            for(int n=0;n<32&&StackWalk64(IMAGE_FILE_MACHINE_AMD64,process,thread,&frame,&context,nullptr,
                    SymFunctionTableAccess64,SymGetModuleBase64,nullptr);++n){
                if(!frame.AddrPC.Offset)break;addresses.push_back(frame.AddrPC.Offset);
            }
        }
        // Resume before symbol formatting or writing the diagnostic file.
        ResumeThread(thread);CloseHandle(thread);
        if(!firstThread)output<<',';firstThread=false;output<<"{\"thread_id\":"<<id<<",\"frames\":[";
        bool firstFrame=true;
        for(auto address:addresses){
            char buffer[sizeof(SYMBOL_INFO)+MAX_SYM_NAME]{};auto* symbol=(SYMBOL_INFO*)buffer;
            symbol->SizeOfStruct=sizeof(SYMBOL_INFO);symbol->MaxNameLen=MAX_SYM_NAME;
            DWORD64 displacement=0;const bool named=SymFromAddr(process,address,&displacement,symbol)!=0;
            IMAGEHLP_MODULE64 module{};module.SizeOfStruct=sizeof(module);SymGetModuleInfo64(process,address,&module);
            if(!firstFrame)output<<',';firstFrame=false;
            output<<"{\"address\":"<<address<<",\"module\":"<<quoted(module.ImageName)<<",\"module_offset\":"
                <<address-module.BaseOfImage<<",\"symbol\":"<<quoted(named?symbol->Name:"")<<'}';
        }
        output<<"]}";
    }
    output<<"]}";SymCleanup(process);CloseHandle(process);return 0;
}
