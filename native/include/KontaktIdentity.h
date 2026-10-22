#pragma once

// Read-only Kontakt 8.10.1 instrument identity reader.
//
// This is deliberately a layout reader, not a process-memory scanner. The
// caller supplies a bounded ReadMemory callback for an owned, live VST3 host
// and independently verified module metadata. It reads only the IComponent ->
// getState receiver -> BEngine chain and the specific tables used by the
// verified Kontakt Lua getters. It never writes to the target process.

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <span>
#include <string>
#include <string_view>
#include <type_traits>
#include <utility>
#include <vector>

namespace ml::kontakt_identity
{
static_assert(sizeof(std::uintptr_t) == 8, "Kontakt identity reader requires x64 pointers");

inline constexpr std::string_view kSupportedVersion = "8.10.1";
// This digest is for the actual loaded Kontakt 8.vst3plugin core module.
// The separate 75cdaa... digest in older probe receipts identifies the
// plugin wrapper and must never be used as a substitute for the loaded core.
inline constexpr std::string_view kSupportedCoreSha256 =
    "6e6abad7518574e4f4e5e20779860f7c789041df63b75e0f03c29a18d701aeab";

inline constexpr std::uint32_t kComponentVtableRva = 0x04E0FB38;
inline constexpr std::uint32_t kBEngineVtableRva = 0x04CD4F30;
inline constexpr std::uint32_t kBEngineTypeDescriptorRva = 0x0A190FF8;
inline constexpr std::uint32_t kComponentGetStateRva = 0x00D5E530;
inline constexpr std::uint32_t kBEngineCountAccessorRva = 0x008A4EF0;
inline constexpr std::uint32_t kBEngineNameAccessorRva = 0x008A62F0;
inline constexpr std::uint32_t kEngineInstrumentTableOffset = 0x2D8F8;
inline constexpr std::uint32_t kComponentStateReceiverOffset = 0xAC0;
inline constexpr std::uint32_t kReceiverEngineOffset = 0x140;
inline constexpr std::uint32_t kInstrumentStatusOffset = 0xE840;
inline constexpr std::uint32_t kBankIndicesVectorOffset = 0x418;
inline constexpr std::uint32_t kInstrumentNameOffset = 0x120A0;
inline constexpr std::uint32_t kTableFirstBankOffset = 0x18;
inline constexpr std::uint32_t kBankFirstRecordOffset = 0x18;
inline constexpr std::uint32_t kRecordsPerBank = 128;
inline constexpr std::uint32_t kBankSlots = 64;
inline constexpr std::uint32_t kMaximumInstrumentIndex =
    kBankSlots * kRecordsPerBank;
// Reader safety ceiling, not a claimed Kontakt vector capacity. Each bank
// vector may map anywhere in the complete 64-bank address space; larger
// vectors fail closed rather than causing unbounded remote reads.
inline constexpr std::uint32_t kMaximumIndexEntriesPerBank =
    kMaximumInstrumentIndex;
inline constexpr std::size_t kMaximumNameBytes = 256;
inline constexpr std::size_t kMaximumStringCapacity = 4096;

// Layout facts are pinned to the actual loaded Kontakt 8.10.1 core module.
// The official count helper counts active banks, while the indices helper
// enumerates each bank's signed-16-bit index vector at +0x418, rebases each
// item by bank*128, and keeps it only when the resolved record pointer exists.
// Thus the number of returned indices may exceed the active-bank count.

enum class Error
{
    ok,
    unsupported_module,
    invalid_address,
    unreadable_memory,
    invalid_rtti,
    invalid_layout,
    index_out_of_range,
    instrument_not_loaded,
    invalid_string,
    invalid_utf8,
    count_mismatch,
    duplicate_index,
};

template <typename T>
struct Result
{
    Error error = Error::ok;
    T value{};

