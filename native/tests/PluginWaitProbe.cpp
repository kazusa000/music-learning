// Capture stacks only from the child host created here, then stop that child.
// No UI automation, process-wide settings, plugin writes or remote symbols.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <tlhelp32.h>
#include <dbghelp.h>
#include <fstream>
#include <string>
#include <iomanip>
#pragma comment(lib, "DbgHelp.lib")

static void frame(std::wofstream& log, HANDLE process, DWORD64 pc)
{
    IMAGEHLP_MODULEW64 module{};
    module.SizeOfStruct = sizeof(module);
    log << L"  pc=0x" << std::hex << pc;
    if (SymGetModuleInfoW64(process, pc, &module))
        log << L" " << module.LoadedImageName << L" +0x" << pc-module.BaseOfImage;
    char storage[sizeof(SYMBOL_INFO)+1024]{};
    auto* symbol = reinterpret_cast<SYMBOL_INFO*>(storage);
    symbol->SizeOfStruct = sizeof(SYMBOL_INFO); symbol->MaxNameLen = 1023;
    DWORD64 displacement = 0;
    if (SymFromAddr(process, pc, &displacement, symbol))
        log << L" " << symbol->Name << L" +0x" << displacement;
    log << std::dec << L"\n";
}

int wmain(int argc, wchar_t** argv)
{
    if (argc != 6) return 2; // HOST REQUEST RESPONSE REPORT WAIT_MS
    SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX|SEM_NOOPENFILEERRORBOX);
    std::wofstream log(argv[4]);
    if (!log) return 3;
    DWORD delay = static_cast<DWORD>(_wtoi(argv[5]));
    if (delay < 100 || delay > 30000) return 4;
    std::wstring command = L"\"" + std::wstring(argv[1]) + L"\" \"" + argv[2] + L"\" \"" + argv[3] + L"\"";
    STARTUPINFOW startup{}; startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESHOWWINDOW; startup.wShowWindow = SW_HIDE;
    PROCESS_INFORMATION child{};
    if (!CreateProcessW(argv[1], command.data(), nullptr, nullptr, FALSE,
                        CREATE_NO_WINDOW, nullptr, nullptr, &startup, &child)) {
        log << L"create failed " << GetLastError() << L"\n"; return 5;
    }
    log << L"owned_pid=" << child.dwProcessId << L"\n";
    auto wait = WaitForSingleObject(child.hProcess, delay);
    if (wait == WAIT_TIMEOUT) {
        SymSetOptions(SYMOPT_DEFERRED_LOADS|SYMOPT_UNDNAME|SYMOPT_FAIL_CRITICAL_ERRORS);
        SymInitializeW(child.hProcess, L"", TRUE);
        HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
        THREADENTRY32 thread{}; thread.dwSize = sizeof(thread);
        if (snapshot != INVALID_HANDLE_VALUE && Thread32First(snapshot, &thread)) {
            do {
                if (thread.th32OwnerProcessID != child.dwProcessId) continue;
                log << L"thread=" << thread.th32ThreadID << L"\n";
                HANDLE handle = OpenThread(THREAD_GET_CONTEXT|THREAD_SUSPEND_RESUME|THREAD_QUERY_INFORMATION,
                                           FALSE, thread.th32ThreadID);
                if (!handle) continue;
                if (SuspendThread(handle) != static_cast<DWORD>(-1)) {
                    CONTEXT context{}; context.ContextFlags = CONTEXT_FULL;
                    if (GetThreadContext(handle, &context)) {
                        frame(log, child.hProcess, context.Rip);
                        STACKFRAME64 stack{};
                        stack.AddrPC = {context.Rip, 0, AddrModeFlat};
                        stack.AddrFrame = {context.Rbp, 0, AddrModeFlat};
                        stack.AddrStack = {context.Rsp, 0, AddrModeFlat};
                        for (int i=0; i<35 && StackWalk64(IMAGE_FILE_MACHINE_AMD64, child.hProcess,
                                handle, &stack, &context, nullptr, SymFunctionTableAccess64,
                                SymGetModuleBase64, nullptr); ++i) {
                            if (!stack.AddrPC.Offset) break;
                            frame(log, child.hProcess, stack.AddrPC.Offset);
                        }
                    }
                    ResumeThread(handle);
                }
                CloseHandle(handle);
            } while (Thread32Next(snapshot, &thread));
        }
        if (snapshot != INVALID_HANDLE_VALUE) CloseHandle(snapshot);
        SymCleanup(child.hProcess);
        log << L"owned_child_timeout=true\n"; log.flush();
        TerminateProcess(child.hProcess, 0xEE);
        WaitForSingleObject(child.hProcess, 5000);
    }
    DWORD code=0; GetExitCodeProcess(child.hProcess, &code);
    log << L"exit_code=" << code << L"\n";
    CloseHandle(child.hThread); CloseHandle(child.hProcess);
    return wait == WAIT_TIMEOUT ? 1 : 0;
}
