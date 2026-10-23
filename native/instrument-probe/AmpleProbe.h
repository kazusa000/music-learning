#pragma once

#include "Host.h"
#include "AmpleReadinessReader.h"

#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <algorithm>
#include <array>
#include <cstddef>
#include <map>
#include <memory>
#include <string>

namespace ml
{
namespace ample_probe_detail
{
struct ModulePin
{
    juce::int64 size = 0;
    juce::int64 modified = 0;
    std::string digest;
};

class CheckedProcessReader
{
public:
    static constexpr std::size_t kMaximumBytes = 64 * 1024;

    bool operator()(std::uintptr_t address, void* destination,
                    std::size_t bytes)
    {
        if (address < 0x10000 || destination == nullptr || bytes == 0
            || bytes > kMaximumBytes - bytesRead
            || bytes - 1 > UINTPTR_MAX - address)
            return false;
        auto* output = static_cast<std::byte*>(destination);
        std::size_t copied = 0;
        while (copied < bytes)
        {
            const auto current = address + copied;
            MEMORY_BASIC_INFORMATION region{};
            if (VirtualQuery(reinterpret_cast<const void*>(current), &region,
                             sizeof(region)) != sizeof(region)
                || region.State != MEM_COMMIT
                || (region.Protect & (PAGE_NOACCESS | PAGE_GUARD)) != 0)
                return false;
            const auto base = reinterpret_cast<std::uintptr_t>(region.BaseAddress);
            if (current < base || region.RegionSize > UINTPTR_MAX - base)
                return false;
            const auto end = base + region.RegionSize;
            if (current >= end)
                return false;
            const auto chunk = std::min(bytes - copied,
                static_cast<std::size_t>(end - current));
            SIZE_T actual = 0;
            if (!ReadProcessMemory(GetCurrentProcess(),
                    reinterpret_cast<const void*>(current), output + copied,
                    chunk, &actual)
                || actual != chunk)
                return false;
            copied += chunk;
        }
        bytesRead += bytes;
        return true;
    }