    explicit operator bool() const noexcept { return error == Error::ok; }
};

struct ModulePin
{
    std::uintptr_t base = 0;
    std::uint32_t image_size = 0;
    std::string_view version;
    // SHA-256 of the loaded module path containing base, not a wrapper digest.
    std::string_view binary_sha256;
};

struct InstrumentNames
{
    std::uint32_t count = 0;
    std::vector<std::string> names;
};

namespace detail
{
inline bool is_user_address(std::uintptr_t address, std::size_t size) noexcept
{
    constexpr std::uintptr_t kMinimumUserAddress = 0x10000;
    constexpr std::uintptr_t kMaximumUserAddress = 0x00007FFFFFFFFFFF;
    if (address < kMinimumUserAddress || address > kMaximumUserAddress)
        return false;
    if (size == 0)
        return true;
    return size - 1 <= kMaximumUserAddress - address;
}

inline bool valid_utf8(std::span<const char> bytes) noexcept
{
    std::size_t i = 0;
    while (i < bytes.size())
    {
        const auto lead = static_cast<unsigned char>(bytes[i]);
        if (lead <= 0x7f)
        {
            if (lead == 0)
                return false;
            ++i;
            continue;
        }

        std::size_t count = 0;
        std::uint32_t codepoint = 0;
        if (lead >= 0xc2 && lead <= 0xdf)
        {
            count = 2;
            codepoint = lead & 0x1f;
        }
        else if (lead >= 0xe0 && lead <= 0xef)
        {
            count = 3;
            codepoint = lead & 0x0f;
        }
        else if (lead >= 0xf0 && lead <= 0xf4)
        {
            count = 4;
            codepoint = lead & 0x07;
        }
        else
        {
            return false;
        }
        if (count > bytes.size() - i)
            return false;
        for (std::size_t j = 1; j < count; ++j)
        {
            const auto continuation = static_cast<unsigned char>(bytes[i + j]);
            if ((continuation & 0xc0) != 0x80)
                return false;
            codepoint = (codepoint << 6) | (continuation & 0x3f);
        }
        if ((count == 3 && codepoint < 0x800)
            || (count == 4 && codepoint < 0x10000)
            || (codepoint >= 0xd800 && codepoint <= 0xdfff)
            || codepoint > 0x10ffff)
            return false;
        i += count;
    }
    return true;
}

inline std::uint32_t read_u32(const std::byte* bytes) noexcept
{
    std::uint32_t value = 0;
    std::memcpy(&value, bytes, sizeof(value));
    return value;
}

inline std::uint64_t read_u64(const std::byte* bytes) noexcept
{
    std::uint64_t value = 0;
    std::memcpy(&value, bytes, sizeof(value));
    return value;
}
} // namespace detail

template <typename ReadMemory>
class Reader
{
public:
    Reader(ModulePin module, ReadMemory read_memory)
        : module_(module), read_memory_(std::move(read_memory))
    {
    }

    Result<std::uint32_t> instrument_count(std::uintptr_t component) const
    {
        const auto engine = resolve_engine(component);
        if (!engine)
            return {engine.error, 0};

        const auto table = read_pointer(engine.value + kEngineInstrumentTableOffset);
        if (!table)
            return {table.error, 0};

        std::uint32_t count = 0;
        for (std::uint32_t slot = 0; slot < kBankSlots; ++slot)
        {
            const auto bank_address = table.value + kTableFirstBankOffset
                + static_cast<std::uintptr_t>(slot) * sizeof(std::uintptr_t);
            const auto bank = read_pointer(bank_address);
            if (!bank)
                return {bank.error, 0};

            const auto status = read_u32_at(bank.value + kInstrumentStatusOffset);
            if (!status)
                return {status.error, 0};
            if (status.value != 0)
                ++count;
        }
        return {Error::ok, count};
    }

