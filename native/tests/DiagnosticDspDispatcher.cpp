#include "../audio-host/DiagnosticDspDispatcher.h"

#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <exception>
#include <future>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace
{
void check(bool condition, const char* message)
{
    if (!condition)
        throw std::runtime_error(message);
}

struct MidiEvent
{
    int offset = 0;
    int note = 0;
    int channel = 1;
    bool noteOn = false;
    float velocity = 0.0f;
};

bool sameMidi(const std::vector<MidiEvent>& left, const std::vector<MidiEvent>& right)
{
    if (left.size() != right.size())
        return false;
    for (std::size_t index = 0; index < left.size(); ++index)
    {
        const auto& a = left[index];
        const auto& b = right[index];
        if (a.offset != b.offset || a.note != b.note || a.channel != b.channel
            || a.noteOn != b.noteOn || a.velocity != b.velocity)
            return false;
    }
    return true;
}

void testStableThreadAndExactPayload()
{
    ml::diagnostic::PersistentDspThread dispatcher;
    const auto callerThread = std::this_thread::get_id();
    const auto dspThread = dispatcher.threadId();
    check(dspThread != std::thread::id{} && dspThread != callerThread,
          "DSP callback must have a distinct persistent owner thread");

    struct BlockJob
    {
        std::vector<float> samples;
        std::vector<MidiEvent> midi;
        std::vector<MidiEvent> observedMidi;
        std::uint64_t dispatchOrdinal = 0;
        std::thread::id callbackThread;
        int callbackCount = 0;
    };

    const std::vector<MidiEvent> events{
        {0, 48, 1, true, 0.35f}, {31, 48, 1, false, 0.0f},
        {64, 60, 1, true, 0.65f}, {95, 60, 1, false, 0.0f},
        {128, 72, 1, true, 0.90f}, {159, 72, 1, false, 0.0f}};
    bool offlineFlag = true; // Mirrors the already-configured offline host flag.
    std::vector<std::uint64_t> callbackOrder;
    std::uint64_t ordinal = 0;

    for (const auto blockSize : {64, 32, 64, 32})
    {
        BlockJob job;
        job.samples.resize(static_cast<std::size_t>(blockSize));
        job.midi = events;
        job.dispatchOrdinal = ++ordinal;
        const auto expectedOrdinal = ordinal;

        dispatcher.process([&job, &callbackOrder, &offlineFlag, expectedOrdinal, dspThread]
        {
            check(std::this_thread::get_id() == dspThread,
                  "Each block must execute on the same DSP thread");
            check(offlineFlag, "Dispatcher must not change the host offline flag");
            ++job.callbackCount;
            job.callbackThread = std::this_thread::get_id();
            job.observedMidi = job.midi;
            for (std::size_t i = 0; i < job.samples.size(); ++i)
                job.samples[i] = static_cast<float>(expectedOrdinal * 1000 + i);
            callbackOrder.push_back(expectedOrdinal);
        });

        check(job.callbackCount == 1, "Each submitted block must execute exactly once");
        check(job.callbackThread == dspThread, "Callback thread identity must remain stable");
        check(sameMidi(job.midi, job.observedMidi), "MIDI offsets, notes and velocities must be unchanged");
        check(job.samples.size() == static_cast<std::size_t>(blockSize)
                  && job.samples.front() == static_cast<float>(expectedOrdinal * 1000)
                  && job.samples.back() == static_cast<float>(expectedOrdinal * 1000 + blockSize - 1),
              "Buffer size and writes must survive the synchronous handoff exactly");
        check(offlineFlag, "Offline mode must remain unchanged after each callback");
    }

    check(callbackOrder == std::vector<std::uint64_t>({1, 2, 3, 4}),
          "Callbacks must remain in render order");
    check(dispatcher.submitted() == 4 && dispatcher.completed() == 4,
          "Submitted and completed callback counts must agree");
    dispatcher.stopAndJoin();
    check(dispatcher.isJoined(), "Idle DSP thread must stop and join before plugin teardown");
}

void testExceptionReturnsOnCaller()
{
    ml::diagnostic::PersistentDspThread dispatcher;
    const auto caller = std::this_thread::get_id();
    std::thread::id caughtOn;
    bool caught = false;
    try
    {
        dispatcher.process([] { throw std::runtime_error("fixture DSP failure"); });
    }
    catch (const std::runtime_error& error)
    {
        caught = std::string(error.what()) == "fixture DSP failure";
        caughtOn = std::this_thread::get_id();
    }
    check(caught && caughtOn == caller, "DSP exception must be rethrown on the submitting thread");

    int laterCalls = 0;
    dispatcher.process([&laterCalls] { ++laterCalls; });
    check(laterCalls == 1 && dispatcher.submitted() == 2 && dispatcher.completed() == 2,
          "A completed throwing callback must not corrupt the next synchronous job");
    dispatcher.stopAndJoin();
    check(dispatcher.isJoined(), "DSP thread must be joined after exception recovery");
}

void testConcurrentRequestRejectedAndJoinWaitsForCompletion()
{
    ml::diagnostic::PersistentDspThread dispatcher;
    std::promise<void> enteredPromise;
    auto entered = enteredPromise.get_future();
    std::promise<void> releasePromise;
    auto release = releasePromise.get_future().share();
    std::atomic<int> callbackCount{0};

    std::thread submitter([&]
    {
        dispatcher.process([&]
        {
            callbackCount.fetch_add(1, std::memory_order_relaxed);
            enteredPromise.set_value();
            release.wait();
        });
    });

    entered.wait();
    bool rejected = false;
    try
    {
        dispatcher.process([&callbackCount] { callbackCount.fetch_add(100, std::memory_order_relaxed); });
    }
    catch (const std::logic_error&)
    {
        rejected = true;
    }
    check(rejected, "A second in-flight block must be rejected, not run concurrently");

    auto stopped = std::async(std::launch::async, [&dispatcher]
    {
        dispatcher.stopAndJoin();
    });
    const auto shutdownWaited = stopped.wait_for(std::chrono::milliseconds(10))
        == std::future_status::timeout;

    releasePromise.set_value();
    submitter.join();
    stopped.get();
    check(shutdownWaited,
          "Shutdown must not return while the callback still owns caller data");
    check(callbackCount.load(std::memory_order_relaxed) == 1,
          "Rejected work must not execute and the accepted job must execute once");
    check(dispatcher.isJoined(), "DSP thread must join after the callback completes");
}
}

int main()
{
    try
    {
        testStableThreadAndExactPayload();
        testExceptionReturnsOnCaller();
        testConcurrentRequestRejectedAndJoinWaitsForCompletion();
        std::cout << "Diagnostic DSP thread contract passed\n";
        return 0;
    }
    catch (const std::exception& error)
    {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
