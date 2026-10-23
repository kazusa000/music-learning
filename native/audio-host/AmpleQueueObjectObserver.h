#pragma once

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <juce_core/juce_core.h>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <initializer_list>
#include <limits>
#include <string_view>
#include <vector>

namespace ml::diagnostic
{
struct AmpleQueueLayoutPin
{
    std::string_view module_sha256;
    std::uint32_t image_size = 0;
    std::uint32_t queue_global_rva = 0;
    std::uint32_t array_pointer_offset = 0x38;
    std::uint32_t capacity_offset = 0x40;
    std::uint32_t count_offset = 0x44;
    std::uint32_t async_updater_message_vtable_rva = 0x051ff4d8;
    std::uint32_t async_owner_offset = 0x10;
    std::uint32_t async_pending_offset = 0x18;
};

inline constexpr AmpleQueueLayoutPin kAmpleGuitarLp40QueuePin{
    "4cd985483c2a7d8da15fb7959f8afb3e2d3c917635718262f1a2f6eab93e850e",
    88674304,
    0x05414710,
    0x38,
    0x40,
    0x44,
    0x051ff4d8,
    0x10,
    0x18,
};

// Reads only the queue root and the bounded list it names. No callback is
// called, no message is removed, and no vendor memory is ever written.
class AmpleQueueObjectObserver final
{
public:
    static constexpr std::size_t maximum_dispatches = 1024;
    static constexpr std::size_t maximum_objects_per_phase = 32;
    static constexpr std::size_t maximum_object_raw_bytes = 64;

    struct DispatchToken
    {
        std::uint32_t id = 0;
    };

    static bool stable_snapshot(std::uintptr_t root_before,
                                std::uintptr_t root_after,
                                const std::array<std::uint8_t, 16>& header_before,
                                const std::array<std::uint8_t, 16>& header_after) noexcept
    {
        return root_before == root_after
            && std::memcmp(header_before.data(), header_after.data(), header_before.size()) == 0;
    }

    explicit AmpleQueueObjectObserver(
        AmpleQueueLayoutPin pin = kAmpleGuitarLp40QueuePin)
        : pin_(pin)
    {
        records_.reserve(maximum_dispatches);
    }

    AmpleQueueObjectObserver(const AmpleQueueObjectObserver&) = delete;
    AmpleQueueObjectObserver& operator=(const AmpleQueueObjectObserver&) = delete;

    DispatchToken begin_dispatch(std::uint32_t message_dispatch_id,
                                 std::int32_t track_index,
                                 std::uint64_t completed_blocks,
                                 std::int64_t source_sample_start,
                                 std::int32_t sample_count)
    {
        const auto index = dispatch_count_++;
        if (index >= maximum_dispatches)
        {
            ++dropped_dispatches_;
            return {};
        }
        Record record;
        record.id = static_cast<std::uint32_t>(index + 1);
        record.message_dispatch_id = message_dispatch_id;
        record.track_index = track_index;
        record.completed_blocks = completed_blocks;
        record.source_sample_start = source_sample_start;
        record.sample_count = sample_count;
        records_.push_back(record);
        return {record.id};
    }

    void capture_before(DispatchToken token, const juce::Array<juce::var>& readiness)
    {
        if (token.id == 0 || token.id > records_.size())
            return;
        capture(token, readiness, records_[token.id - 1].before);
    }

    void capture_after(DispatchToken token, const juce::Array<juce::var>& readiness)
    {
        if (token.id == 0 || token.id > records_.size())
            return;
        capture(token, readiness, records_[token.id - 1].after);
    }