    Result<std::vector<std::uint32_t>> instrument_indices(
        std::uintptr_t component) const
    {
        const auto engine = resolve_engine(component);
        if (!engine)
            return {engine.error, {}};

        const auto table = read_pointer(engine.value + kEngineInstrumentTableOffset);
        if (!table)
            return {table.error, {}};

        std::vector<std::uint32_t> indices;
        for (std::uint32_t bank_index = 0; bank_index < kBankSlots; ++bank_index)
        {
            const auto bank_pointer_address = table.value + kTableFirstBankOffset
                + static_cast<std::uintptr_t>(bank_index) * sizeof(std::uintptr_t);
            const auto bank = read_pointer(bank_pointer_address);
            if (!bank)
                return {bank.error, {}};

            const auto status = read_u32_at(bank.value + kInstrumentStatusOffset);
            if (!status)
                return {status.error, {}};
            if (status.value == 0)
                continue;

            const auto vector_address = bank.value + kBankIndicesVectorOffset;
            std::uintptr_t begin = 0;
            std::uintptr_t end = 0;
            std::uintptr_t capacity = 0;
            if (!read_exact(vector_address, begin)
                || !read_exact(vector_address + sizeof(std::uintptr_t), end)
                || !read_exact(vector_address + 2 * sizeof(std::uintptr_t), capacity))
                return {Error::unreadable_memory, {}};
            if (begin == 0 && end == 0 && capacity == 0)
                continue;
            if (!detail::is_user_address(begin, 1)
                || !detail::is_user_address(end, 0)
                || !detail::is_user_address(capacity, 0)
                || end < begin || capacity < end)
                return {Error::invalid_layout, {}};

            const auto byte_count = end - begin;
            const auto allocated_bytes = capacity - begin;
            if ((byte_count % sizeof(std::int16_t)) != 0
                || (allocated_bytes % sizeof(std::int16_t)) != 0
                || byte_count > allocated_bytes
                || allocated_bytes > kMaximumIndexEntriesPerBank * sizeof(std::int16_t))
                return {Error::invalid_layout, {}};
            const auto item_count = static_cast<std::size_t>(
                byte_count / sizeof(std::int16_t));
            if (item_count == 0)
                continue;
            std::array<std::int16_t, kMaximumIndexEntriesPerBank> bank_indices{};
            const auto bytes = std::span<std::byte>(
                reinterpret_cast<std::byte*>(bank_indices.data()),
                item_count * sizeof(std::int16_t));
            if (!read_bytes(begin, bytes))
                return {Error::unreadable_memory, {}};

            for (std::size_t i = 0; i < item_count; ++i)
            {
                const auto rebased = static_cast<std::int64_t>(bank_index)
                        * kRecordsPerBank
                    + bank_indices[i];
                // The native helper uses an unsigned `> 0x1fff` branch, so
                // negative results are rejected by the same condition.
                if (rebased < 0 || rebased >= kMaximumInstrumentIndex)
                    continue;

                const auto index = static_cast<std::uint32_t>(rebased);
                const auto resolved_bank = index / kRecordsPerBank;
                const auto record_index = index % kRecordsPerBank;
                const auto resolved_bank_address = table.value + kTableFirstBankOffset
                    + static_cast<std::uintptr_t>(resolved_bank) * sizeof(std::uintptr_t);
                const auto resolved_bank_pointer = read_pointer(resolved_bank_address);
                if (!resolved_bank_pointer)
                    return {resolved_bank_pointer.error, {}};
                const auto record_pointer_address = resolved_bank_pointer.value
                    + kBankFirstRecordOffset
                    + static_cast<std::uintptr_t>(record_index) * sizeof(std::uintptr_t);
                const auto record = read_nullable_pointer(record_pointer_address);
                if (!record)
                    return {record.error, {}};
                if (record.value != 0)
                    indices.push_back(index);
            }
        }
        return {Error::ok, std::move(indices)};
    }

    // `index` must come from Kontakt's verified get_instrument_indices result.
    // Do not synthesize [0, count) here: the native getter's count scans active
    // banks, while names use a 128-record bank/index mapping.
    Result<std::string> instrument_name(std::uintptr_t component,
                                        std::uint32_t index) const
    {
        if (index >= kMaximumInstrumentIndex)
            return {Error::index_out_of_range, {}};

        const auto engine = resolve_engine(component);
        if (!engine)
            return {engine.error, {}};
        const auto table = read_pointer(engine.value + kEngineInstrumentTableOffset);
        if (!table)
            return {table.error, {}};

        const auto bank_index = index / kRecordsPerBank;
        const auto record_index = index % kRecordsPerBank;
        const auto bank_pointer_address = table.value + kTableFirstBankOffset
            + static_cast<std::uintptr_t>(bank_index) * sizeof(std::uintptr_t);
        const auto bank = read_pointer(bank_pointer_address);
        if (!bank)
            return {bank.error, {}};

        const auto record_pointer_address = bank.value + kBankFirstRecordOffset
            + static_cast<std::uintptr_t>(record_index) * sizeof(std::uintptr_t);
        const auto record = read_nullable_pointer(record_pointer_address);
        if (!record)
            return {record.error, {}};
        if (record.value == 0)
            return {Error::instrument_not_loaded, {}};
        return read_msvc_string(record.value + kInstrumentNameOffset);
    }

