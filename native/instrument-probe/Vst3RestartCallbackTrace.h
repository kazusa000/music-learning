#pragma once

#include <array>
#include <atomic>
#include <bit>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <thread>

namespace ml::vst3_restart_trace
{

enum class EventKind : std::uint8_t
{
    restart_requested,
    restart_applied,
    host_restart_begin,
    host_restart_end,
    host_reset_begin,
    host_reset_end,
    host_release_begin,
    host_release_end,
    host_prepare_begin,
    host_prepare_end,
    processor_reset_begin,
    processor_reset_end,
    processor_release_begin,
    processor_release_end,
    processor_prepare_begin,
    processor_prepare_noop,
    processor_prepare_end,
    set_processing_begin,
    set_processing_end,
    set_active_begin,
    set_active_end
};

enum class DispatchRoute : std::int32_t
{
    none = 0,
    message_thread_immediate = 1,
    async_queued = 2,
    async_applied = 3
};

struct Event
{
    std::uint64_t sequence = 0;
    std::uint64_t monotonic_ticks = 0;
    std::uint64_t thread_id = 0;
    std::uint64_t completed_blocks = 0;
    std::int64_t sample_position = -1;
    std::int32_t track_index = -1;
    std::int32_t plugin_index = -1;
    std::int32_t raw_flags = 0;
    std::int32_t value = 0;
    double scalar = 0.0;
    EventKind kind = EventKind::restart_requested;
    DispatchRoute route = DispatchRoute::none;
};

// Fixed-capacity, allocation-free callback recorder for an isolated diagnostic
// build. Writers claim unique slots with one atomic increment; every field is
// atomic so a concurrent final snapshot cannot race an in-flight callback.
class Recorder
{
public:
    static constexpr std::size_t capacity = 1024;
    static_assert(std::atomic<std::uint64_t>::is_always_lock_free,
                  "The diagnostic callback recorder requires lock-free 64-bit atomics");
    static_assert(std::atomic<std::int32_t>::is_always_lock_free,
                  "The diagnostic callback recorder requires lock-free 32-bit atomics");

    void clear() noexcept
    {
        next.store(0, std::memory_order_relaxed);
        dropped.store(0, std::memory_order_relaxed);
        track.store(-1, std::memory_order_relaxed);
        plugin.store(-1, std::memory_order_relaxed);
        block.store(0, std::memory_order_relaxed);
        sample.store(-1, std::memory_order_relaxed);
        for (auto& slot : slots)
            slot.committed.store(0, std::memory_order_relaxed);
    }

    void set_context(std::int32_t trackIndex, std::int32_t pluginIndex,
                     std::uint64_t completedBlocks, std::int64_t samplePosition) noexcept
    {
        track.store(trackIndex, std::memory_order_relaxed);
        plugin.store(pluginIndex, std::memory_order_relaxed);
        block.store(completedBlocks, std::memory_order_relaxed);
        sample.store(samplePosition, std::memory_order_relaxed);
    }

    void record(EventKind kind, std::int32_t rawFlags = 0,
                DispatchRoute route = DispatchRoute::none,
                std::int32_t value = 0, double scalar = 0.0) noexcept
    {
        const auto ordinal = next.fetch_add(1, std::memory_order_relaxed);
        if (ordinal >= capacity)
        {
            dropped.fetch_add(1, std::memory_order_relaxed);
            return;
        }

        auto& slot = slots[static_cast<std::size_t>(ordinal)];
        slot.monotonicTicks.store(clock_ticks(), std::memory_order_relaxed);
        slot.threadId.store(thread_token(), std::memory_order_relaxed);
        slot.completedBlocks.store(block.load(std::memory_order_relaxed), std::memory_order_relaxed);
        slot.samplePosition.store(sample.load(std::memory_order_relaxed), std::memory_order_relaxed);
        slot.trackIndex.store(track.load(std::memory_order_relaxed), std::memory_order_relaxed);
        slot.pluginIndex.store(plugin.load(std::memory_order_relaxed), std::memory_order_relaxed);
        slot.rawFlags.store(rawFlags, std::memory_order_relaxed);
        slot.value.store(value, std::memory_order_relaxed);
        slot.scalarBits.store(std::bit_cast<std::uint64_t>(scalar), std::memory_order_relaxed);
        slot.kind.store(static_cast<std::uint8_t>(kind), std::memory_order_relaxed);
        slot.route.store(static_cast<std::int32_t>(route), std::memory_order_relaxed);
        slot.committed.store(ordinal + 1, std::memory_order_release);
    }

