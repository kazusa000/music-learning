#pragma once

// Read-only, version-pinned Ample loading-state reader. This header contains
// no process access or plugin calls; the caller supplies a bounded reader for
// an owned, live plugin component.

#include <cstddef>
#include <cstdint>
#include <limits>
#include <string>
#include <string_view>
#include <type_traits>

namespace ml::ample_readiness
{
static_assert(sizeof(std::uintptr_t) == 8,
              "Ample readiness layout reader requires x64 pointers");

enum class Error
{
    ok,
    unsupported_product,
    unsupported_version,
    unsupported_module,
    invalid_module,
    invalid_component,
    component_vtable_mismatch,
    unsupported_component_mapping,
    invalid_primary_object,
    unreadable_memory,
    invalid_loading_byte,
    invalid_error_count,
    invalid_status_string,
};

struct LayoutPin
{
    std::string_view product;
    std::string_view version;
    std::string_view module_sha256;
    std::uint32_t component_vtable_rva = 0;
    // Static-analysis values are provenance only. They are deliberately not
    // consulted to form an object pointer until the mapping is independently
    // attested for this exact module and component vtable.
    std::uintptr_t candidate_component_to_primary_offset = 0;
    std::uint32_t candidate_secondary_vtable_rva = 0;
    bool component_mapping_verified = false;
    std::uintptr_t component_to_primary_offset = 0;
    std::uint32_t primary_vtable_rva = 0;
    std::uintptr_t loading_byte_offset = 0;
    std::uintptr_t status_string_offset = 0;
    std::uintptr_t loader_error_count_offset = 0;
};

struct RuntimeIdentity
{
    std::string_view product;
    std::string_view version;
    std::string_view module_sha256;
    std::uintptr_t module_base = 0;
    std::uint32_t image_size = 0;
    std::uintptr_t component = 0;
};

struct Snapshot
{
    Error error = Error::ok;
    std::uintptr_t component_vtable = 0;
    std::uintptr_t primary_processor = 0;
    std::uint8_t loading_byte = 0;
    std::int32_t loader_error_count = 0;
    std::string status;
    bool fields_read = false;
    bool ready = false;
};

inline constexpr LayoutPin kAGLP40{
    "Ample Guitar LP",
    "4.0.0",
    "4cd985483c2a7d8da15fb7959f8afb3e2d3c917635718262f1a2f6eab93e850e",
    // Static queryInterface analysis reports the wrapper's IComponent vtable.
    // This does not prove its relationship to the processor object.
    0x051f71c0,
    0x210,
    0x051f8770,
    false,
    0,
    0,
    0x250,
    0x3c0,
    0x46fb8,
};

inline constexpr std::string_view error_name(Error error) noexcept
{
    switch (error)
    {
        case Error::ok: return "ok";
        case Error::unsupported_product: return "unsupported_product";
        case Error::unsupported_version: return "unsupported_version";
        case Error::unsupported_module: return "unsupported_module";
        case Error::invalid_module: return "invalid_module";
        case Error::invalid_component: return "invalid_component";
        case Error::component_vtable_mismatch: return "component_vtable_mismatch";
        case Error::unsupported_component_mapping: return "unsupported_component_mapping";
        case Error::invalid_primary_object: return "invalid_primary_object";
        case Error::unreadable_memory: return "unreadable_memory";
        case Error::invalid_loading_byte: return "invalid_loading_byte";
        case Error::invalid_error_count: return "invalid_error_count";
        case Error::invalid_status_string: return "invalid_status_string";
    }
    return "unknown";
}

inline bool add_overflow(std::uintptr_t left, std::uintptr_t right,
                         std::uintptr_t& result) noexcept
{
    if (right > std::numeric_limits<std::uintptr_t>::max() - left)
        return true;
    result = left + right;
    return false;
}

template <typename ReadMemory>
Snapshot inspect(const RuntimeIdentity& runtime, const LayoutPin& pin,
                 ReadMemory&& read_memory)
{
    static_assert(std::is_invocable_r_v<bool, ReadMemory, std::uintptr_t,
                                        void*, std::size_t>);
    Snapshot result;
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
        || runtime.component == 0 || pin.component_vtable_rva == 0
        || pin.component_vtable_rva >= runtime.image_size)
    {
        result.error = Error::invalid_module;
        return result;
    }