    // Connects the memory reader to a separately captured official indices
    // getter result. It refuses count/indices disagreement and duplicate slots.
    Result<InstrumentNames> names_for_indices(
        std::uintptr_t component,
        std::span<const std::uint32_t> indices,
        std::uint32_t expected_instrument_count) const
    {
        const auto actual_count = instrument_count(component);
        if (!actual_count)
            return {actual_count.error, {}};
        if (actual_count.value != expected_instrument_count
            || indices.size() > kMaximumInstrumentIndex)
            return {Error::count_mismatch, {}};

        InstrumentNames output;
        output.count = actual_count.value;
        output.names.reserve(indices.size());
        std::vector<std::uint32_t> seen(indices.begin(), indices.end());
        std::sort(seen.begin(), seen.end());
        if (std::adjacent_find(seen.begin(), seen.end()) != seen.end())
            return {Error::duplicate_index, {}};

        for (const auto index : indices)
        {
            auto name = instrument_name(component, index);
            if (!name)
                return {name.error, {}};
            output.names.push_back(std::move(name.value));
        }
        return {Error::ok, std::move(output)};
    }

private:
    bool module_is_supported() const noexcept
    {
        return module_.base != 0
            && module_.image_size > kBEngineTypeDescriptorRva + 64
            && module_.image_size > kBEngineVtableRva + 8
            && module_.image_size > kComponentVtableRva + 8
            && module_.image_size > kComponentGetStateRva + 8
            && module_.version == kSupportedVersion
            && module_.binary_sha256 == kSupportedCoreSha256;
    }

    bool in_module(std::uintptr_t address, std::size_t size) const noexcept
    {
        if (address < module_.base || size == 0)
            return false;
        const auto offset = address - module_.base;
        return offset < module_.image_size
            && size <= static_cast<std::size_t>(module_.image_size - offset);
    }

    template <typename T>
    bool read_exact(std::uintptr_t address, T& output) const
    {
        static_assert(std::is_trivially_copyable_v<T>);
        if (!detail::is_user_address(address, sizeof(T)))
            return false;
        return read_memory_(address, &output, sizeof(T));
    }

    bool read_bytes(std::uintptr_t address, std::span<std::byte> output) const
    {
        if (!detail::is_user_address(address, output.size()))
            return false;
        return read_memory_(address, output.data(), output.size());
    }

    Result<std::uintptr_t> read_pointer(std::uintptr_t address) const
    {
        std::uint64_t pointer = 0;
        if (!read_exact(address, pointer))
            return {Error::unreadable_memory, 0};
        if (pointer > std::numeric_limits<std::uintptr_t>::max()
            || !detail::is_user_address(static_cast<std::uintptr_t>(pointer), 1))
            return {Error::invalid_address, 0};
        return {Error::ok, static_cast<std::uintptr_t>(pointer)};
    }

    Result<std::uintptr_t> read_nullable_pointer(std::uintptr_t address) const
    {
        std::uint64_t pointer = 0;
        if (!read_exact(address, pointer))
            return {Error::unreadable_memory, 0};
        if (pointer == 0)
            return {Error::ok, 0};
        if (pointer > std::numeric_limits<std::uintptr_t>::max()
            || !detail::is_user_address(static_cast<std::uintptr_t>(pointer), 1))
            return {Error::invalid_address, 0};
        return {Error::ok, static_cast<std::uintptr_t>(pointer)};
    }

    Result<std::uint32_t> read_u32_at(std::uintptr_t address) const
    {
        std::uint32_t value = 0;
        if (!read_exact(address, value))
            return {Error::unreadable_memory, 0};
        return {Error::ok, value};
    }