    juce::var report() const
    {
        juce::Array<juce::var> dispatch_rows;
        dispatch_rows.ensureStorageAllocated(static_cast<int>(records_.size()));
        for (const auto& record : records_)
        {
            auto* row = new juce::DynamicObject();
            row->setProperty("dispatch_id", static_cast<int>(record.id));
            row->setProperty("message_observer_dispatch_id",
                             static_cast<int>(record.message_dispatch_id));
            row->setProperty("track_index", record.track_index);
            row->setProperty("completed_blocks",
                             static_cast<juce::int64>(record.completed_blocks));
            row->setProperty("source_sample_start",
                             static_cast<juce::int64>(record.source_sample_start));
            row->setProperty("sample_count", record.sample_count);
            row->setProperty("source_sample_end_exclusive",
                static_cast<juce::int64>(record.source_sample_start + record.sample_count));
            row->setProperty("before", serialize(record.before));
            row->setProperty("after", serialize(record.after));
            dispatch_rows.add(juce::var(row));
        }

        auto* result = new juce::DynamicObject();
        result->setProperty("schema_version", 1);
        result->setProperty("diagnostic_only", true);
        result->setProperty("read_only", true);
        result->setProperty("module_sha256_pin", juce::String(pin_.module_sha256.data(),
            static_cast<int>(pin_.module_sha256.size())));
        result->setProperty("module_image_size_pin", static_cast<juce::int64>(pin_.image_size));
        result->setProperty("queue_global_rva_hex", hex(pin_.queue_global_rva));
        result->setProperty("queue_global_semantics", "pointer slot; observer reads the queue pointer before touching its object");
        result->setProperty("recognized_async_updater_vtable_rva_hex",
                            hex(pin_.async_updater_message_vtable_rva));
        result->setProperty("recognized_async_owner_offset_hex", hex(pin_.async_owner_offset));
        result->setProperty("recognized_async_pending_offset_hex", hex(pin_.async_pending_offset));
        auto* offsets = new juce::DynamicObject();
        offsets->setProperty("array_pointer", hex(pin_.array_pointer_offset));
        offsets->setProperty("capacity_u32", hex(pin_.capacity_offset));
        offsets->setProperty("count_u32", hex(pin_.count_offset));
        result->setProperty("queue_header_offsets", juce::var(offsets));
        result->setProperty("header_sampling", "same 16-byte header read before and after bounded object reads; equal bytes required for stable=true");
        result->setProperty("maximum_objects_per_phase",
                            static_cast<int>(maximum_objects_per_phase));
        result->setProperty("maximum_raw_bytes_per_object",
                            static_cast<int>(maximum_object_raw_bytes));
        result->setProperty("callback_invoked", false);
        result->setProperty("memory_written", false);
        result->setProperty("arbitrary_memory_scan", false);
        result->setProperty("dispatches", dispatch_rows);
        result->setProperty("dispatches_recorded", static_cast<juce::int64>(records_.size()));
        result->setProperty("dispatches_seen", static_cast<juce::int64>(dispatch_count_));
        result->setProperty("dispatches_dropped", static_cast<juce::int64>(dropped_dispatches_));
        return juce::var(result);
    }

private:
    enum class ReadStatus : std::uint8_t
    {
        ok,
        null_address,
        address_overflow,
        query_failed,
        outside_region,
        not_committed,
        unreadable_protection,
        read_failed,
        partial_read,
    };

    struct QueueHeader
    {
        std::uintptr_t array = 0;
        std::uint32_t capacity = 0;
        std::uint32_t count = 0;
    };
    static_assert(sizeof(QueueHeader) == 16);

    struct ObjectRecord
    {
        std::uintptr_t address = 0;
        std::uintptr_t vtable = 0;
        std::uintptr_t virtual_slot_address = 0;
        std::uintptr_t callback = 0;
        ReadStatus object_status = ReadStatus::null_address;
        ReadStatus callback_status = ReadStatus::null_address;
        std::uint8_t raw_bytes_read = 0;
        std::array<std::uint8_t, maximum_object_raw_bytes> raw{};
        bool recognized_async_updater = false;
        ReadStatus owner_status = ReadStatus::null_address;
        std::uintptr_t owner = 0;
        std::uint8_t owner_raw_bytes_read = 0;
        std::array<std::uint8_t, maximum_object_raw_bytes> owner_raw{};
        std::uintptr_t owner_vtable = 0;
        std::uintptr_t owner_callback = 0;
        std::uintptr_t owner_virtual_slot_address = 0;
        ReadStatus owner_callback_status = ReadStatus::null_address;
        std::int32_t pending_value = 0;
        ReadStatus pending_status = ReadStatus::null_address;
    };

