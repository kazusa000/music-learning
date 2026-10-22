#include "KontaktIdentity.h"

#include <array>
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <map>
#include <span>
#include <string>
#include <string_view>
#include <vector>

using namespace ml::kontakt_identity;

static void check_at(bool value, int line)
{
    if (!value)
    {
        std::fprintf(stderr, "KontaktIdentity fixture assertion failed at line %d\n", line);
        std::abort();
    }
}

#define check(value) check_at((value), __LINE__)

struct SparseMemory
{
    std::map<std::uintptr_t, std::byte> bytes;

    bool operator()(std::uintptr_t address, void* output, std::size_t size) const
    {
        auto* destination = static_cast<std::byte*>(output);
        for (std::size_t i = 0; i < size; ++i)
        {
            const auto found = bytes.find(address + i);
            if (found == bytes.end())
                return false;
            destination[i] = found->second;
        }
        return true;
    }

    template <typename T>
    void write(std::uintptr_t address, const T& value)
    {
        static_assert(std::is_trivially_copyable_v<T>);
        const auto* source = reinterpret_cast<const std::byte*>(&value);
        for (std::size_t i = 0; i < sizeof(T); ++i)
            bytes[address + i] = source[i];
    }

    void write_bytes(std::uintptr_t address, std::span<const std::byte> input)
    {
        for (std::size_t i = 0; i < input.size(); ++i)
            bytes[address + i] = input[i];
    }

    void erase(std::uintptr_t address, std::size_t size)
    {
        for (std::size_t i = 0; i < size; ++i)
            bytes.erase(address + i);
    }
};

struct Fixture
{
    static constexpr std::uintptr_t module_base = 0x100000000;
    static constexpr std::uint32_t module_size = 0x0A200000;
    static constexpr std::uintptr_t component = 0x20000000;
    static constexpr std::uintptr_t receiver = 0x20001000;
    static constexpr std::uintptr_t engine = 0x20002000;
    static constexpr std::uintptr_t table = 0x20003000;
    static constexpr std::uintptr_t component_vtable = module_base + kComponentVtableRva;
    static constexpr std::uintptr_t vtable = module_base + kBEngineVtableRva;
    static constexpr std::uintptr_t col = module_base + 0x00600000;
    static constexpr std::uintptr_t class_descriptor = module_base + 0x00601000;

    SparseMemory memory;
    ModulePin pin {module_base, module_size, kSupportedVersion, kSupportedCoreSha256};
    std::map<std::uint32_t, std::vector<std::int16_t>> indices_by_bank;

    Fixture()
    {
        memory.write(component, component_vtable);
        memory.write(component + kComponentStateReceiverOffset, receiver);
        memory.write(receiver + kReceiverEngineOffset, engine);
        memory.write(engine, vtable);
        memory.write(engine + kEngineInstrumentTableOffset, table);
        memory.write(vtable - sizeof(std::uintptr_t), col);

        std::array<std::uint32_t, 6> locator {
            1, 0, 0, kBEngineTypeDescriptorRva,
            static_cast<std::uint32_t>(class_descriptor - module_base),
            static_cast<std::uint32_t>(col - module_base)};
        memory.write_bytes(col, std::as_bytes(std::span(locator)));
        constexpr std::string_view type_name = ".?AVBEngine@@";
        std::array<std::byte, 16 + type_name.size() + 1> type_descriptor{};
        std::memcpy(type_descriptor.data() + 16, type_name.data(), type_name.size());
        memory.write_bytes(module_base + kBEngineTypeDescriptorRva, type_descriptor);

        for (std::uint32_t slot = 0; slot < kBankSlots; ++slot)
        {
            const auto bank = bank_address(slot);
            memory.write(table + kTableFirstBankOffset + slot * sizeof(std::uintptr_t), bank);
            const std::uint32_t inactive = 0;
            memory.write(bank + kInstrumentStatusOffset, inactive);
            const std::uintptr_t empty_record = 0;
            for (std::uint32_t record = 0; record < kRecordsPerBank; ++record)
                memory.write(bank + kBankFirstRecordOffset
                                 + record * sizeof(std::uintptr_t),
                             empty_record);
        }
    }

    static constexpr std::uintptr_t bank_address(std::uint32_t slot)
    {
        return 0x30000000 + static_cast<std::uintptr_t>(slot) * 0x20000;
    }