    std::size_t snapshot(Event* destination, std::size_t destinationCapacity) const noexcept
    {
        if (destination == nullptr || destinationCapacity == 0)
            return 0;

        const auto reserved = next.load(std::memory_order_acquire);
        const auto limit = reserved < capacity ? reserved : capacity;
        const auto count = limit < destinationCapacity ? limit : destinationCapacity;
        std::size_t copied = 0;
        for (std::size_t i = 0; i < count; ++i)
        {
            const auto& slot = slots[i];
            if (slot.committed.load(std::memory_order_acquire) != i + 1)
                continue;

            auto& event = destination[copied++];
            event.sequence = i + 1;
            event.monotonic_ticks = slot.monotonicTicks.load(std::memory_order_relaxed);
            event.thread_id = slot.threadId.load(std::memory_order_relaxed);
            event.completed_blocks = slot.completedBlocks.load(std::memory_order_relaxed);
            event.sample_position = slot.samplePosition.load(std::memory_order_relaxed);
            event.track_index = slot.trackIndex.load(std::memory_order_relaxed);
            event.plugin_index = slot.pluginIndex.load(std::memory_order_relaxed);
            event.raw_flags = slot.rawFlags.load(std::memory_order_relaxed);
            event.value = slot.value.load(std::memory_order_relaxed);
            event.scalar = std::bit_cast<double>(slot.scalarBits.load(std::memory_order_relaxed));
            event.kind = static_cast<EventKind>(slot.kind.load(std::memory_order_relaxed));
            event.route = static_cast<DispatchRoute>(slot.route.load(std::memory_order_relaxed));
        }
        return copied;
    }

    std::uint64_t dropped_events() const noexcept
    {
        return dropped.load(std::memory_order_relaxed);
    }

    std::uint64_t reserved_events() const noexcept
    {
        return next.load(std::memory_order_acquire);
    }

    static constexpr std::int64_t tick_period_numerator() noexcept
    {
        return std::chrono::steady_clock::period::num;
    }

    static constexpr std::int64_t tick_period_denominator() noexcept
    {
        return std::chrono::steady_clock::period::den;
    }

private:
    struct Slot
    {
        std::atomic<std::uint64_t> committed { 0 };
        std::atomic<std::uint64_t> monotonicTicks { 0 };
        std::atomic<std::uint64_t> threadId { 0 };
        std::atomic<std::uint64_t> completedBlocks { 0 };
        std::atomic<std::int64_t> samplePosition { -1 };
        std::atomic<std::int32_t> trackIndex { -1 };
        std::atomic<std::int32_t> pluginIndex { -1 };
        std::atomic<std::int32_t> rawFlags { 0 };
        std::atomic<std::int32_t> value { 0 };
        std::atomic<std::uint64_t> scalarBits { std::bit_cast<std::uint64_t>(0.0) };
        std::atomic<std::uint8_t> kind { 0 };
        std::atomic<std::int32_t> route { 0 };
    };

    static std::uint64_t clock_ticks() noexcept
    {
        return static_cast<std::uint64_t>(std::chrono::steady_clock::now().time_since_epoch().count());
    }

    static std::uint64_t thread_token() noexcept
    {
        return static_cast<std::uint64_t>(std::hash<std::thread::id>{}(std::this_thread::get_id()));
    }