    struct PhaseSnapshot
    {
        bool captured = false;
        bool header_stable = false;
        bool queue_root_stable = false;
        std::string_view status = "not_captured";
        std::uintptr_t module_base = 0;
        std::uint32_t image_size = 0;
        std::uintptr_t queue_root_slot_address = 0;
        std::uintptr_t queue_root_before = 0;
        std::uintptr_t queue_root_after = 0;
        std::uintptr_t queue_address = 0;
        QueueHeader header_before{};
        QueueHeader header_after{};
        ReadStatus queue_root_before_status = ReadStatus::null_address;
        ReadStatus queue_root_after_status = ReadStatus::null_address;
        ReadStatus header_before_status = ReadStatus::null_address;
        ReadStatus header_after_status = ReadStatus::null_address;
        std::uint32_t truncated_count = 0;
        std::uint32_t objects_seen = 0;
        std::array<ObjectRecord, maximum_objects_per_phase> objects{};
    };

    struct Record
    {
        std::uint32_t id = 0;
        std::uint32_t message_dispatch_id = 0;
        std::int32_t track_index = -1;
        std::uint64_t completed_blocks = 0;
        std::int64_t source_sample_start = -1;
        std::int32_t sample_count = 0;
        PhaseSnapshot before{};
        PhaseSnapshot after{};
    };

    struct ModuleLocation
    {
        bool resolved = false;
        std::uintptr_t module_base = 0;
        std::uintptr_t rva = 0;
        juce::String path;
    };

    static constexpr std::uint32_t maximum_reported_capacity = 1u << 24;

    static const char* read_status_name(ReadStatus status) noexcept
    {
        switch (status)
        {
            case ReadStatus::ok: return "ok";
            case ReadStatus::null_address: return "null_address";
            case ReadStatus::address_overflow: return "address_overflow";
            case ReadStatus::query_failed: return "virtual_query_failed";
            case ReadStatus::outside_region: return "outside_memory_region";
            case ReadStatus::not_committed: return "memory_not_committed";
            case ReadStatus::unreadable_protection: return "memory_not_readable";
            case ReadStatus::read_failed: return "read_process_memory_failed";
            case ReadStatus::partial_read: return "partial_read";
        }
        return "unknown";
    }

    static juce::String hex(std::uintptr_t value)
    {
        constexpr char digits[] = "0123456789abcdef";
        std::array<char, 2 + sizeof(value) * 2 + 1> buffer{};
        buffer[0] = '0';
        buffer[1] = 'x';
        for (std::size_t index = 0; index < sizeof(value) * 2; ++index)
        {
            const auto shift = (sizeof(value) * 2 - index - 1) * 4;
            buffer[index + 2] = digits[(value >> shift) & 0xf];
        }
        return juce::String::fromUTF8(buffer.data(), static_cast<int>(buffer.size() - 1));
    }

    static juce::String raw_hex(const std::array<std::uint8_t, maximum_object_raw_bytes>& bytes,
                                std::uint8_t bytes_read)
    {
        constexpr char digits[] = "0123456789abcdef";
        juce::String output;
        for (std::size_t index = 0; index < bytes_read; ++index)
        {
            output += digits[(bytes[index] >> 4) & 0xf];
            output += digits[bytes[index] & 0xf];
        }
        return output;
    }

    static bool readable_protection(DWORD protection) noexcept
    {
        const auto base = protection & 0xff;
        return base == PAGE_READONLY || base == PAGE_READWRITE
            || base == PAGE_WRITECOPY || base == PAGE_EXECUTE_READ
            || base == PAGE_EXECUTE_READWRITE || base == PAGE_EXECUTE_WRITECOPY;
    }

    static ReadStatus read_exact(std::uintptr_t address, void* destination,
                                 std::size_t bytes) noexcept
    {
        if (address == 0 || destination == nullptr || bytes == 0)
            return ReadStatus::null_address;
        if (bytes - 1 > std::numeric_limits<std::uintptr_t>::max() - address)
            return ReadStatus::address_overflow;
        auto* output = static_cast<std::uint8_t*>(destination);
        std::size_t copied = 0;
        while (copied < bytes)
        {
            const auto current = address + copied;
            MEMORY_BASIC_INFORMATION region{};
            if (VirtualQuery(reinterpret_cast<const void*>(current), &region,
                             sizeof(region)) != sizeof(region))
                return ReadStatus::query_failed;
            const auto region_begin = reinterpret_cast<std::uintptr_t>(region.BaseAddress);
            if (region.RegionSize > std::numeric_limits<std::uintptr_t>::max() - region_begin)
                return ReadStatus::address_overflow;
            const auto region_end = region_begin + region.RegionSize;
            if (current < region_begin || current >= region_end)
                return ReadStatus::outside_region;
            if (region.State != MEM_COMMIT)
                return ReadStatus::not_committed;
            if ((region.Protect & PAGE_GUARD) != 0 || !readable_protection(region.Protect))
                return ReadStatus::unreadable_protection;
            const auto chunk = (std::min)(bytes - copied,
                static_cast<std::size_t>(region_end - current));
            SIZE_T actual = 0;
            if (!ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<const void*>(current),
                                   output + copied, chunk, &actual))
                return ReadStatus::read_failed;
            if (actual != chunk)
                return ReadStatus::partial_read;
            copied += chunk;
        }
        return ReadStatus::ok;
    }

