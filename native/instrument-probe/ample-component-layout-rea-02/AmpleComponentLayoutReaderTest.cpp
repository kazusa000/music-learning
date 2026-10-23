#include "AmpleComponentLayoutReader.h"

#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>
#include <string>
#include <unordered_map>
#include <vector>

namespace
{
using namespace ml::ample_layout_diagnostic;

struct FakeMemory
{
    template <typename Value>
    void put(std::uintptr_t address, const Value& value)
    {
        std::vector<std::uint8_t> bytes(sizeof(Value));
        std::memcpy(bytes.data(), &value, sizeof(Value));
        regions[address] = std::move(bytes);
    }

    void put_string(std::uintptr_t address, const std::string& value)
    {
        std::vector<std::uint8_t> bytes(value.begin(), value.end());
        bytes.push_back(0);
        regions[address] = std::move(bytes);
    }

    bool read(std::uintptr_t address, void* output, std::size_t bytes)
    {
        reads.push_back(address);
        for (const auto& [start, data] : regions)
        {
            if (address >= start && address - start <= data.size()
                && bytes <= data.size() - (address - start))
            {
                std::memcpy(output, data.data() + (address - start), bytes);
                return true;
            }
        }
        return false;
    }

    std::unordered_map<std::uintptr_t, std::vector<std::uint8_t>> regions;
    std::vector<std::uintptr_t> reads;
};

struct Fixture
{
    static constexpr std::uintptr_t module = 0x7ffacad60000ULL;
    static constexpr std::uintptr_t wrapper = 0x0000012345600000ULL;
    static constexpr std::uintptr_t primary = 0x0000012345700000ULL;
    static constexpr std::uintptr_t processor = primary + 0x210;
    static constexpr std::uintptr_t status_chars = 0x0000012345800000ULL;

    RuntimeIdentity runtime{
        "Ample Guitar LP", "4.0.0",
        "4cd985483c2a7d8da15fb7959f8afb3e2d3c917635718262f1a2f6eab93e850e",
        module, 0x05491000, wrapper};
    FakeMemory memory;

    Fixture()
    {
        memory.put(wrapper, module + 0x051f7a00);
        memory.put(wrapper + 0x18, module + 0x051f71c0);
        memory.put(wrapper + 0x38, processor);
        memory.put(processor, module + 0x051f8a90);
        memory.put(primary, module + 0x051f90d0);
        const std::uint8_t loading = 0;
        const std::int32_t errors = 0;
        memory.put(primary + 0x250, loading);
        memory.put(primary + 0x46fb8, errors);
        memory.put(primary + 0x3c0, status_chars);
        memory.put_string(status_chars, "Success");
    }

    ReadinessResult inspect()
    {
        return inspect_readiness(runtime, kAgLp40InstalledLayout,
            [this](std::uintptr_t address, void* output, std::size_t bytes) {
                return memory.read(address, output, bytes);
            });
    }
};

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
    using namespace ml::ample_layout_diagnostic;

