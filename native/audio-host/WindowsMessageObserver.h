#pragma once

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <juce_core/juce_core.h>

#include <array>
#include <atomic>
#include <cstdint>
#include <stdexcept>

namespace ml::diagnostic
{
// A bounded, thread-local observer for messages removed from this process's
// current GUI-thread queue. The hook never writes files or constructs strings.
class ScopedWindowsMessageObserver final
{
public:
    static constexpr std::size_t event_capacity = 4096;
    static constexpr std::size_t dispatch_capacity = 1024;

    struct DispatchToken
    {
        std::uint32_t id = 0;
    };

    ScopedWindowsMessageObserver()
        : process_id_(GetCurrentProcessId()), thread_id_(GetCurrentThreadId())
    {
        if (active_ != nullptr)
            throw std::logic_error("Only one Windows message observer may be active per thread");

        active_ = this;
        hook_ = SetWindowsHookExW(WH_GETMESSAGE, &hook_proc, nullptr, thread_id_);
        if (hook_ == nullptr)
        {
            active_ = nullptr;
            throw std::runtime_error("SetWindowsHookExW(WH_GETMESSAGE) failed");
        }
    }

    ScopedWindowsMessageObserver(const ScopedWindowsMessageObserver&) = delete;
    ScopedWindowsMessageObserver& operator=(const ScopedWindowsMessageObserver&) = delete;

    ~ScopedWindowsMessageObserver()
    {
        if (hook_ != nullptr)
            UnhookWindowsHookEx(hook_);
        hook_ = nullptr;
        if (active_ == this)
            active_ = nullptr;
    }

    DispatchToken begin_dispatch(std::int32_t track_index,
                                 std::uint64_t completed_blocks,
                                 std::int64_t source_sample_start,
                                 std::int32_t sample_count)
    {
        if (dispatch_active_)
            throw std::logic_error("Overlapping message-dispatch observation windows");
        const auto index = dispatch_count_.fetch_add(1, std::memory_order_relaxed);
        active_track_index_ = track_index;
        active_completed_blocks_ = static_cast<std::int64_t>(completed_blocks);
        active_source_sample_start_ = source_sample_start;
        active_sample_count_ = sample_count;
        dispatch_active_ = true;
        if (index >= dispatches_.size())
        {
            dropped_dispatches_.fetch_add(1, std::memory_order_relaxed);
            active_dispatch_id_ = 0;
            return {};
        }

        auto& dispatch = dispatches_[index];
        dispatch.id = index + 1;
        dispatch.track_index = track_index;
        dispatch.completed_blocks = completed_blocks;
        dispatch.source_sample_start = source_sample_start;
        dispatch.sample_count = sample_count;
        dispatch.events_begin = event_count_.load(std::memory_order_relaxed);
        dispatch.events_end_exclusive = dispatch.events_begin;
        dispatch.elapsed_microseconds = 0;
        dispatch.completed = false;
        active_dispatch_id_ = dispatch.id;
        return {dispatch.id};
    }

    void end_dispatch(DispatchToken token, std::uint64_t elapsed_microseconds)
    {
        if (!dispatch_active_)
            throw std::logic_error("No message-dispatch observation window is active");
        if (token.id != 0)
        {
            const auto index = static_cast<std::size_t>(token.id - 1);
            if (index >= dispatches_.size() || dispatches_[index].id != token.id)
                throw std::logic_error("Message-dispatch observation token is invalid");
            auto& dispatch = dispatches_[index];
            dispatch.events_end_exclusive = event_count_.load(std::memory_order_relaxed);
            dispatch.elapsed_microseconds = elapsed_microseconds;
            dispatch.completed = true;
        }
        active_dispatch_id_ = 0;
        active_track_index_ = -1;
        active_completed_blocks_ = -1;
        active_source_sample_start_ = -1;
        active_sample_count_ = 0;
        dispatch_active_ = false;
    }