    static ModuleLocation resolve_module(std::uintptr_t address)
    {
        ModuleLocation result;
        if (address == 0)
            return result;
        HMODULE module = nullptr;
        constexpr auto flags = GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS
                             | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT;
        if (!GetModuleHandleExW(flags, reinterpret_cast<LPCWSTR>(address), &module))
            return result;
        std::array<wchar_t, 32768> path{};
        const auto length = GetModuleFileNameW(module, path.data(),
                                               static_cast<DWORD>(path.size()));
        if (length == 0 || length >= path.size())
            return result;
        result.resolved = true;
        result.module_base = reinterpret_cast<std::uintptr_t>(module);
        result.rva = address - result.module_base;
        result.path = juce::String(path.data(), static_cast<int>(length));
        return result;
    }

    static juce::var module_json(const ModuleLocation& module)
    {
        return make_object({{"resolved", module.resolved},
                       {"module_base_hex", module.resolved ? hex(module.module_base) : juce::String()},
                       {"rva_hex", module.resolved ? hex(module.rva) : juce::String()},
                       {"path", module.path}});
    }

    static juce::var serialize_header(const QueueHeader& header)
    {
        return make_object({{"array_pointer_hex", hex(header.array)},
                       {"capacity", static_cast<int>(header.capacity)},
                       {"count", static_cast<int>(header.count)}});
    }

    static juce::var serialize_object(const ObjectRecord& item, std::size_t index)
    {
        const auto vtable_module = resolve_module(item.vtable);
        const auto callback_module = resolve_module(item.callback);
        const auto owner_vtable_module = resolve_module(item.owner_vtable);
        const auto owner_callback_module = resolve_module(item.owner_callback);
        return make_object({
            {"index", static_cast<int>(index)},
            {"object_address_hex", hex(item.address)},
            {"object_read_status", read_status_name(item.object_status)},
            {"raw_bytes_read", static_cast<int>(item.raw_bytes_read)},
            {"raw_bytes_hex", raw_hex(item.raw, item.raw_bytes_read)},
            {"vtable_address_hex", hex(item.vtable)},
            {"vtable_module", module_json(vtable_module)},
            {"virtual_slot_plus_8_address_hex", hex(item.virtual_slot_address)},
            {"virtual_slot_plus_8_callback_address_hex", hex(item.callback)},
            {"virtual_slot_plus_8_read_status", read_status_name(item.callback_status)},
            {"callback_module", module_json(callback_module)},
            {"recognized_async_updater_message", item.recognized_async_updater},
            {"async_pending_read_status", item.recognized_async_updater
                ? read_status_name(item.pending_status) : "not_applicable_unrecognized_vtable"},
            {"async_pending_original_value", item.pending_value},
            {"async_owner_read_status", item.recognized_async_updater
                ? read_status_name(item.owner_status) : "not_applicable_unrecognized_vtable"},
            {"async_owner_address_hex", hex(item.owner)},
            {"async_owner_raw_bytes_read", static_cast<int>(item.owner_raw_bytes_read)},
            {"async_owner_raw_bytes_hex", raw_hex(item.owner_raw, item.owner_raw_bytes_read)},
            {"async_owner_vtable_address_hex", hex(item.owner_vtable)},
            {"async_owner_vtable_module", module_json(owner_vtable_module)},
            {"async_owner_virtual_slot_plus_8_address_hex", hex(item.owner_virtual_slot_address)},
            {"async_owner_virtual_slot_plus_8_callback_address_hex", hex(item.owner_callback)},
            {"async_owner_virtual_slot_plus_8_read_status",
             read_status_name(item.owner_callback_status)},
            {"async_owner_callback_module", module_json(owner_callback_module)},
            {"object_type", item.recognized_async_updater
                ? "REA-pinned VST3 AsyncUpdater message" : "unknown"},
            {"owner", item.recognized_async_updater
                ? "bounded owner pointer at pinned +0x10" : "unknown"},
        });
    }

