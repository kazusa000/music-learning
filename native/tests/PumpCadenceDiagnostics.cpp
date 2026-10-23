#include "../instrument-probe/PumpCadenceDiagnostics.h"

#include <cstdlib>
#include <iostream>

namespace
{
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
    using ml::pump_cadence::SampleRange;
    using ml::pump_cadence::TraceBudget;
    using ml::pump_cadence::dispatch_due;

    constexpr std::int64_t block_size = 512;
    check(!dispatch_due(0, 64), "no dispatch before the first completed block");
    check(!dispatch_due(63, 64), "64-block schedule must not dispatch early");
    check(dispatch_due(64, 64), "64-block schedule dispatches after block 64");
    check(!dispatch_due(31, 32), "32-block schedule must not dispatch early");
    check(dispatch_due(32, 32), "32-block schedule dispatches after block 32");
    check(dispatch_due(64, 32), "32-block schedule dispatches again after block 64");
    check(!dispatch_due(64, 0), "zero interval is never treated as a pump");

    // A scheduling boundary may shorten an individual processBlock. The pump
    // remains block-counted, and its sample coordinate is the actual boundary.
    std::int64_t cursor = 0;
    std::int64_t observed_dispatch_sample = -1;
    for (std::uint64_t completed = 1; completed <= 64; ++completed)
    {
        const auto actual_count = completed == 7 ? 128 : block_size;
        cursor += actual_count;
        if (dispatch_due(completed, 64)) observed_dispatch_sample = cursor;
    }
    check(observed_dispatch_sample == 32384,
          "dispatch coordinates must accumulate actual block counts, including a short block");

    const auto end_64 = static_cast<std::int64_t>(64) * block_size;
    const auto end_32 = static_cast<std::int64_t>(32) * block_size;
    check(end_64 == 32768, "64 x 512 must map to actual frame 32768");
    check(end_32 == 16384, "32 x 512 must map to actual frame 16384");

    // The last rendered block may extend beyond a content-only duration when
    // tail/latency is included. Preserve the actual start and count verbatim.
    constexpr std::int64_t project_content_samples = 220500;
    constexpr SampleRange final_block{220160, 512};
    check(final_block.end_exclusive() == 220672,
          "diagnostic end must be the actual block end, not a clamped value");
    check(final_block.end_exclusive() > project_content_samples,
          "raw block end must preserve legitimate tail/latency beyond content");

    TraceBudget bounded{2};
    check(bounded.reserve(), "first bounded trace record is stored");
    check(bounded.reserve(), "second bounded trace record is stored");
    check(!bounded.reserve(), "records beyond the cap are not stored");
    check(bounded.recorded_blocks() == 2 && bounded.dropped_blocks() == 1,
          "bounded trace overflow is explicit rather than silent");
    check(bounded.maximum_blocks() == 2, "trace cap remains observable");

    std::cout << "pump cadence diagnostic fixture passed\n";
    return 0;
}
