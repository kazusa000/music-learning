#pragma once

#include "KontaktIdentity.h"
#include <windows.h>
#include <array>
#include <map>
#include <string>

namespace ml
{
// Used only by the separately built intake probe. Never patches a plugin or
// replaces a production host. Ownership starts at JUCE's public IComponent.
inline juce::var kontaktIdentity(juce::AudioPluginInstance& plugin)
{
    const auto description = plugin.getPluginDescription();
    if (description.name != "Kontakt 8")
        return {};
    require(description.version == kontakt_identity::kSupportedVersion.data(),
            "Unsupported Kontakt identity layout version");
    auto* client = plugin.getVST3Client();
    require(client != nullptr, "Kontakt identity requires a VST3 component");
    const auto component = reinterpret_cast<std::uintptr_t>(client->getIComponentPtr());
    std::size_t bytesRead = 0;
    auto read = [&](std::uintptr_t address, void* destination, std::size_t bytes) -> bool {
        constexpr std::size_t budget = 64 * 1024;
        if (bytes == 0 || bytes > budget || bytesRead > budget - bytes)
            return false;
        MEMORY_BASIC_INFORMATION region{};
        if (!VirtualQuery(reinterpret_cast<void*>(address), &region, sizeof(region))
            || region.State != MEM_COMMIT || (region.Protect & (PAGE_NOACCESS | PAGE_GUARD)))
            return false;
        const auto end = reinterpret_cast<std::uintptr_t>(region.BaseAddress) + region.RegionSize;
        if (address >= end || bytes > end - address)
            return false;
        SIZE_T actual = 0;
        if (!ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(address), destination, bytes, &actual)
            || actual != bytes)
            return false;
        bytesRead += bytes;
        return true;
    };
    std::uintptr_t vtable = 0;
    require(read(component, &vtable, sizeof(vtable)), "Cannot read owned Kontakt component");
    HMODULE module = nullptr;
    require(GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                              reinterpret_cast<LPCWSTR>(vtable), &module) != 0,
            "Cannot locate actual Kontakt component module");
    std::array<wchar_t, 32768> path{};
    const auto length = GetModuleFileNameW(module, path.data(), static_cast<DWORD>(path.size()));
    require(length > 0 && length < path.size(), "Cannot resolve actual Kontakt module path");
    const juce::File binary{juce::String(path.data())};
    // Hash the actual loaded core, not the portable wrapper which loaded it.
    struct Pin { juce::int64 size; juce::int64 modified; std::string digest; };
    // One fresh host owns one synchronous probe. Repeated before/after
    // snapshots reuse this process-local pin only while file metadata agrees.
    static std::map<HMODULE, Pin> modulePins;
    const auto size = binary.getSize();
    const auto modified = binary.getLastModificationTime().toMilliseconds();
    auto cached = modulePins.find(module);
    if (cached == modulePins.end())
    {
        juce::FileInputStream input(binary);
        require(input.openedOk(), "Cannot hash actual Kontakt core");
        juce::BufferedInputStream buffered(input, 64 * 1024);
        const auto hash = juce::SHA256(buffered).toHexString().toStdString();
        require(binary.getSize() == size && binary.getLastModificationTime().toMilliseconds() == modified,
                "Kontakt core changed while hashing");
        cached = modulePins.emplace(module, Pin{size, modified, hash}).first;
    }
    require(cached->second.size == size && cached->second.modified == modified,
            "Kontakt core changed during probe");
    const auto& digest = cached->second.digest;
    const auto base = reinterpret_cast<std::uintptr_t>(module);
    IMAGE_DOS_HEADER dos{};
    require(read(base, &dos, sizeof(dos)) && dos.e_magic == IMAGE_DOS_SIGNATURE
                && dos.e_lfanew >= sizeof(dos) && dos.e_lfanew <= 1024 * 1024,
            "Invalid Kontakt core PE header");
    IMAGE_NT_HEADERS64 pe{};
    require(read(base + dos.e_lfanew, &pe, sizeof(pe)) && pe.Signature == IMAGE_NT_SIGNATURE
                && pe.OptionalHeader.Magic == IMAGE_NT_OPTIONAL_HDR64_MAGIC,
            "Invalid Kontakt core image");
    const auto version = description.version.toStdString();
    kontakt_identity::Reader reader(
        kontakt_identity::ModulePin{base, pe.OptionalHeader.SizeOfImage, version, digest}, read);
    const auto count = reader.instrument_count(component);
    require(static_cast<bool>(count), "Kontakt native instrument count failed: " + juce::String(static_cast<int>(count.error)));
    const auto indices = reader.instrument_indices(component);
    require(static_cast<bool>(indices), "Kontakt native instrument indices failed: " + juce::String(static_cast<int>(indices.error)));
    const auto names = reader.names_for_indices(component, indices.value, count.value);
    require(static_cast<bool>(names), "Kontakt native instrument names failed: " + juce::String(static_cast<int>(names.error)));
    juce::Array<juce::var> instruments, indexValues;
    for (std::size_t i = 0; i < indices.value.size(); ++i)
    {
        indexValues.add(static_cast<int>(indices.value[i]));
        instruments.add(object({{"index", static_cast<int>(indices.value[i])},
                                {"name", juce::String::fromUTF8(names.value.names[i].c_str())}}));
    }
    return object({{"schema_version", 1}, {"method", "owned-component-layout-reader"},
                   {"module_path", binary.getFullPathName()}, {"module_sha256", juce::String(digest)},
                   {"version", description.version}, {"instrument_count", static_cast<int>(count.value)},
                   {"instrument_indices", indexValues}, {"instruments", instruments},
                   {"memory_bytes_read", static_cast<int>(bytesRead)}, {"memory_budget_bytes", 65536}});
}
} // namespace ml