    std::array<Slot, capacity> slots {};
    std::atomic<std::uint64_t> next { 0 };
    std::atomic<std::uint64_t> dropped { 0 };
    std::atomic<std::int32_t> track { -1 };
    std::atomic<std::int32_t> plugin { -1 };
    std::atomic<std::uint64_t> block { 0 };
    std::atomic<std::int64_t> sample { -1 };
};

inline Recorder global_recorder;
inline thread_local std::uint32_t dispatch_route = static_cast<std::uint32_t>(DispatchRoute::none);

inline Recorder& recorder() noexcept { return global_recorder; }
inline std::uint32_t& current_dispatch_route() noexcept { return dispatch_route; }

inline DispatchRoute current_route() noexcept
{
    return static_cast<DispatchRoute>(current_dispatch_route());
}

class ScopedDispatchRoute
{
public:
    explicit ScopedDispatchRoute(DispatchRoute next) noexcept
        : previous(current_dispatch_route())
    {
        current_dispatch_route() = static_cast<std::uint32_t>(next);
    }

    ~ScopedDispatchRoute() noexcept { current_dispatch_route() = previous; }
    ScopedDispatchRoute(const ScopedDispatchRoute&) = delete;
    ScopedDispatchRoute& operator=(const ScopedDispatchRoute&) = delete;

private:
    std::uint32_t previous;
};

template <typename Processor>
auto call_set_processing(Processor* processor, bool processing)
    -> decltype(processor->setProcessing(processing))
{
    recorder().record(EventKind::set_processing_begin, 0, current_route(), processing ? 1 : 0);
    auto result = processor->setProcessing(processing);
    recorder().record(EventKind::set_processing_end, 0, current_route(), processing ? 1 : 0,
                      static_cast<double>(result));
    return result;
}

template <typename Component>
auto call_set_active(Component* component, bool active)
    -> decltype(component->setActive(active))
{
    recorder().record(EventKind::set_active_begin, 0, current_route(), active ? 1 : 0);
    auto result = component->setActive(active);
    recorder().record(EventKind::set_active_end, 0, current_route(), active ? 1 : 0,
                      static_cast<double>(result));
    return result;
}

inline const char* event_name(EventKind kind) noexcept
{
    switch (kind)
    {
        case EventKind::restart_requested: return "restart_requested";
        case EventKind::restart_applied: return "restart_applied";
        case EventKind::host_restart_begin: return "host_restart_begin";
        case EventKind::host_restart_end: return "host_restart_end";
        case EventKind::host_reset_begin: return "host_reset_begin";
        case EventKind::host_reset_end: return "host_reset_end";
        case EventKind::host_release_begin: return "host_release_begin";
        case EventKind::host_release_end: return "host_release_end";
        case EventKind::host_prepare_begin: return "host_prepare_begin";
        case EventKind::host_prepare_end: return "host_prepare_end";
        case EventKind::processor_reset_begin: return "processor_reset_begin";
        case EventKind::processor_reset_end: return "processor_reset_end";
        case EventKind::processor_release_begin: return "processor_release_begin";
        case EventKind::processor_release_end: return "processor_release_end";
        case EventKind::processor_prepare_begin: return "processor_prepare_begin";
        case EventKind::processor_prepare_noop: return "processor_prepare_noop";
        case EventKind::processor_prepare_end: return "processor_prepare_end";
        case EventKind::set_processing_begin: return "set_processing_begin";
        case EventKind::set_processing_end: return "set_processing_end";
        case EventKind::set_active_begin: return "set_active_begin";
        case EventKind::set_active_end: return "set_active_end";
    }
    return "unknown";
}

inline const char* route_name(DispatchRoute route) noexcept
{
    switch (route)
    {
        case DispatchRoute::none: return "none";
        case DispatchRoute::message_thread_immediate: return "message_thread_immediate";
        case DispatchRoute::async_queued: return "async_queued";
        case DispatchRoute::async_applied: return "async_applied";
    }
    return "unknown";
}

} // namespace ml::vst3_restart_trace