    if (!read_memory(runtime.component, &result.component_vtable,
                     sizeof(result.component_vtable))
        || result.component_vtable == 0)
    {
        result.error = Error::invalid_component;
        return result;
    }

    std::uintptr_t expected_component_vtable = 0;
    if (add_overflow(runtime.module_base, pin.component_vtable_rva,
                     expected_component_vtable))
    {
        result.error = Error::invalid_module;
        return result;
    }
    if (result.component_vtable != expected_component_vtable)
    {
        result.error = Error::component_vtable_mismatch;
        return result;
    }

    // Static reports conflict: queryInterface returns the wrapper at +0x38,
    // while another candidate primary/secondary relation suggests +0x210.
    // Neither has been tied to a unique primary object by runtime evidence.
    // Fail closed until that mapping and primary vtable are independently pinned.
    if (!pin.component_mapping_verified
        || pin.component_to_primary_offset == 0
        || pin.primary_vtable_rva == 0
        || pin.primary_vtable_rva >= runtime.image_size)
    {
        result.error = Error::unsupported_component_mapping;
        return result;
    }
    if (runtime.component < pin.component_to_primary_offset)
    {
        result.error = Error::invalid_primary_object;
        return result;
    }
    result.primary_processor = runtime.component - pin.component_to_primary_offset;
    std::uintptr_t component_from_primary = 0;
    if (add_overflow(result.primary_processor, pin.component_to_primary_offset,
                     component_from_primary)
        || component_from_primary != runtime.component)
    {
        result.error = Error::invalid_primary_object;
        return result;
    }

    std::uintptr_t primary_vtable = 0;
    std::uintptr_t expected_primary_vtable = 0;
    if (!read_memory(result.primary_processor, &primary_vtable,
                     sizeof(primary_vtable))
        || add_overflow(runtime.module_base, pin.primary_vtable_rva,
                        expected_primary_vtable)
        || primary_vtable != expected_primary_vtable)
    {
        result.error = Error::invalid_primary_object;
        return result;
    }

    std::uintptr_t loading_address = 0;
    std::uintptr_t status_address = 0;
    std::uintptr_t errors_address = 0;
    if (add_overflow(result.primary_processor, pin.loading_byte_offset,
                     loading_address)
        || add_overflow(result.primary_processor, pin.status_string_offset,
                        status_address)
        || add_overflow(result.primary_processor,
                        pin.loader_error_count_offset, errors_address))
    {
        result.error = Error::invalid_primary_object;
        return result;
    }

    if (!read_memory(loading_address, &result.loading_byte,
                     sizeof(result.loading_byte))
        || !read_memory(errors_address, &result.loader_error_count,
                        sizeof(result.loader_error_count)))
    {
        result.error = Error::unreadable_memory;
        return result;
    }
    if (result.loading_byte > 1)
    {
        result.error = Error::invalid_loading_byte;
        return result;
    }
    if (result.loader_error_count < 0
        || result.loader_error_count > 1000000)
    {
        result.error = Error::invalid_error_count;
        return result;
    }

    // JUCE String stores a UTF-8 character pointer. Read the pointer and at
    // most 64 bytes through the caller's checked reader; never call plugin or
    // JUCE code against an object owned by the plugin.
    std::uintptr_t status_chars = 0;
    if (!read_memory(status_address, &status_chars, sizeof(status_chars))
        || status_chars == 0)
    {
        result.error = Error::invalid_status_string;
        return result;
    }
    constexpr std::size_t max_status_bytes = 64;
    bool terminated = false;
    for (std::size_t i = 0; i < max_status_bytes; ++i)
    {
        std::uintptr_t character_address = 0;
        char value = 0;
        if (add_overflow(status_chars, i, character_address)
            || !read_memory(character_address, &value, sizeof(value)))
        {
            result.error = Error::invalid_status_string;
            return result;
        }
        if (value == '\0')
        {
            terminated = true;
            break;
        }
        const auto byte = static_cast<unsigned char>(value);
        if (byte < 0x20 || byte > 0x7e)
        {
            result.error = Error::invalid_status_string;
            return result;
        }
        result.status.push_back(value);
    }
    if (!terminated)
    {
        result.error = Error::invalid_status_string;
        return result;
    }

    result.fields_read = true;
    result.ready = result.loading_byte == 0
        && result.loader_error_count == 0 && result.status == "Success";
    return result;
}
} // namespace ml::ample_readiness