    static juce::var serialize(const PhaseSnapshot& snapshot)
    {
        juce::Array<juce::var> objects;
        objects.ensureStorageAllocated(static_cast<int>(snapshot.objects_seen));
        for (std::size_t index = 0; index < snapshot.objects_seen; ++index)
            objects.add(serialize_object(snapshot.objects[index], index));
        return make_object({
            {"captured", snapshot.captured},
            {"status", juce::String(snapshot.status.data(), static_cast<int>(snapshot.status.size()))},
            {"module_base_hex", hex(snapshot.module_base)},
            {"module_image_size", static_cast<juce::int64>(snapshot.image_size)},
            {"queue_root_slot_address_hex", hex(snapshot.queue_root_slot_address)},
            {"queue_root_before_hex", hex(snapshot.queue_root_before)},
            {"queue_root_after_hex", hex(snapshot.queue_root_after)},
            {"queue_root_before_read_status", read_status_name(snapshot.queue_root_before_status)},
            {"queue_root_after_read_status", read_status_name(snapshot.queue_root_after_status)},
            {"queue_address_hex", hex(snapshot.queue_address)},
            {"header_before_read_status", read_status_name(snapshot.header_before_status)},
            {"header_after_read_status", read_status_name(snapshot.header_after_status)},
            {"header_before", serialize_header(snapshot.header_before)},
            {"header_after", serialize_header(snapshot.header_after)},
            {"header_stable", snapshot.header_stable},
            {"queue_root_stable", snapshot.queue_root_stable},
            {"objects_validated_against_stable_queue",
             snapshot.header_stable && snapshot.queue_root_stable},
            {"count_truncated", static_cast<juce::int64>(snapshot.truncated_count)},
            {"objects_recorded", static_cast<juce::int64>(snapshot.objects_seen)},
            {"objects", objects},
        });
    }

    static bool parse_module_base(const juce::DynamicObject* object,
                                  std::uintptr_t& base)
    {
        if (object == nullptr)
            return false;
        const auto text = object->getProperty("module_base").toString();
        if (text.isEmpty())
            return false;
        base = static_cast<std::uintptr_t>(text.getHexValue64());
        return base != 0;
    }

    static juce::var make_object(
        std::initializer_list<std::pair<juce::String, juce::var>> properties)
    {
        auto* value = new juce::DynamicObject();
        for (const auto& property : properties)
            value->setProperty(property.first, property.second);
        return juce::var(value);
    }