    Result<std::uintptr_t> resolve_engine(std::uintptr_t component) const
    {
        if (!module_is_supported())
            return {Error::unsupported_module, 0};
        if (!detail::is_user_address(component, kComponentStateReceiverOffset + 8))
            return {Error::invalid_address, 0};

        const auto component_vtable = read_pointer(component);
        if (!component_vtable
            || component_vtable.value != module_.base + kComponentVtableRva)
            return {Error::invalid_rtti, 0};

        const auto receiver = read_pointer(component + kComponentStateReceiverOffset);
        if (!receiver)
            return {receiver.error, 0};
        const auto engine = read_pointer(receiver.value + kReceiverEngineOffset);
        if (!engine)
            return {engine.error, 0};
        const auto vtable = read_pointer(engine.value);
        if (!vtable)
            return {vtable.error, 0};
        if (vtable.value != module_.base + kBEngineVtableRva
            || !in_module(vtable.value, sizeof(std::uintptr_t))
            || vtable.value < module_.base + sizeof(std::uintptr_t))
            return {Error::invalid_rtti, 0};

        const auto col_pointer = read_pointer(vtable.value - sizeof(std::uintptr_t));
        if (!col_pointer || !in_module(col_pointer.value, 24))
            return {Error::invalid_rtti, 0};

        std::array<std::byte, 24> col{};
        if (!read_bytes(col_pointer.value, col))
            return {Error::unreadable_memory, 0};
        const auto signature = detail::read_u32(col.data());
        const auto offset = detail::read_u32(col.data() + 4);
        const auto construction_displacement = detail::read_u32(col.data() + 8);
        const auto type_descriptor_rva = detail::read_u32(col.data() + 12);
        const auto class_descriptor_rva = detail::read_u32(col.data() + 16);
        const auto self_rva = detail::read_u32(col.data() + 20);
        const auto col_rva = col_pointer.value - module_.base;
        if (signature != 1 || offset != 0 || construction_displacement != 0
            || type_descriptor_rva != kBEngineTypeDescriptorRva
            || class_descriptor_rva >= module_.image_size
            || self_rva != col_rva)
            return {Error::invalid_rtti, 0};

        constexpr std::string_view kTypeName = ".?AVBEngine@@";
        std::array<std::byte, 16 + kTypeName.size() + 1> type_descriptor{};
        const auto td_address = module_.base + kBEngineTypeDescriptorRva;
        if (!in_module(td_address, type_descriptor.size()))
            return {Error::invalid_rtti, 0};
        if (!read_bytes(td_address, type_descriptor))
            return {Error::unreadable_memory, 0};
        const auto* actual_name = reinterpret_cast<const char*>(type_descriptor.data() + 16);
        if (std::memcmp(actual_name, kTypeName.data(), kTypeName.size()) != 0
            || actual_name[kTypeName.size()] != '\0')
            return {Error::invalid_rtti, 0};
        return {Error::ok, engine.value};
    }

    Result<std::string> read_msvc_string(std::uintptr_t object) const
    {
        // MSVC x64 std::string stores size/capacity at +0x10/+0x18. Capacity
        // <= 15 means the first 16 bytes are the SSO buffer; otherwise qword 0
        // is a pointer. Read exactly size+1 bytes and require its terminator.
        std::array<std::byte, 32> representation{};
        if (!read_bytes(object, representation))
            return {Error::unreadable_memory, {}};
        const auto size = static_cast<std::size_t>(detail::read_u64(representation.data() + 16));
        const auto capacity = static_cast<std::size_t>(detail::read_u64(representation.data() + 24));
        if (size == 0 || size > kMaximumNameBytes || capacity < size
            || capacity > kMaximumStringCapacity)
            return {Error::invalid_string, {}};

        std::vector<char> bytes(size + 1, '\0');
        if (capacity <= 15)
        {
            if (size > 15)
                return {Error::invalid_string, {}};
            std::memcpy(bytes.data(), representation.data(), size + 1);
        }
        else
        {
            const auto data_pointer = detail::read_u64(representation.data());
            if (data_pointer > std::numeric_limits<std::uintptr_t>::max())
                return {Error::invalid_address, {}};
            const auto data_address = static_cast<std::uintptr_t>(data_pointer);
            if (!detail::is_user_address(data_address, size + 1))
                return {Error::invalid_address, {}};
            if (!read_memory_(data_address, bytes.data(), bytes.size()))
                return {Error::unreadable_memory, {}};
        }
        if (bytes[size] != '\0')
            return {Error::invalid_string, {}};
        const std::span<const char> text(bytes.data(), size);
        if (!detail::valid_utf8(text))
            return {Error::invalid_utf8, {}};
        return {Error::ok, std::string(bytes.data(), size)};
    }

    ModulePin module_;
    ReadMemory read_memory_;
};
} // namespace ml::kontakt_identity