    juce::var report() const
    {
        juce::Array<juce::var> event_rows;
        const auto event_total = event_count_.load(std::memory_order_relaxed);
        const auto event_limit = event_total < events_.size() ? event_total : events_.size();
        event_rows.ensureStorageAllocated(static_cast<int>(event_limit));
        for (std::size_t i = 0; i < event_limit; ++i)
        {
            const auto& event = events_[i];
            const auto window_module = resolve_module(event.window_proc);
            const auto timer_module = event.message == WM_TIMER && event.timer_proc != 0
                ? resolve_module(event.timer_proc) : ModuleLocation{};
            event_rows.add(juce::var(new juce::DynamicObject()));
            auto* row = event_rows.getLast().getDynamicObject();
            row->setProperty("sequence", static_cast<juce::int64>(event.sequence));
            row->setProperty("dispatch_id", static_cast<int>(event.dispatch_id));
            row->setProperty("track_index", event.track_index);
            row->setProperty("completed_blocks", static_cast<juce::int64>(event.completed_blocks));
            row->setProperty("source_sample_start", static_cast<juce::int64>(event.source_sample_start));
            row->setProperty("sample_count", event.sample_count);
            row->setProperty("source_sample_end_exclusive",
                              static_cast<juce::int64>(event.source_sample_start + event.sample_count));
            row->setProperty("message_id", static_cast<int>(event.message));
            row->setProperty("message_label", event.message == WM_TIMER ? "WM_TIMER" : "unclassified");
            row->setProperty("message_time_ms", static_cast<juce::int64>(event.message_time_ms));
            row->setProperty("wparam_hex", hex(event.wparam));
            row->setProperty("lparam_hex", hex(event.lparam));
            row->setProperty("hwnd_hex", hex(event.hwnd));
            row->setProperty("window_scope", event.has_window ? "process_window" : "current_thread_no_window");
            row->setProperty("thread_id", static_cast<int>(event.thread_id));
            row->setProperty("window_thread_id", static_cast<int>(event.window_thread_id));
            row->setProperty("window_process_id", static_cast<int>(event.window_process_id));
            row->setProperty("window_belongs_to_current_process", event.window_belongs_to_current_process);
            row->setProperty("window_proc_address_hex", hex(event.window_proc));
            row->setProperty("window_proc_module_resolved", window_module.resolved);
            row->setProperty("window_proc_module_handle_hex", hex(window_module.module_handle));
            row->setProperty("window_proc_module_path", window_module.path);
            row->setProperty("window_proc_module_rva_hex", hex(window_module.rva));
            row->setProperty("timer_callback_address_hex", hex(event.timer_proc));
            row->setProperty("timer_callback_module_resolved", timer_module.resolved);
            row->setProperty("timer_callback_module_handle_hex", hex(timer_module.module_handle));
            row->setProperty("timer_callback_module_path", timer_module.path);
            row->setProperty("timer_callback_module_rva_hex", hex(timer_module.rva));
        }

        juce::Array<juce::var> dispatch_rows;
        const auto dispatch_total = dispatch_count_.load(std::memory_order_relaxed);
        const auto dispatch_limit = dispatch_total < dispatches_.size() ? dispatch_total : dispatches_.size();
        dispatch_rows.ensureStorageAllocated(static_cast<int>(dispatch_limit));
        for (std::size_t i = 0; i < dispatch_limit; ++i)
        {
            const auto& dispatch = dispatches_[i];
            auto* row = new juce::DynamicObject();
            row->setProperty("dispatch_id", static_cast<int>(dispatch.id));
            row->setProperty("track_index", dispatch.track_index);
            row->setProperty("completed_blocks", static_cast<juce::int64>(dispatch.completed_blocks));
            row->setProperty("source_sample_start", static_cast<juce::int64>(dispatch.source_sample_start));
            row->setProperty("sample_count", dispatch.sample_count);
            row->setProperty("source_sample_end_exclusive",
                              static_cast<juce::int64>(dispatch.source_sample_start + dispatch.sample_count));
            row->setProperty("event_begin_index", static_cast<juce::int64>(dispatch.events_begin));
            row->setProperty("event_end_index_exclusive", static_cast<juce::int64>(dispatch.events_end_exclusive));
            row->setProperty("elapsed_microseconds", static_cast<juce::int64>(dispatch.elapsed_microseconds));
            row->setProperty("completed", dispatch.completed);
            dispatch_rows.add(juce::var(row));
        }

        auto* result = new juce::DynamicObject();
        result->setProperty("schema_version", 1);
        result->setProperty("hook", "WH_GETMESSAGE");
        result->setProperty("queue_filter", "PM_REMOVE only");
        result->setProperty("hook_scope", "current process, current GUI thread");
        result->setProperty("hook_thread_id", static_cast<int>(thread_id_));
        result->setProperty("process_id", static_cast<int>(process_id_));
        result->setProperty("hook_installed", hook_ != nullptr);
        result->setProperty("events", event_rows);
        result->setProperty("events_recorded", static_cast<juce::int64>(event_limit));
        result->setProperty("events_dropped", static_cast<juce::int64>(dropped_events_.load(std::memory_order_relaxed)));
        result->setProperty("foreign_window_messages_skipped",
                            static_cast<juce::int64>(foreign_window_messages_.load(std::memory_order_relaxed)));
        result->setProperty("thread_messages_without_window_recorded",
                            static_cast<juce::int64>(thread_messages_without_window_.load(std::memory_order_relaxed)));
        result->setProperty("unexpected_thread_callbacks_skipped",
                            static_cast<juce::int64>(unexpected_thread_callbacks_.load(std::memory_order_relaxed)));
        result->setProperty("dispatch_windows", dispatch_rows);
        result->setProperty("dispatch_windows_recorded", static_cast<juce::int64>(dispatch_limit));
        result->setProperty("dispatch_windows_dropped", static_cast<juce::int64>(dropped_dispatches_.load(std::memory_order_relaxed)));
        return juce::var(result);
    }

private:
    struct Event
    {
        std::uint64_t sequence = 0;
        std::uint32_t dispatch_id = 0;
        std::int32_t track_index = -1;
        std::int64_t completed_blocks = -1;
        std::int64_t source_sample_start = -1;
        std::int32_t sample_count = 0;
        std::uint32_t message = 0;
        std::uint32_t message_time_ms = 0;
        std::uintptr_t hwnd = 0;
        std::uintptr_t wparam = 0;
        std::uintptr_t lparam = 0;
        std::uintptr_t window_proc = 0;
        std::uintptr_t timer_proc = 0;
        std::uint32_t thread_id = 0;
        std::uint32_t window_thread_id = 0;
        std::uint32_t window_process_id = 0;
        bool has_window = false;
        bool window_belongs_to_current_process = false;
    };