    void capture(DispatchToken token, const juce::Array<juce::var>& readiness,
                 PhaseSnapshot& output)
    {
        if (token.id == 0 || token.id > records_.size())
            return;
        output.captured = true;
        const juce::DynamicObject* ample = nullptr;
        for (const auto& value : readiness)
        {
            auto* candidate = value.getDynamicObject();
            if (candidate != nullptr
                && candidate->getProperty("product").toString() == "Ample Guitar LP")
            {
                if (ample != nullptr)
                {
                    output.status = "ambiguous_multiple_ample_plugins";
                    return;
                }
                ample = candidate;
            }
        }
        if (ample == nullptr)
        {
            output.status = "ample_plugin_not_found_in_existing_readiness";
            return;
        }
        if (ample->getProperty("manufacturer").toString() != "Ample Sound"
            || ample->getProperty("version").toString() != "4.0.0"
            || ample->getProperty("module_sha256").toString()
                != juce::String(pin_.module_sha256.data(), static_cast<int>(pin_.module_sha256.size()))
            || ample->getProperty("image_size").toString().getIntValue() != static_cast<int>(pin_.image_size))
        {
            output.status = "unsupported_or_mismatched_module_identity";
            return;
        }
        if (!static_cast<bool>(ample->getProperty("ready")))
        {
            output.status = "existing_readiness_not_ready";
            return;
        }
        if (pin_.array_pointer_offset + sizeof(std::uintptr_t) != pin_.capacity_offset
            || pin_.capacity_offset + sizeof(std::uint32_t) != pin_.count_offset)
        {
            output.status = "unsupported_queue_header_offsets";
            return;
        }
        if (!parse_module_base(ample, output.module_base))
        {
            output.status = "module_base_unavailable_in_existing_readiness";
            return;
        }
        output.image_size = pin_.image_size;
        if (output.module_base > std::numeric_limits<std::uintptr_t>::max() - output.image_size
            || output.image_size < sizeof(std::uintptr_t)
            || pin_.queue_global_rva > output.image_size - sizeof(std::uintptr_t)
            || static_cast<std::uintptr_t>(pin_.queue_global_rva) >
                std::numeric_limits<std::uintptr_t>::max() - output.module_base
            || pin_.array_pointer_offset > 0x10000
            || pin_.count_offset > 0x10000 - sizeof(std::uint32_t)
            || pin_.async_updater_message_vtable_rva >= output.image_size
            || pin_.async_owner_offset > maximum_object_raw_bytes - sizeof(std::uintptr_t)
            || pin_.async_pending_offset > maximum_object_raw_bytes - sizeof(std::int32_t)
            || pin_.array_pointer_offset + sizeof(std::uintptr_t) != pin_.capacity_offset
            || pin_.capacity_offset + sizeof(std::uint32_t) != pin_.count_offset)
        {
            output.status = "queue_rva_outside_pinned_module";
            return;
        }
        output.queue_root_slot_address = output.module_base + pin_.queue_global_rva;
        output.queue_root_before_status = read_exact(output.queue_root_slot_address,
            &output.queue_root_before, sizeof(output.queue_root_before));
        if (output.queue_root_before_status != ReadStatus::ok)
        {
            output.status = "queue_root_first_read_failed";
            return;
        }
        if (output.queue_root_before == 0)
        {
            output.status = "queue_root_is_null";
            return;
        }
        output.queue_address = output.queue_root_before;
        if (output.queue_address > std::numeric_limits<std::uintptr_t>::max()
                                       - pin_.array_pointer_offset
            || output.queue_address + pin_.array_pointer_offset >
                std::numeric_limits<std::uintptr_t>::max() - sizeof(QueueHeader))
        {
            output.status = "queue_header_address_overflow";
            return;
        }

        const auto header_address = output.queue_address + pin_.array_pointer_offset;
        output.header_before_status = read_exact(header_address, &output.header_before,
                                                 sizeof(output.header_before));
        if (output.header_before_status != ReadStatus::ok)
        {
            output.status = "header_first_read_failed";
            return;
        }
        if (output.header_before.count > output.header_before.capacity
            || output.header_before.capacity > maximum_reported_capacity
            || (output.header_before.count != 0 && output.header_before.array == 0))
        {
            output.status = "invalid_queue_header_bounds";
            return;
        }

        const auto wanted = (std::min)(static_cast<std::size_t>(output.header_before.count),
                                       maximum_objects_per_phase);
        output.truncated_count = output.header_before.count - static_cast<std::uint32_t>(wanted);
        for (std::size_t index = 0; index < wanted; ++index)
        {
            auto& item = output.objects[index];
            const auto displacement = index * sizeof(std::uintptr_t);
            if (displacement > std::numeric_limits<std::uintptr_t>::max()
                                   - output.header_before.array)
            {
                item.object_status = ReadStatus::address_overflow;
                ++output.objects_seen;
                continue;
            }
            std::uintptr_t object_address = 0;
            item.object_status = read_exact(output.header_before.array + displacement,
                                            &object_address, sizeof(object_address));
            item.address = object_address;
            if (item.object_status == ReadStatus::ok && object_address == 0)
                item.object_status = ReadStatus::null_address;
            if (item.object_status == ReadStatus::ok)
            {
                item.object_status = read_exact(object_address, item.raw.data(), item.raw.size());
                if (item.object_status == ReadStatus::ok)
                {
                    item.raw_bytes_read = static_cast<std::uint8_t>(item.raw.size());
                    std::memcpy(&item.vtable, item.raw.data(), sizeof(item.vtable));
                    if (item.vtable != 0
                        && item.vtable <= std::numeric_limits<std::uintptr_t>::max()
                                               - sizeof(std::uintptr_t))
                    {
                        item.virtual_slot_address = item.vtable + sizeof(std::uintptr_t);
                        item.callback_status = read_exact(item.virtual_slot_address,
                            &item.callback, sizeof(item.callback));
                    }
                    else
                        item.callback_status = item.vtable == 0
                            ? ReadStatus::null_address : ReadStatus::address_overflow;
                    if (output.module_base <= std::numeric_limits<std::uintptr_t>::max()
                                                   - pin_.async_updater_message_vtable_rva
                        && item.vtable == output.module_base
                            + pin_.async_updater_message_vtable_rva)
                    {
                        item.recognized_async_updater = true;
                        if (item.address > std::numeric_limits<std::uintptr_t>::max()
                                               - pin_.async_owner_offset)
                            item.owner_status = ReadStatus::address_overflow;
                        else
                        {
                            item.owner_status = read_exact(item.address + pin_.async_owner_offset, &item.owner,
                                                           sizeof(item.owner));
                        }
                        if (item.address > std::numeric_limits<std::uintptr_t>::max()
                                               - pin_.async_pending_offset)
                            item.pending_status = ReadStatus::address_overflow;
                        else
                        {
                            item.pending_status = read_exact(item.address + pin_.async_pending_offset,
                                &item.pending_value, sizeof(item.pending_value));
                        }
                        if (item.owner_status == ReadStatus::ok && item.owner != 0)
                        {
                            item.owner_status = read_exact(item.owner, item.owner_raw.data(),
                                                           item.owner_raw.size());
                            if (item.owner_status == ReadStatus::ok)
                            {
                                item.owner_raw_bytes_read = static_cast<std::uint8_t>(
                                    item.owner_raw.size());
                                std::memcpy(&item.owner_vtable, item.owner_raw.data(),
                                            sizeof(item.owner_vtable));
                                if (item.owner_vtable != 0
                                    && item.owner_vtable <= std::numeric_limits<std::uintptr_t>::max()
                                                                 - sizeof(std::uintptr_t))
                                {
                                    item.owner_virtual_slot_address = item.owner_vtable
                                        + sizeof(std::uintptr_t);
                                    item.owner_callback_status = read_exact(
                                        item.owner_virtual_slot_address, &item.owner_callback,
                                        sizeof(item.owner_callback));
                                }
                                else
                                    item.owner_callback_status = item.owner_vtable == 0
                                        ? ReadStatus::null_address : ReadStatus::address_overflow;
                            }
                        }
                        else if (item.owner_status == ReadStatus::ok)
                            item.owner_status = ReadStatus::null_address;
                    }
                }
            }
            ++output.objects_seen;
        }

        output.header_after_status = read_exact(header_address, &output.header_after,
                                                sizeof(output.header_after));
        output.queue_root_after_status = read_exact(output.queue_root_slot_address,
            &output.queue_root_after, sizeof(output.queue_root_after));
        if (output.header_after_status != ReadStatus::ok
            || output.queue_root_after_status != ReadStatus::ok)
        {
            output.status = "second_header_or_queue_root_read_failed";
            return;
        }
        output.queue_root_stable = output.queue_root_before == output.queue_root_after;
        output.header_stable = std::memcmp(&output.header_before, &output.header_after,
                                           sizeof(QueueHeader)) == 0;
        std::array<std::uint8_t, sizeof(QueueHeader)> header_before_bytes{};
        std::array<std::uint8_t, sizeof(QueueHeader)> header_after_bytes{};
        std::memcpy(header_before_bytes.data(), &output.header_before, sizeof(QueueHeader));
        std::memcpy(header_after_bytes.data(), &output.header_after, sizeof(QueueHeader));
        const auto stable = stable_snapshot(output.queue_root_before, output.queue_root_after,
                                            header_before_bytes, header_after_bytes);
        output.status = stable ? "stable_snapshot"
            : (output.queue_root_stable ? "queue_header_changed_during_object_reads"
                                        : "queue_root_changed_during_object_reads");
    }

    const AmpleQueueLayoutPin pin_;
    std::vector<Record> records_;
    std::uint64_t dispatch_count_ = 0;
    std::uint64_t dropped_dispatches_ = 0;
};
} // namespace ml::diagnostic
