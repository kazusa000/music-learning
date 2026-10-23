#include "../audio-host/WindowsMessageObserver.h"

#include <cstdlib>
#include <iostream>
#include <string>

namespace
{
constexpr wchar_t window_class_name[] = L"MusicLearningWindowsMessageObserverFixture";
constexpr UINT fixture_message = WM_APP + 0x57;
constexpr UINT timer_message = WM_TIMER;

LRESULT CALLBACK fixture_window_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam)
{
    return DefWindowProcW(window, message, wparam, lparam);
}

void CALLBACK fixture_timer_proc(HWND, UINT, UINT_PTR, DWORD)
{
}

bool check(bool condition, const char* label)
{
    if (!condition)
        std::cerr << "FAIL: " << label << '\n';
    return condition;
}
}

int main()
{
    const auto instance = GetModuleHandleW(nullptr);
    WNDCLASSW window_class{};
    window_class.lpfnWndProc = &fixture_window_proc;
    window_class.hInstance = instance;
    window_class.lpszClassName = window_class_name;
    const auto atom = RegisterClassW(&window_class);
    if (atom == 0 && GetLastError() != ERROR_CLASS_ALREADY_EXISTS)
        return 2;

    const auto window = CreateWindowExW(0, window_class_name, L"observer fixture", 0,
                                        0, 0, 1, 1, HWND_MESSAGE, nullptr, instance, nullptr);
    if (window == nullptr)
        return 3;

    bool ok = true;
    {
        ml::diagnostic::ScopedWindowsMessageObserver observer;
        const auto token = observer.begin_dispatch(2, 64, 32768, 512);
        ok &= check(PostMessageW(window, fixture_message, 0x1234, 0x5678) != FALSE,
                    "post same-process fixture message");

        MSG message{};
        ok &= check(PeekMessageW(&message, window, fixture_message, fixture_message, PM_NOREMOVE) != FALSE,
                    "peek without removing fixture message");
        ok &= check(PeekMessageW(&message, window, fixture_message, fixture_message, PM_REMOVE) != FALSE,
                    "remove fixture message");
        ok &= check(message.message == fixture_message, "fixture message id preserved");

        const auto timer_proc = reinterpret_cast<LPARAM>(&fixture_timer_proc);
        ok &= check(PostMessageW(window, timer_message, 0x9876, timer_proc) != FALSE,
                    "post same-process timer-shaped message");
        ok &= check(PeekMessageW(&message, window, timer_message, timer_message, PM_REMOVE) != FALSE,
                    "remove timer-shaped fixture message");

        ok &= check(PostThreadMessageW(GetCurrentThreadId(), timer_message, 0xabcd, timer_proc) != FALSE,
                    "post current-thread WM_TIMER message");
        ok &= check(PeekMessageW(&message, nullptr, timer_message, timer_message, PM_REMOVE) != FALSE,
                    "remove current-thread WM_TIMER message");
        observer.end_dispatch(token, 812);

        const auto report = observer.report();
        const auto event_value = report.getProperty(juce::Identifier("events"), {});
        const auto* events = event_value.getArray();
        ok &= check(events != nullptr && events->size() == 3,
                    "PM_NOREMOVE is excluded while process-window and thread messages are recorded");
        if (events != nullptr && events->size() == 3)
        {
            const auto& event = (*events)[0];
            ok &= check(event.getProperty(juce::Identifier("message_id"), 0) == static_cast<int>(fixture_message),
                        "raw message id recorded");
            ok &= check(event.getProperty(juce::Identifier("wparam_hex"), {}).toString().endsWith("1234"),
                        "raw wParam recorded");
            ok &= check(event.getProperty(juce::Identifier("lparam_hex"), {}).toString().endsWith("5678"),
                        "raw lParam recorded");
            ok &= check(event.getProperty(juce::Identifier("dispatch_id"), 0) == 1,
                        "message linked to dispatch window");
            ok &= check(event.getProperty(juce::Identifier("window_belongs_to_current_process"), false),
                        "only current-process window captured");
            ok &= check(event.getProperty(juce::Identifier("window_proc_module_resolved"), false),
                        "window procedure module resolved after hook dispatch");
            ok &= check(!event.getProperty(juce::Identifier("window_proc_module_path"), {}).toString().isEmpty(),
                        "window procedure module identity retained");

            const auto& timer_event = (*events)[1];
            ok &= check(timer_event.getProperty(juce::Identifier("message_id"), 0) == static_cast<int>(timer_message),
                        "timer-shaped message id retained");
            ok &= check(timer_event.getProperty(juce::Identifier("timer_callback_address_hex"), {}).toString()
                            .endsWith(juce::String::toHexString(
                                static_cast<juce::int64>(reinterpret_cast<std::uintptr_t>(&fixture_timer_proc)))
                                          .toLowerCase()),
                        "WM_TIMER lParam callback address retained");
            ok &= check(timer_event.getProperty(juce::Identifier("timer_callback_module_resolved"), false),
                        "timer callback module resolved after hook dispatch");

            const auto& thread_timer_event = (*events)[2];
            ok &= check(thread_timer_event.getProperty(juce::Identifier("window_scope"), {}).toString()
                            == "current_thread_no_window",
                        "null-HWND timer is retained as a current-thread message");
            ok &= check(!thread_timer_event.getProperty(
                            juce::Identifier("window_belongs_to_current_process"), true),
                        "thread timer is not mislabeled as a window message");
            ok &= check(thread_timer_event.getProperty(
                            juce::Identifier("timer_callback_module_resolved"), false),
                        "thread timer callback resolves to a module in this process");
        }

        const auto dispatch_value = report.getProperty(juce::Identifier("dispatch_windows"), {});
        const auto* dispatches = dispatch_value.getArray();
        ok &= check(dispatches != nullptr && dispatches->size() == 1,
                    "one raw dispatch-boundary record retained");
        if (dispatches != nullptr && dispatches->size() == 1)
        {
            const auto& dispatch = (*dispatches)[0];
            ok &= check(dispatch.getProperty(juce::Identifier("completed_blocks"), 0) == 64,
                        "dispatch associated with completed render block");
            ok &= check(dispatch.getProperty(juce::Identifier("source_sample_start"), 0) == 32768,
                        "dispatch sample start retained");
            ok &= check(dispatch.getProperty(juce::Identifier("sample_count"), 0) == 512,
                        "dispatch sample count retained");
            ok &= check(dispatch.getProperty(juce::Identifier("event_begin_index"), 0) == 0
                            && dispatch.getProperty(juce::Identifier("event_end_index_exclusive"), 0) == 3,
                        "raw begin/end event indexes retained");
            ok &= check(dispatch.getProperty(juce::Identifier("elapsed_microseconds"), 0) == 812,
                        "dispatch elapsed duration retained");
        }

        ok &= check(report.getProperty(juce::Identifier("hook"), {}).toString() == "WH_GETMESSAGE",
                    "hook contract named");
        ok &= check(report.getProperty(juce::Identifier("queue_filter"), {}).toString() == "PM_REMOVE only",
                    "PM_REMOVE filter named");
        ok &= check(report.getProperty(juce::Identifier("hook_thread_id"), 0) == static_cast<int>(GetCurrentThreadId()),
                    "hook remains scoped to current thread");
        ok &= check(report.getProperty(juce::Identifier("process_id"), 0) == static_cast<int>(GetCurrentProcessId()),
                    "hook remains scoped to current process");
        ok &= check(report.getProperty(juce::Identifier("events_dropped"), 1) == 0,
                    "bounded recorder reports no overflow");
        ok &= check(report.getProperty(juce::Identifier("thread_messages_without_window_recorded"), 0) == 1,
                    "null-HWND thread message is counted and retained");

        for (std::size_t i = 0; i + 1 < ml::diagnostic::ScopedWindowsMessageObserver::dispatch_capacity; ++i)
        {
            const auto capacity_token = observer.begin_dispatch(0, i, static_cast<std::int64_t>(i * 512), 512);
            observer.end_dispatch(capacity_token, 0);
        }
        const auto overflow_token = observer.begin_dispatch(0, 2048, 2048 * 512, 512);
        observer.end_dispatch(overflow_token, 0);
        const auto after_capacity = observer.report();
        ok &= check(after_capacity.getProperty(juce::Identifier("dispatch_windows_dropped"), 0) == 1,
                    "dispatch capacity overflow is counted without breaking scope cleanup");
    }

    DestroyWindow(window);
    UnregisterClassW(window_class_name, instance);
    if (!ok)
        return 1;
    std::cout << "Windows message observer contract passed\n";
    return 0;
}