    void set_bank_indices(std::uint32_t bank_index,
                          const std::vector<std::int16_t>& local_indices)
    {
        const auto bank = bank_address(bank_index);
        const auto vector_address = bank + 0x10000;
        if (local_indices.empty())
        {
            const std::uintptr_t empty = 0;
            memory.write(bank + kBankIndicesVectorOffset, empty);
            memory.write(bank + kBankIndicesVectorOffset + sizeof(std::uintptr_t), empty);
            memory.write(bank + kBankIndicesVectorOffset + 2 * sizeof(std::uintptr_t), empty);
            return;
        }
        const auto end = vector_address + local_indices.size() * sizeof(std::int16_t);
        memory.write(bank + kBankIndicesVectorOffset, vector_address);
        memory.write(bank + kBankIndicesVectorOffset + sizeof(std::uintptr_t),
                     end);
        memory.write(bank + kBankIndicesVectorOffset + 2 * sizeof(std::uintptr_t), end);
        memory.write_bytes(vector_address,
                           std::as_bytes(std::span(local_indices.data(), local_indices.size())));
    }

    void set_allocated_empty_indices(std::uint32_t bank_index)
    {
        const auto bank = bank_address(bank_index);
        const auto vector_address = bank + 0x10000;
        memory.write(bank + kBankIndicesVectorOffset, vector_address);
        memory.write(bank + kBankIndicesVectorOffset + sizeof(std::uintptr_t),
                     vector_address);
        memory.write(bank + kBankIndicesVectorOffset + 2 * sizeof(std::uintptr_t),
                     vector_address);
    }

    void add_short(std::uint32_t index, std::string_view name)
    {
        const auto bank = bank_address(index / kRecordsPerBank);
        const auto record = 0x50000000 + static_cast<std::uintptr_t>(index) * 0x400;
        const auto record_pointer = bank + kBankFirstRecordOffset
            + static_cast<std::uintptr_t>(index % kRecordsPerBank) * sizeof(std::uintptr_t);
        memory.write(record_pointer, record);
        indices_by_bank[index / kRecordsPerBank].push_back(
            static_cast<std::int16_t>(index % kRecordsPerBank));
        set_bank_indices(index / kRecordsPerBank,
                         indices_by_bank[index / kRecordsPerBank]);
        const auto object = record + kInstrumentNameOffset;
        std::array<std::byte, 32> value{};
        std::memcpy(value.data(), name.data(), name.size());
        const auto size = static_cast<std::uint64_t>(name.size());
        constexpr std::uint64_t capacity = 15;
        std::memcpy(value.data() + 16, &size, sizeof(size));
        std::memcpy(value.data() + 24, &capacity, sizeof(capacity));
        memory.write_bytes(object, value);
        const std::uint32_t active = 1;
        memory.write(bank + kInstrumentStatusOffset, active);
    }

    void add_long(std::uint32_t index, std::string_view name)
    {
        const auto bank = bank_address(index / kRecordsPerBank);
        const auto record = 0x50000000 + static_cast<std::uintptr_t>(index) * 0x400;
        const auto record_pointer = bank + kBankFirstRecordOffset
            + static_cast<std::uintptr_t>(index % kRecordsPerBank) * sizeof(std::uintptr_t);
        memory.write(record_pointer, record);
        indices_by_bank[index / kRecordsPerBank].push_back(
            static_cast<std::int16_t>(index % kRecordsPerBank));
        set_bank_indices(index / kRecordsPerBank,
                         indices_by_bank[index / kRecordsPerBank]);
        const auto object = record + kInstrumentNameOffset;
        const auto data = 0x70000000 + static_cast<std::uintptr_t>(index) * 0x1000;
        std::array<std::byte, 32> value{};
        const std::uint64_t size = name.size();
        constexpr std::uint64_t capacity = 31;
        std::memcpy(value.data(), &data, sizeof(data));
        std::memcpy(value.data() + 16, &size, sizeof(size));
        std::memcpy(value.data() + 24, &capacity, sizeof(capacity));
        memory.write_bytes(object, value);
        std::vector<std::byte> bytes(name.size() + 1);
        std::memcpy(bytes.data(), name.data(), name.size());
        memory.write_bytes(data, bytes);
        const std::uint32_t active = 1;
        memory.write(bank + kInstrumentStatusOffset, active);
    }

    Reader<SparseMemory> reader() const { return Reader<SparseMemory>(pin, memory); }
};