    {
        Fixture fixture;
        const auto result = fixture.inspect();
        check(result.component.valid
                  && result.component.wrapper == Fixture::wrapper,
              "primary component view normalizes at offset zero");
        check(result.component.pointer_reads == 1
                  && result.component.bytes_read == 8,
              "base-view normalization reads one bounded pointer");
        check(result.readiness_reader_invoked && result.readiness.ready,
              "normalized wrapper enters the original strict readiness reader");
        check(result.readiness.primary_processor == Fixture::primary
                  && result.readiness.status == "Success",
              "existing reader validates the +0x38/+0x210 processor path");
    }
    {
        Fixture fixture;
        fixture.runtime.component_view = Fixture::wrapper + 0x18;
        const auto result = fixture.inspect();
        check(result.component.valid
                  && result.component.wrapper == Fixture::wrapper
                  && result.component.normalized_offset == 0x18,
              "secondary +0x18 view normalizes only after base-vtable check");
        check(result.component.pointer_reads == 2
                  && result.component.bytes_read == 16
                  && result.readiness_reader_invoked && result.readiness.ready,
              "secondary normalization stays bounded and preserves readiness checks");
    }
    {
        Fixture fixture;
        fixture.runtime.product = "Other product";
        check(fixture.inspect().component.error == Error::unsupported_product
                  && fixture.memory.reads.empty(),
              "product mismatch is rejected before memory reads");
    }
    {
        Fixture fixture;
        fixture.runtime.version = "4.0.1";
        check(fixture.inspect().component.error == Error::unsupported_version
                  && fixture.memory.reads.empty(),
              "version mismatch is rejected before memory reads");
    }
    {
        Fixture fixture;
        fixture.runtime.module_sha256 = "different";
        check(fixture.inspect().component.error == Error::unsupported_module
                  && fixture.memory.reads.empty(),
              "module SHA mismatch is rejected before memory reads");
    }
    {
        Fixture fixture;
        fixture.runtime.image_size -= 1;
        check(fixture.inspect().component.error == Error::invalid_module
                  && fixture.memory.reads.empty(),
              "wrong image size cannot use the pinned layout");
    }
    {
        Fixture fixture;
        fixture.memory.put(Fixture::wrapper, Fixture::module + 0x051f8770);
        const auto result = fixture.inspect();
        check(result.component.error == Error::unrecognized_component_vtable
                  && !result.readiness_reader_invoked
                  && fixture.memory.reads.size() == 1,
              "unknown component table fails before wrapper fields are read");
    }
    {
        Fixture fixture;
        fixture.runtime.component_view = Fixture::wrapper + 0x18;
        fixture.memory.put(Fixture::wrapper + 0x18,
                           Fixture::module + 0x051f8770);
        check(fixture.inspect().component.error
                  == Error::unrecognized_component_vtable,
              "unlisted secondary table is not normalized by guesswork");
    }
    {
        Fixture fixture;
        fixture.runtime.component_view = Fixture::wrapper + 0x18;
        fixture.memory.put(Fixture::wrapper, Fixture::module + 0x051f7690);
        const auto result = fixture.inspect();
        check(result.component.error == Error::wrapper_vtable_mismatch
                  && !result.readiness_reader_invoked,
              "listed secondary table still requires its exact wrapper base table");
    }
    {
        Fixture fixture;
        fixture.runtime.component_view = 0x10;
        fixture.memory.put(0x10, Fixture::module + 0x051f71c0);
        const auto result = fixture.inspect();
        check(result.component.error == Error::wrapper_underflow
                  && fixture.memory.reads.size() == 1,
              "secondary pointer subtraction rejects underflow without a second read");
    }
    {
        Fixture fixture;
        fixture.runtime.module_base =
            std::numeric_limits<std::uintptr_t>::max() - 8;
        check(fixture.inspect().component.error == Error::invalid_module
                  && fixture.memory.reads.empty(),
              "module-base overflow is rejected before memory access");
    }
    {
        Fixture fixture;
        fixture.memory.put(Fixture::processor, Fixture::module + 0x051f8770);
        const auto result = fixture.inspect();
        check(result.readiness_reader_invoked
                  && result.readiness.error
                         == ml::ample_readiness::Error::processor_subobject_vtable_mismatch
                  && !result.readiness.ready,
              "original readiness reader rejects an unverified processor subobject vtable");
    }
    {
        Fixture fixture;
        fixture.memory.put(Fixture::primary, Fixture::module + 0x051f9140);
        const auto result = fixture.inspect();
        check(result.readiness.error
                  == ml::ample_readiness::Error::invalid_primary_object
                  && !result.readiness.ready,
              "original readiness reader requires the exact primary vtable");
    }
    {
        Fixture fixture;
        const std::int32_t errors = -1;
        fixture.memory.put(Fixture::primary + 0x46fb8, errors);
        const auto result = fixture.inspect();
        check(result.readiness.error
                  == ml::ample_readiness::Error::invalid_error_count
                  && !result.readiness.ready,
              "valid layout cannot convert invalid loader state into readiness");
    }

    std::cout << "Ample component-layout bridge fixtures passed\n";
    return 0;
}
