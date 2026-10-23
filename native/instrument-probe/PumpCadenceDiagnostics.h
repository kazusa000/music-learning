#pragma once

#include <cstddef>
#include <cstdint>

namespace ml::pump_cadence
{

// The diagnostic cadence counts completed process blocks. A dispatch happens
// after a complete block, so the reported sample boundary is the actual end
// of that block rather than a rounded project-duration boundary.
constexpr bool dispatch_due(std::uint64_t completed_blocks,
                            std::uint32_t interval) noexcept
{
    return interval != 0 && completed_blocks != 0
        && completed_blocks % interval == 0;
}

struct SampleRange
{
    std::int64_t start = 0;
    std::int64_t count = 0;

    constexpr std::int64_t end_exclusive() const noexcept
    {
        return start + count;
    }
};

// A bounded recorder must expose omissions. It never replaces an observed
// sample position with a project-duration clamp or silently drops overflow.
class TraceBudget
{
public:
    explicit constexpr TraceBudget(std::size_t limit) noexcept : maximum(limit) {}

    constexpr bool reserve() noexcept
    {
        if (recorded < maximum)
        {
            ++recorded;
            return true;
        }
        ++dropped;
        return false;
    }

    constexpr std::size_t recorded_blocks() const noexcept { return recorded; }
    constexpr std::size_t dropped_blocks() const noexcept { return dropped; }
    constexpr std::size_t maximum_blocks() const noexcept { return maximum; }

private:
    std::size_t maximum = 0;
    std::size_t recorded = 0;
    std::size_t dropped = 0;
};

} // namespace ml::pump_cadence