int main()
{
    {
        Fixture empty;
        const auto reader = empty.reader();
        const auto count = reader.instrument_count(Fixture::component);
        check(count && count.value == 0);
        const auto indices = reader.instrument_indices(Fixture::component);
        check(indices && indices.value.empty());
        const auto names = reader.names_for_indices(Fixture::component, {}, 0);
        check(names && names.value.count == 0 && names.value.names.empty());
        const auto no_name = reader.instrument_name(Fixture::component, 0);
        check(!no_name && no_name.error == Error::instrument_not_loaded);
        const auto out_of_range = reader.instrument_name(
            Fixture::component, kMaximumInstrumentIndex);
        check(!out_of_range && out_of_range.error == Error::index_out_of_range);
    }

    {
        Fixture two_slots;
        two_slots.add_short(0, "Grand Piano");
        two_slots.add_long(128, "A Long Kontakt Instrument Name");
        const auto reader = two_slots.reader();
        const auto count = reader.instrument_count(Fixture::component);
        check(count && count.value == 2);
        const auto actual_indices = reader.instrument_indices(Fixture::component);
        check(actual_indices && actual_indices.value == std::vector<std::uint32_t>({0, 128}));
        const std::array<std::uint32_t, 2> indices {0, 128};
        const auto names = reader.names_for_indices(Fixture::component, indices, 2);
        check(names && names.value.count == 2 && names.value.names.size() == 2);
        check(names.value.names[0] == "Grand Piano");
        check(names.value.names[1] == "A Long Kontakt Instrument Name");

        const auto mismatch = reader.names_for_indices(
            Fixture::component, indices, 1);
        check(!mismatch && mismatch.error == Error::count_mismatch);
        const std::array<std::uint32_t, 2> duplicate {0, 0};
        const auto duplicate_result = reader.names_for_indices(
            Fixture::component, duplicate, 2);
        check(!duplicate_result && duplicate_result.error == Error::duplicate_index);
    }

    {
        // The official count getter counts active banks, while the index
        // getter can return multiple instruments from one bank.
        Fixture same_bank;
        same_bank.add_short(1, "Grand Piano");
        same_bank.add_short(6, "Clavinet");
        const auto reader = same_bank.reader();
        const auto count = reader.instrument_count(Fixture::component);
        const auto indices = reader.instrument_indices(Fixture::component);
        check(count && count.value == 1);
        check(indices && indices.value == std::vector<std::uint32_t>({1, 6}));
        const auto names = reader.names_for_indices(
            Fixture::component, indices.value, count.value);
        check(names && names.value.count == 1 && names.value.names.size() == 2);
        check(names.value.names[0] == "Grand Piano");
        check(names.value.names[1] == "Clavinet");
    }

    {
        // A bank-local vector may contain an index whose resolved slot is
        // empty. The native helper silently filters that slot.
        Fixture sparse;
        sparse.add_short(1, "Grand Piano");
        sparse.set_bank_indices(0, {1, 6});
        const auto indices = sparse.reader().instrument_indices(Fixture::component);
        check(indices && indices.value == std::vector<std::uint32_t>({1}));
    }

    {
        // Kontakt may represent an empty vector either as null/null or as an
        // allocated begin==end pair. Both are valid and return no indices.
        Fixture allocated_empty;
        allocated_empty.add_short(0, "Grand Piano");
        allocated_empty.set_allocated_empty_indices(0);
        const auto indices = allocated_empty.reader().instrument_indices(
            Fixture::component);
        check(indices && indices.value.empty());
    }

    {
        // The native helper rebases signed local entries, then resolves the
        // resulting global bank/slot. A bank-1 -1 entry resolves slot 127.
        Fixture cross_bank;
        cross_bank.add_short(127, "Grand Piano");
        cross_bank.set_bank_indices(0, {});
        cross_bank.memory.write(Fixture::bank_address(0) + kInstrumentStatusOffset,
                                std::uint32_t{0});
        cross_bank.set_bank_indices(1, {-1});
        cross_bank.memory.write(Fixture::bank_address(1) + kInstrumentStatusOffset,
                                std::uint32_t{1});
        const auto reader = cross_bank.reader();
        const auto count = reader.instrument_count(Fixture::component);
        const auto indices = reader.instrument_indices(Fixture::component);
        check(count && count.value == 1);
        check(indices && indices.value == std::vector<std::uint32_t>({127}));
        const auto names = reader.names_for_indices(
            Fixture::component, indices.value, count.value);
        check(names && names.value.names == std::vector<std::string>({"Grand Piano"}));
    }

    {
        // Values below zero and above the last global record are rejected by
        // the native unsigned 0x1fff bound check.
        Fixture bounds;
        bounds.add_short(0, "Grand Piano");
        bounds.add_short(kMaximumInstrumentIndex - 1, "Clavinet");
        bounds.set_bank_indices(0, {-1, 0});
        bounds.set_bank_indices(kBankSlots - 1, {127, 128});
        const auto indices = bounds.reader().instrument_indices(Fixture::component);
        check(indices && indices.value == std::vector<std::uint32_t>({0, 8191}));
    }

    {
        Fixture odd_vector;
        odd_vector.add_short(0, "Grand Piano");
        const auto begin = Fixture::bank_address(0) + 0x10000;
        odd_vector.memory.write(Fixture::bank_address(0) + kBankIndicesVectorOffset
                                    + sizeof(std::uintptr_t),
                                begin + 1);
        const auto indices = odd_vector.reader().instrument_indices(Fixture::component);
        check(!indices && indices.error == Error::invalid_layout);
    }

    {
        Fixture capacity_before_end;
        capacity_before_end.add_short(0, "Grand Piano");
        const auto bank = Fixture::bank_address(0);
        const auto begin = bank + 0x10000;
        capacity_before_end.memory.write(bank + kBankIndicesVectorOffset
                                             + 2 * sizeof(std::uintptr_t),
                                         begin);
        const auto indices = capacity_before_end.reader().instrument_indices(
            Fixture::component);
        check(!indices && indices.error == Error::invalid_layout);
    }

    {
        Fixture oversized_vector;
        oversized_vector.add_short(0, "Grand Piano");
        oversized_vector.set_bank_indices(
            0, std::vector<std::int16_t>(kMaximumIndexEntriesPerBank + 1, 0));
        const auto indices = oversized_vector.reader().instrument_indices(Fixture::component);
        check(!indices && indices.error == Error::invalid_layout);
    }

    {
        Fixture invalid_vector_pointer;
        invalid_vector_pointer.add_short(0, "Grand Piano");
        invalid_vector_pointer.memory.write(
            Fixture::bank_address(0) + kBankIndicesVectorOffset,
            std::uintptr_t{0x77770000});
        invalid_vector_pointer.memory.write(
            Fixture::bank_address(0) + kBankIndicesVectorOffset
                + sizeof(std::uintptr_t),
            std::uintptr_t{0x77770002});
        invalid_vector_pointer.memory.write(
            Fixture::bank_address(0) + kBankIndicesVectorOffset
                + 2 * sizeof(std::uintptr_t),
            std::uintptr_t{0x77770002});
        const auto indices = invalid_vector_pointer.reader().instrument_indices(
            Fixture::component);
        check(!indices && indices.error == Error::unreadable_memory);
    }

    {
        Fixture bad_pointer;
        bad_pointer.add_short(0, "Grand Piano");
        const auto bank = Fixture::bank_address(0);
        const auto record_pointer = bank + kBankFirstRecordOffset;
        const std::uintptr_t invalid_record = 0x77770000;
        bad_pointer.memory.write(record_pointer, invalid_record);
        const auto name = bad_pointer.reader().instrument_name(Fixture::component, 0);
        check(!name && name.error == Error::unreadable_memory);
    }

    {
        Fixture bad_rtti;
        constexpr std::string_view fake_name = ".?AVOtherType@@";
        std::array<std::byte, fake_name.size() + 1> fake{};
        std::memcpy(fake.data(), fake_name.data(), fake_name.size());
        bad_rtti.memory.write_bytes(
            Fixture::module_base + kBEngineTypeDescriptorRva + 16, fake);
        const auto count = bad_rtti.reader().instrument_count(Fixture::component);
        check(!count && count.error == Error::invalid_rtti);
    }

    {
        Fixture truncated;
        truncated.add_long(0, "A Long Kontakt Instrument Name");
        const auto data = 0x70000000;
        truncated.memory.erase(data + 16, 16);
        const auto name = truncated.reader().instrument_name(Fixture::component, 0);
        check(!name && name.error == Error::unreadable_memory);
    }

    {
        Fixture invalid_utf8;
        const std::string malformed("\xC0\xAF", 2);
        invalid_utf8.add_short(0, malformed);
        const auto name = invalid_utf8.reader().instrument_name(Fixture::component, 0);
        check(!name && name.error == Error::invalid_utf8);
    }

    {
        Fixture wrong_module;
        auto pin = wrong_module.pin;
        pin.version = "8.10.0";
        const auto count = Reader<SparseMemory>(pin, wrong_module.memory)
            .instrument_count(Fixture::component);
        check(!count && count.error == Error::unsupported_module);
    }

    {
        Fixture wrong_hash;
        auto pin = wrong_hash.pin;
        pin.binary_sha256 = "0000000000000000000000000000000000000000000000000000000000000000";
        const auto count = Reader<SparseMemory>(pin, wrong_hash.memory)
            .instrument_count(Fixture::component);
        check(!count && count.error == Error::unsupported_module);
    }

    return 0;
}
