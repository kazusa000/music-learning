#include "../instrument-probe/Vst3RestartCallbackTrace.h"

#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

namespace
{
struct MockProcessor
{
    int calls = 0;
    int setProcessing(bool processing) noexcept
    {
        ++calls;
        return processing ? 37 : -12;
    }
};

struct MockComponent
{
    int calls = 0;
    int setActive(bool active) noexcept
    {
        ++calls;
        return active ? 21 : -9;
    }
};

void check(bool condition, const char* message)
{
    if (!condition)
    {
        std::cerr << message << '\n';
        std::exit(1);
    }
}
}

int main()
{
    using namespace ml::vst3_restart_trace;

    Recorder trace;
    trace.clear();
    trace.set_context(3, 1, 64, 32768);
    trace.record(EventKind::restart_requested, 0x12, DispatchRoute::async_queued);
    trace.record(EventKind::restart_applied, 0x16, DispatchRoute::async_applied);

    std::array<Event, 8> events {};
    auto count = trace.snapshot(events.data(), events.size());
    check(count == 2, "two callback events are observable after the dispatch returns");
    check(events[0].sequence == 1 && events[1].sequence == 2,
          "event order is preserved by reservation sequence");
    check(events[0].raw_flags == 0x12 && events[0].route == DispatchRoute::async_queued,
          "the original request flags and queued route are preserved");
    check(events[1].raw_flags == 0x16 && events[1].route == DispatchRoute::async_applied,
          "the coalesced applied flags and async route are preserved");
    check(events[0].track_index == 3 && events[0].plugin_index == 1
              && events[0].completed_blocks == 64 && events[0].sample_position == 32768,
          "events retain the nearest render context");
    check(events[0].thread_id == events[1].thread_id
              && events[0].monotonic_ticks != 0 && events[1].monotonic_ticks >= events[0].monotonic_ticks,
          "events carry stable thread identity and ordered monotonic time");
    check(std::string(event_name(EventKind::set_processing_end)) == "set_processing_end",
          "event names are stable for the report schema");
    check(std::string(route_name(DispatchRoute::message_thread_immediate))
              == "message_thread_immediate",
          "dispatch route names are stable for the report schema");

    trace.clear();
    MockProcessor processor;
    MockComponent component;
    const auto processingResult = call_set_processing(&processor, false);
    const auto activeResult = call_set_active(&component, true);
    check(processingResult == -12 && activeResult == 21
              && processor.calls == 1 && component.calls == 1,
          "instrumentation preserves each lifecycle callback and exact result");
    count = trace.snapshot(events.data(), events.size());
    check(count == 4
              && events[0].kind == EventKind::set_processing_begin
              && events[1].kind == EventKind::set_processing_end
              && events[2].kind == EventKind::set_active_begin
              && events[3].kind == EventKind::set_active_end,
          "lifecycle wrappers record ordered begin and end events");
    check(events[0].value == 0 && events[1].scalar == -12.0
              && events[2].value == 1 && events[3].scalar == 21.0,
          "lifecycle trace retains the requested state and exact return code");

    trace.clear();
    constexpr int threadCount = 4;
    constexpr int eventsPerThread = 32;
    std::vector<std::thread> writers;
    for (int t = 0; t < threadCount; ++t)
        writers.emplace_back([&trace, t]
        {
            for (int i = 0; i < eventsPerThread; ++i)
                trace.record(EventKind::set_processing_end, 0,
                             DispatchRoute::none, t * eventsPerThread + i, 0.0);
        });
    for (auto& writer : writers)
        writer.join();

    std::array<Event, Recorder::capacity> concurrentEvents {};
    count = trace.snapshot(concurrentEvents.data(), concurrentEvents.size());
    check(count == threadCount * eventsPerThread,
          "concurrent callback writers retain every event within the fixed cap");
    std::vector<std::uint64_t> sequences;
    sequences.reserve(count);
    for (std::size_t i = 0; i < count; ++i)
        sequences.push_back(concurrentEvents[i].sequence);
    std::sort(sequences.begin(), sequences.end());
    check(std::adjacent_find(sequences.begin(), sequences.end()) == sequences.end(),
          "concurrent writers claim unique event sequence slots");
    check(trace.dropped_events() == 0, "the concurrency fixture stays below the trace cap");

    trace.clear();
    for (std::size_t i = 0; i < Recorder::capacity + 5; ++i)
        trace.record(EventKind::restart_requested, static_cast<std::int32_t>(i));
    count = trace.snapshot(concurrentEvents.data(), concurrentEvents.size());
    check(count == Recorder::capacity, "trace output is bounded by its fixed slot count");
    check(trace.dropped_events() == 5, "overflow is explicit rather than silently discarded");
    check(Recorder::tick_period_numerator() > 0 && Recorder::tick_period_denominator() > 0,
          "monotonic tick units are reportable");

    std::cout << "VST3 restart callback trace contract passed\n";
    return 0;
}