    std::size_t bytesRead = 0;
};

inline std::string hashModule(HMODULE module, juce::String& path)
{
    std::array<wchar_t, 32768> buffer{};
    const auto length = GetModuleFileNameW(module, buffer.data(),
                                            static_cast<DWORD>(buffer.size()));
    require(length > 0 && length < buffer.size(),
            "Cannot resolve actual Ample module path");
    path = juce::String(buffer.data());
    const juce::File binary(path);
    require(binary.existsAsFile(), "Actual Ample module is not a file");
    const auto size = binary.getSize();
    const auto modified = binary.getLastModificationTime().toMilliseconds();
    static std::map<HMODULE, ModulePin> modulePins;
    auto cached = modulePins.find(module);
    if (cached == modulePins.end())
    {
        juce::FileInputStream input(binary);
        require(input.openedOk(), "Cannot hash actual Ample module");
        juce::BufferedInputStream buffered(input, 64 * 1024);
        const auto digest = juce::SHA256(buffered).toHexString().toStdString();
        require(binary.getSize() == size
                    && binary.getLastModificationTime().toMilliseconds() == modified,
                "Ample module changed while hashing");
        cached = modulePins.emplace(module,
            ModulePin{size, modified, digest}).first;
    }
    require(cached->second.size == size && cached->second.modified == modified,
            "Ample module changed during the probe");
    return cached->second.digest;
}

inline bool readImageSize(CheckedProcessReader& read, std::uintptr_t base,
                          std::uint32_t& imageSize)
{
    IMAGE_DOS_HEADER dos{};
    if (!read(base, &dos, sizeof(dos)) || dos.e_magic != IMAGE_DOS_SIGNATURE
        || dos.e_lfanew < static_cast<LONG>(sizeof(dos))
        || dos.e_lfanew > 1024 * 1024)
        return false;
    if (static_cast<std::uintptr_t>(dos.e_lfanew) > UINTPTR_MAX - base)
        return false;
    IMAGE_NT_HEADERS64 pe{};
    const auto address = base + static_cast<std::uintptr_t>(dos.e_lfanew);
    if (!read(address, &pe, sizeof(pe)) || pe.Signature != IMAGE_NT_SIGNATURE
        || pe.OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC
        || pe.OptionalHeader.SizeOfImage == 0)
        return false;
    imageSize = pe.OptionalHeader.SizeOfImage;
    return true;
}

inline juce::var serialize(const ample_readiness::Snapshot& snapshot,
                           const CheckedProcessReader& reader)
{
    using ample_readiness::error_name;
    const auto status = snapshot.status.empty()
        ? juce::var() : juce::var(juce::String(snapshot.status));
    return object({
        {"status", juce::String(error_name(snapshot.error).data())},
        {"component_vtable", juce::String::toHexString(
            static_cast<juce::int64>(snapshot.component_vtable))},
        {"processor_subobject", snapshot.processor_subobject == 0
            ? juce::var() : juce::var(juce::String::toHexString(
                static_cast<juce::int64>(snapshot.processor_subobject)))},
        {"processor_subobject_vtable", snapshot.processor_subobject_vtable == 0
            ? juce::var() : juce::var(juce::String::toHexString(
                static_cast<juce::int64>(snapshot.processor_subobject_vtable)))},
        {"primary_processor", snapshot.primary_processor == 0
            ? juce::var() : juce::var(juce::String::toHexString(
                static_cast<juce::int64>(snapshot.primary_processor)))},
        {"loading_byte", snapshot.loading_byte_read
            ? juce::var(static_cast<int>(snapshot.loading_byte)) : juce::var()},
        {"loading_byte_read", snapshot.loading_byte_read},
        {"status_string", status},
        {"status_read", snapshot.status_read},
        {"loader_error_count", snapshot.loader_error_count_read
            ? juce::var(static_cast<int>(snapshot.loader_error_count)) : juce::var()},
        {"loader_error_count_read", snapshot.loader_error_count_read},
        {"fields_read", snapshot.fields_read}, {"ready", snapshot.ready},
        {"process_memory_bytes_read", static_cast<int>(reader.bytesRead)},
        {"memory_budget_bytes", static_cast<int>(CheckedProcessReader::kMaximumBytes)},
        {"scope", "read-only loading diagnostic; not identity/audio acceptance"},
    });
}
} // namespace ample_probe_detail

inline juce::var ampleReadiness(juce::AudioPluginInstance& plugin,
                                const char* phase)
{
    using namespace ample_readiness;
    auto result = std::make_unique<juce::DynamicObject>();
    result->setProperty("schema_version", 1);
    result->setProperty("phase", phase);
    result->setProperty("method", "owned-component-read-only-layout-reader");
    result->setProperty("fields_read", false);
    result->setProperty("ready", false);

    const auto description = plugin.getPluginDescription();
    result->setProperty("product", description.name);
    result->setProperty("manufacturer", description.manufacturerName);
    result->setProperty("version", description.version);
    if (description.name != "Ample Guitar LP"
        || description.manufacturerName != "Ample Sound")
    {
        result->setProperty("status", "unsupported_product");
        return juce::var(result.release());
    }
    if (description.version != "4.0.0")
    {
        result->setProperty("status", "unsupported_version");
        return juce::var(result.release());
    }

    const auto* client = plugin.getVST3Client();
    require(client != nullptr, "Ample readiness probe requires VST3Client");
    auto* component = client->getIComponentPtr();
    require(component != nullptr, "Ample readiness probe has no IComponent");
    const auto componentAddress = reinterpret_cast<std::uintptr_t>(component);
    ample_probe_detail::CheckedProcessReader checkedRead;
    std::uintptr_t componentVtable = 0;
    if (!checkedRead(componentAddress, &componentVtable, sizeof(componentVtable)))
    {
        result->setProperty("status", "invalid_component");
        result->setProperty("component_address", juce::String::toHexString(
            static_cast<juce::int64>(componentAddress)));
        result->setProperty("process_memory_bytes_read",
                            static_cast<int>(checkedRead.bytesRead));
        return juce::var(result.release());
    }

    HMODULE module = nullptr;
    require(GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS
            | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
            reinterpret_cast<LPCWSTR>(componentVtable), &module) != 0,
            "Cannot resolve actual Ample component module");
    juce::String modulePath;
    const auto digest = ample_probe_detail::hashModule(module, modulePath);
    const auto moduleBase = reinterpret_cast<std::uintptr_t>(module);
    std::uint32_t imageSize = 0;
    if (!ample_probe_detail::readImageSize(checkedRead, moduleBase, imageSize))
    {
        result->setProperty("status", "invalid_module");
        result->setProperty("module_path", modulePath);
        result->setProperty("module_sha256", juce::String(digest));
        result->setProperty("process_memory_bytes_read",
                            static_cast<int>(checkedRead.bytesRead));
        return juce::var(result.release());
    }

    result->setProperty("component_address", juce::String::toHexString(
        static_cast<juce::int64>(componentAddress)));
    result->setProperty("component_vtable", juce::String::toHexString(
        static_cast<juce::int64>(componentVtable)));
    result->setProperty("module_path", modulePath);
    result->setProperty("module_sha256", juce::String(digest));
    result->setProperty("module_base", juce::String::toHexString(
        static_cast<juce::int64>(moduleBase)));
    result->setProperty("image_size", static_cast<int>(imageSize));

    const auto version = description.version.toStdString();
    RuntimeIdentity runtime{
        "Ample Guitar LP", version, digest,
        moduleBase, imageSize, componentAddress};
    const auto snapshot = inspect(runtime, kAGLP40, checkedRead);
    result->setProperty("readiness", ample_probe_detail::serialize(snapshot, checkedRead));
    result->setProperty("status", juce::String(error_name(snapshot.error).data()));
    result->setProperty("fields_read", snapshot.fields_read);
    result->setProperty("ready", snapshot.ready);
    return juce::var(result.release());
}
} // namespace ml
