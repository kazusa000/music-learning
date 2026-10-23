#pragma once

#include <condition_variable>
#include <cstdint>
#include <exception>
#include <functional>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <utility>

namespace ml::diagnostic
{

// A synchronous handoff to one persistent callback thread. The caller owns
// every object captured by a submitted task and waits until that task returns,
// so AudioBuffer views and MidiBuffers may safely remain stack-owned.
//
// This diagnostic deliberately has no local timeout or cancellation path: the
// enclosing isolated host is bounded by its owning process supervisor. If a
// plugin callback hangs, unwinding these references or detaching this thread
// would be unsafe; the supervisor must fail the whole owned host process.
class PersistentDspThread final
{
public:
    PersistentDspThread()
    {
        // Start only from the constructor body: all mutexes, condition
        // variables and worker-owned state have completed initialization.
        worker = std::thread([this] { run(); });
        std::unique_lock lock(mutex);
        readyCondition.wait(lock, [this] { return ready; });
    }

    ~PersistentDspThread() noexcept
    {
        stopAndJoin();
    }

    PersistentDspThread(const PersistentDspThread&) = delete;
    PersistentDspThread& operator=(const PersistentDspThread&) = delete;
    PersistentDspThread(PersistentDspThread&&) = delete;
    PersistentDspThread& operator=(PersistentDspThread&&) = delete;

    void process(std::function<void()> task)
    {
        if (!task)
            throw std::invalid_argument("DSP task is empty");

        std::unique_lock lock(mutex);
        if (stopping || joined)
            throw std::logic_error("DSP thread is stopped");
        if (inFlight)
            throw std::logic_error("Concurrent DSP task submission is forbidden");

        taskBody = std::move(task);
        taskError = nullptr;
        resultReady = false;
        inFlight = true;
        ++submittedCount;
        workCondition.notify_one();

        // Keep the caller's captured buffer/MIDI references alive until the
        // DSP callback has definitely returned. A hung callback is handled by
        // the external owned-host deadline, which terminates the isolated host.
        resultCondition.wait(lock, [this] { return resultReady; });
        const auto error = taskError;
        inFlight = false;
        resultCondition.notify_all();
        lock.unlock();

        if (error)
            std::rethrow_exception(error);
    }

    void stopAndJoin() noexcept
    {
        {
            std::unique_lock lock(mutex);
            if (joined)
                return;

            // Normally process() has already returned. If a destructor is
            // reached while another caller owns an in-flight callback, wait
            // for it rather than invalidating data still used by the plugin.
            resultCondition.wait(lock, [this] { return !inFlight; });
            stopping = true;
            workCondition.notify_one();
        }

        if (worker.joinable())
            worker.join();

        std::lock_guard lock(mutex);
        joined = true;
    }

    std::thread::id threadId() const
    {
        std::lock_guard lock(mutex);
        return workerId;
    }

    std::uint64_t submitted() const
    {
        std::lock_guard lock(mutex);
        return submittedCount;
    }

    std::uint64_t completed() const
    {
        std::lock_guard lock(mutex);
        return completedCount;
    }

    bool isJoined() const
    {
        std::lock_guard lock(mutex);
        return joined;
    }

private:
    void run() noexcept
    {
        std::unique_lock lock(mutex);
        workerId = std::this_thread::get_id();
        ready = true;
        readyCondition.notify_all();

        for (;;)
        {
            workCondition.wait(lock, [this] { return stopping || static_cast<bool>(taskBody); });
            if (stopping && !taskBody)
                break;

            auto task = std::move(taskBody);
            taskBody = {};
            lock.unlock();

            std::exception_ptr error;
            try
            {
                task();
            }
            catch (...)
            {
                error = std::current_exception();
            }
            task = {};

            lock.lock();
            taskError = error;
            ++completedCount;
            resultReady = true;
            resultCondition.notify_all();
        }
    }

    mutable std::mutex mutex;
    std::condition_variable workCondition;
    std::condition_variable resultCondition;
    std::condition_variable readyCondition;
    std::function<void()> taskBody;
    std::exception_ptr taskError;
    std::thread::id workerId;
    std::uint64_t submittedCount = 0;
    std::uint64_t completedCount = 0;
    bool ready = false;
    bool resultReady = false;
    bool inFlight = false;
    bool stopping = false;
    bool joined = false;
    // Keep the worker last so even future constructor changes cannot start it
    // before the state it accesses has been initialized.
    std::thread worker;
};

} // namespace ml::diagnostic