    struct DispatchWindow
    {
        std::uint32_t id = 0;
        std::int32_t track_index = -1;
        std::uint64_t completed_blocks = 0;
        std::int64_t source_sample_start = -1;
        std::int32_t sample_count = 0;
        std::uint64_t events_begin = 0;
        std::uint64_t events_end_exclusive = 0;
        std::uint64_t elapsed_microseconds = 0;
        bool completed = false;
    };

    struct ModuleLocation
    {
        bool resolved = false;
        std::uintptr_t module_handle = 0;
        std::uintptr_t rva = 0;
        juce::String path;
    };

    static constexpr std::uint32_t no_dispatch = 0;
    static inline thread_local ScopedWindowsMessageObserver* active_ = nullptr;

    static juce::String hex(std::uintptr_t value)
    {
        constexpr char digits[] = "0123456789abcdef";
        std::array<char, 2 + sizeof(value) * 2 + 1> result{};
        result[0] = '0';
        result[1] = 'x';
        for (std::size_t i = 0; i < sizeof(value) * 2; ++i)
        {
            const auto shift = (sizeof(value) * 2 - i - 1) * 4;
            result[2 + i] = digits[(value >> shift) & 0xf];
        }
        return juce::String::fromUTF8(result.data(), static_cast<int>(result.size() - 1));
    }

