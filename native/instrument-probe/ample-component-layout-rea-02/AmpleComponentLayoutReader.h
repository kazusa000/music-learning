#pragma once

// Independent read-only adapter for two statically verified AGLP component
// views. The original AmpleReadinessReader validates the processor subobject,
// primary vtable, and readiness fields after this adapter normalizes the view.
#include "../AmpleReadinessReader.h"

#include <cstddef>
#include <cstdint>
#include <limits>
#include <type_traits>

namespace ml::ample_layout_diagnostic
{
struct InterfaceViewPin
{
    std::uint32_t vtable_rva = 0;
    std::uintptr_t wrapper_offset = 0;
};

struct LayoutPin
{
    std::string_view product;
    std::string_view version;
    std::string_view module_sha256;
    std::uint32_t image_size = 0;
    std::uint32_t wrapper_vtable_rva = 0;
    InterfaceViewPin secondary_view{};
    ml::ample_readiness::LayoutPin readiness_pin{};
};

struct RuntimeIdentity
{
    std::string_view product;
    std::string_view version;
    std::string_view module_sha256;
    std::uintptr_t module_base = 0;
    std::uint32_t image_size = 0;
    std::uintptr_t component_view = 0;
};

enum class Error
{
    ok,
    unsupported_product,
    unsupported_version,
    unsupported_module,
    invalid_module,
    unreadable_component_view,
    unrecognized_component_vtable,
    wrapper_underflow,
    unreadable_wrapper_vtable,
    wrapper_vtable_mismatch,
};

struct NormalizedComponent
{
    Error error = Error::ok;
    std::uintptr_t observed_vtable = 0;
    std::uintptr_t wrapper = 0;
    std::uintptr_t normalized_offset = 0;
    std::size_t bytes_read = 0;
    std::size_t pointer_reads = 0;
    bool valid = false;
};

struct ReadinessResult
{
    NormalizedComponent component;
    ml::ample_readiness::Snapshot readiness;
    bool readiness_reader_invoked = false;
};

// Exact AGLP 4.0.0 module identity. REA constructor evidence and a read-only
// PE vtable/COL check bind these tables to the JUCE wrapper; only the primary
// table was seen in the preserved runtime capture. The +0x18 view remains a
// statically supported normalization case, not a claimed runtime observation.
inline constexpr LayoutPin kAgLp40InstalledLayout{
    "Ample Guitar LP", "4.0.0",
    "4cd985483c2a7d8da15fb7959f8afb3e2d3c917635718262f1a2f6eab93e850e",
    0x05491000,
    0x051f7a00,
    {0x051f71c0, 0x18},
    {
        "Ample Guitar LP", "4.0.0",
        "4cd985483c2a7d8da15fb7959f8afb3e2d3c917635718262f1a2f6eab93e850e",
        0x051f7a00,
        0x38,
        0x210,
        0x051f8a90,
        0x051f90d0,
        0x250,
        0x3c0,
        0x46fb8,
    },
};

inline bool add_overflow(std::uintptr_t left, std::uintptr_t right,
                         std::uintptr_t& result) noexcept
{
    if (right > std::numeric_limits<std::uintptr_t>::max() - left)
        return true;
    result = left + right;
    return false;
}

template <typename ReadMemory>
NormalizedComponent normalize_component(const RuntimeIdentity& runtime,
                                        const LayoutPin& pin,
                                        ReadMemory&& read_memory)
{
    static_assert(std::is_invocable_r_v<bool, ReadMemory, std::uintptr_t,
                                        void*, std::size_t>);
    NormalizedComponent result;
    if (runtime.product != pin.product)
    {
        result.error = Error::unsupported_product;
        return result;
    }
    if (runtime.version != pin.version)
    {
        result.error = Error::unsupported_version;
        return result;
    }
    if (runtime.module_sha256 != pin.module_sha256)
    {
        result.error = Error::unsupported_module;
        return result;
    }
    if (runtime.module_base == 0 || runtime.image_size == 0
        || runtime.image_size != pin.image_size || runtime.component_view == 0
        || pin.wrapper_vtable_rva == 0
        || pin.wrapper_vtable_rva >= runtime.image_size
        || pin.secondary_view.vtable_rva == 0
        || pin.secondary_view.vtable_rva >= runtime.image_size
        || pin.secondary_view.wrapper_offset == 0)
    {
        result.error = Error::invalid_module;
        return result;
    }
    std::uintptr_t wrapper_vtable_address = 0;
    std::uintptr_t secondary_vtable_address = 0;
    if (add_overflow(runtime.module_base, pin.wrapper_vtable_rva,
                     wrapper_vtable_address)
        || add_overflow(runtime.module_base, pin.secondary_view.vtable_rva,
                        secondary_vtable_address))
    {
        result.error = Error::invalid_module;
        return result;
    }

    auto read_pointer = [&](std::uintptr_t address, std::uintptr_t& value) {
        if (result.pointer_reads >= 2)
            return false;
        ++result.pointer_reads;
        if (!read_memory(address, &value, sizeof(value)))
            return false;
        result.bytes_read += sizeof(value);
        return true;
    };
    if (!read_pointer(runtime.component_view, result.observed_vtable)
        || result.observed_vtable == 0)
    {
        result.error = Error::unreadable_component_view;
        return result;
    }

    if (result.observed_vtable == wrapper_vtable_address)
    {
        result.wrapper = runtime.component_view;
    }
    else if (result.observed_vtable == secondary_vtable_address)
    {
        result.normalized_offset = pin.secondary_view.wrapper_offset;
        if (runtime.component_view < result.normalized_offset)
        {
            result.error = Error::wrapper_underflow;
            return result;
        }
        result.wrapper = runtime.component_view - result.normalized_offset;
        std::uintptr_t normalized_vtable = 0;
        if (!read_pointer(result.wrapper, normalized_vtable))
        {
            result.error = Error::unreadable_wrapper_vtable;
            return result;
        }
        if (normalized_vtable != wrapper_vtable_address)
        {
            result.error = Error::wrapper_vtable_mismatch;
            return result;
        }
    }
    else
    {
        result.error = Error::unrecognized_component_vtable;
        return result;
    }
    result.valid = true;
    return result;
}

template <typename ReadMemory>
ReadinessResult inspect_readiness(const RuntimeIdentity& runtime,
                                  const LayoutPin& pin,
                                  ReadMemory&& read_memory)
{
    ReadinessResult result;
    result.component = normalize_component(runtime, pin, read_memory);
    if (!result.component.valid)
        return result;
    const ml::ample_readiness::RuntimeIdentity normalized{
        runtime.product, runtime.version, runtime.module_sha256,
        runtime.module_base, runtime.image_size, result.component.wrapper};
    result.readiness = ml::ample_readiness::inspect(
        normalized, pin.readiness_pin, read_memory);
    result.readiness_reader_invoked = true;
    return result;
}
} // namespace ml::ample_layout_diagnostic
