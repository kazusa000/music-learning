#include "../instrument-probe/AmpleReadinessReader.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <type_traits>

using namespace ml::ample_readiness;

static void check_at(bool value, int line)
{
    if (!value)
    {
        std::fprintf(stderr, "AmpleReadinessReader fixture failed at line %d\n", line);
        std::abort();
    }
}
#define check(value) check_at((value), __LINE__)

struct SparseMemory
{
    std::map<std::uintptr_t, std::byte> bytes;
    std::size_t reads = 0;

    bool operator()(std::uintptr_t address, void* output, std::size_t size)
    {
        ++reads;
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

    void write_text(std::uintptr_t address, const char* value)
    {
        for (std::size_t i = 0; i <= std::strlen(value); ++i)
            bytes[address + i] = static_cast<std::byte>(value[i]);
    }
};

struct Fixture
{
    static constexpr std::uintptr_t module_base = 0x100000000;
    static constexpr std::uint32_t image_size = 0x06000000;
    static constexpr std::uintptr_t component = 0x20000000;
    static constexpr std::uintptr_t processor_subobject = component + 0x5000;
    static constexpr std::uintptr_t primary = processor_subobject - 0x210;
    static constexpr std::uintptr_t status_chars = 0x30000000;
    static constexpr std::uint32_t component_rva = 0x051f71c0;
    static constexpr std::uint32_t processor_subobject_rva = 0x051f8a90;
    static constexpr std::uint32_t primary_rva = 0x051f90d0;

    SparseMemory memory;
    LayoutPin pin{
        "Ample Guitar LP", "test-version", "test-sha", component_rva,
        0x38, 0x210, processor_subobject_rva, primary_rva, 0x30, 0x40, 0x50};
    RuntimeIdentity runtime{
        "Ample Guitar LP", "test-version", "test-sha", module_base,
        image_size, component};

    Fixture()
    {
        memory.write(component, module_base + component_rva);
        memory.write(component + pin.component_processor_pointer_offset,
                     processor_subobject);
        memory.write(processor_subobject, module_base + processor_subobject_rva);
        memory.write(primary, module_base + primary_rva);
        memory.write(primary + pin.loading_byte_offset, std::uint8_t{0});
        memory.write(primary + pin.loader_error_count_offset, std::int32_t{0});
        memory.write(primary + pin.status_string_offset, status_chars);
        memory.write_text(status_chars, "Success");
    }

    Snapshot inspect()
    {
        return ml::ample_readiness::inspect(runtime, pin, memory);
    }
};

int main()
{
    {
        Fixture fixture;
        fixture.pin.processor_subobject_vtable_rva = 0;
        const auto result = fixture.inspect();
        check(result.error == Error::unsupported_component_mapping);
        check(result.component_vtable == Fixture::module_base + Fixture::component_rva);
        check(result.primary_processor == 0 && !result.fields_read && !result.ready);
        check(fixture.memory.reads == 1);
    }
    {
        Fixture fixture;
        fixture.runtime.product = "Ample Guitar M";
        const auto result = fixture.inspect();
        check(result.error == Error::unsupported_product && fixture.memory.reads == 0);
    }
    {
        Fixture fixture;
        fixture.runtime.version = "4.0.1";
        const auto result = fixture.inspect();
        check(result.error == Error::unsupported_version && fixture.memory.reads == 0);
    }
    {
        Fixture fixture;
        fixture.runtime.module_sha256 = "other-sha";
        const auto result = fixture.inspect();
        check(result.error == Error::unsupported_module && fixture.memory.reads == 0);
    }
    {
        Fixture fixture;
        fixture.memory.write(Fixture::component, Fixture::module_base + 0x2100);
        const auto result = fixture.inspect();
        check(result.error == Error::component_vtable_mismatch);
        check(fixture.memory.reads == 1);
    }
    {
        Fixture fixture;
        auto result = fixture.inspect();
        check(result.error == Error::ok && result.fields_read && result.ready);
        check(result.loading_byte == 0 && result.loader_error_count == 0);
        check(result.status == "Success");
        check(result.processor_subobject == Fixture::processor_subobject);
        check(result.processor_subobject_vtable
              == Fixture::module_base + Fixture::processor_subobject_rva);
        check(result.primary_processor == Fixture::primary);
    }
    {
        Fixture fixture;
        fixture.memory.write(Fixture::component + fixture.pin.component_processor_pointer_offset,
                             std::uintptr_t{0x31000000});
        const auto result = fixture.inspect();
        check(result.error == Error::invalid_processor_subobject);
        check(!result.fields_read && !result.ready);
    }
    {
        Fixture fixture;
        fixture.memory.write(Fixture::processor_subobject,
                             Fixture::module_base + 0x051f8a91);
        const auto result = fixture.inspect();
        check(result.error == Error::processor_subobject_vtable_mismatch);
        check(!result.fields_read && !result.ready);
    }
    {
        Fixture fixture;
        fixture.memory.write(Fixture::primary + fixture.pin.loading_byte_offset,
                             std::uint8_t{1});
        const auto result = fixture.inspect();
        check(result.fields_read && !result.ready && result.loading_byte == 1);
    }
    {
        Fixture fixture;
        fixture.memory.write(Fixture::primary + fixture.pin.loader_error_count_offset,
                             std::int32_t{1});
        const auto result = fixture.inspect();
        check(result.fields_read && !result.ready && result.loader_error_count == 1);
    }
    {
        Fixture fixture;
        fixture.memory.write_text(Fixture::status_chars, "Loading");
        const auto result = fixture.inspect();
        check(result.fields_read && !result.ready && result.status == "Loading");
    }
    {
        Fixture fixture;
        fixture.memory.write(Fixture::primary, Fixture::module_base + 0x3010);
        const auto result = fixture.inspect();
        check(result.error == Error::invalid_primary_object);
        check(!result.fields_read && !result.ready);
    }
    {
        Fixture fixture;
        fixture.memory.write(Fixture::primary + fixture.pin.loading_byte_offset,
                             std::uint8_t{2});
        const auto result = fixture.inspect();
        check(result.error == Error::invalid_loading_byte && !result.fields_read);
    }
    {
        Fixture fixture;
        fixture.memory.write(Fixture::primary + fixture.pin.loader_error_count_offset,
                             std::int32_t{-1});
        const auto result = fixture.inspect();
        check(result.error == Error::invalid_error_count && !result.fields_read);
        check(result.loading_byte_read && result.loader_error_count_read);
        check(result.loader_error_count == -1 && !result.status_read);
    }
    {
        Fixture fixture;
        fixture.memory.write(Fixture::primary + fixture.pin.status_string_offset,
                             std::uintptr_t{0x31000000});
        const auto result = fixture.inspect();
        check(result.error == Error::invalid_status_string && !result.fields_read);
    }
    std::puts("AmpleReadinessReader fixtures passed");
}