    static ModuleLocation resolve_module(std::uintptr_t address)
    {
        ModuleLocation result;
        if (address == 0)
            return result;

        HMODULE module = nullptr;
        const auto flags = GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS
                         | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT;
        if (!GetModuleHandleExW(flags, reinterpret_cast<LPCWSTR>(address), &module))
            return result;

        std::array<wchar_t, 32768> path{};
        const auto length = GetModuleFileNameW(module, path.data(), static_cast<DWORD>(path.size()));
        if (length == 0 || length >= path.size())
            return result;

        result.resolved = true;
        result.module_handle = reinterpret_cast<std::uintptr_t>(module);
        result.rva = address - result.module_handle;
        result.path = juce::String(path.data(), length);
        return result;
    }

    static LRESULT CALLBACK hook_proc(int code, WPARAM wparam, LPARAM lparam) noexcept
    {
        auto* observer = active_;
        const auto hook = observer != nullptr ? observer->hook_ : nullptr;
        if (code == HC_ACTION && wparam == PM_REMOVE && observer != nullptr && lparam != 0)
        {
            const auto* message = reinterpret_cast<const MSG*>(lparam);
            observer->record(*message);
        }
        return CallNextHookEx(hook, code, wparam, lparam);
    }

    void record(const MSG& message) noexcept
    {
        const auto current_thread = GetCurrentThreadId();
        if (current_thread != thread_id_)
        {
            unexpected_thread_callbacks_.fetch_add(1, std::memory_order_relaxed);
            return;
        }

        std::uint32_t window_thread = 0;
        DWORD window_process = 0;
        const bool has_window = message.hwnd != nullptr;
        if (has_window)
        {
            window_thread = GetWindowThreadProcessId(message.hwnd, &window_process);
            if (window_process != process_id_)
            {
                foreign_window_messages_.fetch_add(1, std::memory_order_relaxed);
                return;
            }
        }
        else
        {
            window_thread = thread_id_;
            thread_messages_without_window_.fetch_add(1, std::memory_order_relaxed);
        }

        const auto slot = event_count_.fetch_add(1, std::memory_order_relaxed);
        if (slot >= events_.size())
        {
            dropped_events_.fetch_add(1, std::memory_order_relaxed);
            return;
        }
        auto& event = events_[slot];
        event.sequence = slot;
        event.dispatch_id = active_dispatch_id_;
        event.track_index = active_track_index_;
        event.completed_blocks = active_completed_blocks_;
        event.source_sample_start = active_source_sample_start_;
        event.sample_count = active_sample_count_;
        event.message = message.message;
        event.message_time_ms = message.time;
        event.hwnd = reinterpret_cast<std::uintptr_t>(message.hwnd);
        event.wparam = static_cast<std::uintptr_t>(message.wParam);
        event.lparam = static_cast<std::uintptr_t>(message.lParam);
        event.thread_id = current_thread;
        event.window_thread_id = window_thread;
        event.window_process_id = has_window ? window_process : 0;
        event.has_window = has_window;
        event.window_belongs_to_current_process = has_window && window_process == process_id_;
        if (has_window && event.window_belongs_to_current_process)
            event.window_proc = static_cast<std::uintptr_t>(GetWindowLongPtrW(message.hwnd, GWLP_WNDPROC));
        if (message.message == WM_TIMER && message.lParam != 0)
            event.timer_proc = static_cast<std::uintptr_t>(message.lParam);
    }

    HHOOK hook_ = nullptr;
    const std::uint32_t process_id_;
    const std::uint32_t thread_id_;
    std::array<Event, event_capacity> events_{};
    std::array<DispatchWindow, dispatch_capacity> dispatches_{};
    std::atomic<std::uint64_t> event_count_{0};
    std::atomic<std::uint64_t> dropped_events_{0};
    std::atomic<std::uint64_t> dispatch_count_{0};
    std::atomic<std::uint64_t> dropped_dispatches_{0};
    std::atomic<std::uint64_t> foreign_window_messages_{0};
    std::atomic<std::uint64_t> thread_messages_without_window_{0};
    std::atomic<std::uint64_t> unexpected_thread_callbacks_{0};
    std::uint32_t active_dispatch_id_ = no_dispatch;
    std::int32_t active_track_index_ = -1;
    std::int64_t active_completed_blocks_ = -1;
    std::int64_t active_source_sample_start_ = -1;
    std::int32_t active_sample_count_ = 0;
    bool dispatch_active_ = false;
};
} // namespace ml::diagnostic
