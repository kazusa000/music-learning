#include "../ample-component-layout-rea-02/AmpleComponentLayoutReader.h"

#include <cstdint>
#include <cstring>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
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

    bool read(std::uintptr_t address, void* output, std::size_t bytes) const
    {
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

    std::map<std::uintptr_t, std::vector<std::uint8_t>> regions;
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

    ReadinessResult inspect(bool secondary)
    {
        if (secondary)
            runtime.component_view = wrapper + 0x18;
        return inspect_readiness(runtime, kAgLp40InstalledLayout,
            [this](std::uintptr_t address, void* output, std::size_t bytes) {
                return memory.read(address, output, bytes);
            });
    }
};

std::string pointer(std::uintptr_t value)
{
    std::ostringstream output;
    output << "0x" << std::hex << value;
    return output.str();
}

bool emit_case(const char* name, bool secondary, bool& first)
{
    Fixture fixture;
    const auto result = fixture.inspect(secondary);
    if (!result.component.valid || !result.readiness_reader_invoked
        || !result.readiness.ready)
        return false;
    std::uintptr_t raw_vtable = 0;
    if (!fixture.memory.read(fixture.runtime.component_view, &raw_vtable,
                             sizeof(raw_vtable)))
        return false;
    if (!first)
        std::cout << ',';
    first = false;
    std::cout << "{\"case\":\"" << name
              << "\",\"ready\":true,\"component_vtable\":\""
              << pointer(raw_vtable)
              << "\",\"normalized_component_vtable\":\""
              << pointer(result.readiness.component_vtable)
              << "\",\"normalized_component_wrapper\":\""
              << pointer(result.component.wrapper)
              << "\",\"component_normalized_offset\":"
              << result.component.normalized_offset
              << ",\"component_pointer_reads\":"
              << result.component.pointer_reads
              << ",\"component_bytes_read\":"
              << result.component.bytes_read << '}';
    return true;
}
}

int main()
{
    std::cout << "{\"schema_version\":1,\"identity\":{"
                 "\"product\":\"Ample Guitar LP\",\"version\":\"4.0.0\","
                 "\"module_sha256\":\"4cd985483c2a7d8da15fb7959f8afb3e2d3c917635718262f1a2f6eab93e850e\","
                 "\"image_size\":88674304},\"cases\":[";
    bool first = true;
    if (!emit_case("primary", false, first)
        || !emit_case("secondary", true, first))
    {
        std::cerr << "Pinned dual-layout reader failed its wire fixture\n";
        return 1;
    }
    std::cout << "]}\n";
    return 0;
}
