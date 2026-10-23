#include "../audio-host/AmpleQueueObjectObserver.h"

#include <array>
#include <cstdint>
#include <iostream>

namespace
{
void fixture_callback() noexcept {}

struct FixtureObject
{
    std::uintptr_t vtable = 0;
    std::uint64_t unknown_field = 0;
    std::uintptr_t owner = 0;
    std::int32_t pending = 0;
    std::uint32_t reserved = 0;
    std::array<std::uint64_t, 4> payload{};
};
static_assert(sizeof(FixtureObject) == 64);
static_assert(offsetof(FixtureObject, owner) == 0x10);
static_assert(offsetof(FixtureObject, pending) == 0x18);

struct FixtureOwner
{
    std::uintptr_t vtable = 0;
    std::array<std::uint64_t, 7> payload{};
};
static_assert(sizeof(FixtureOwner) == 64);

void fixture_owner_callback() noexcept {}

std::array<std::uintptr_t, 2> fixture_vtable{
    0,
    reinterpret_cast<std::uintptr_t>(&fixture_callback),
};
std::array<std::uintptr_t, 2> unknown_vtable{
    0,
    reinterpret_cast<std::uintptr_t>(&fixture_callback),
};
std::array<std::uintptr_t, 2> owner_vtable{
    0,
    reinterpret_cast<std::uintptr_t>(&fixture_owner_callback),
};
std::array<FixtureObject, 40> fixture_objects{};
std::array<FixtureOwner, 40> fixture_owners{};
std::array<std::uintptr_t, 40> fixture_pointers{};

struct FixtureQueue
{
    std::array<std::uint8_t, 0x38> prefix{};
    std::uintptr_t* array = nullptr;
    std::uint32_t capacity = 0;
    std::uint32_t count = 0;
    std::uint32_t reserved = 0;
};
static_assert(offsetof(FixtureQueue, array) == 0x38);
static_assert(offsetof(FixtureQueue, capacity) == 0x40);
static_assert(offsetof(FixtureQueue, count) == 0x44);

FixtureQueue fixture_queue{};
FixtureQueue* fixture_queue_root = &fixture_queue;
constexpr std::string_view fixture_sha =
    "fixture-amplitude-queue-observer-module-pin";

bool check(bool condition, const char* label)
{
    if (!condition)
        std::cerr << "FAIL: " << label << '\n';
    return condition;
}

bool image_size(HMODULE module, std::uint32_t& size)
{
    const auto base = reinterpret_cast<std::uintptr_t>(module);
    IMAGE_DOS_HEADER dos{};
    SIZE_T read = 0;
    if (!ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<const void*>(base),
                           &dos, sizeof(dos), &read)
        || read != sizeof(dos) || dos.e_magic != IMAGE_DOS_SIGNATURE
        || dos.e_lfanew < static_cast<LONG>(sizeof(dos)))
        return false;
    IMAGE_NT_HEADERS64 nt{};
    if (!ReadProcessMemory(GetCurrentProcess(),
            reinterpret_cast<const void*>(base + static_cast<std::uintptr_t>(dos.e_lfanew)),
            &nt, sizeof(nt), &read)
        || read != sizeof(nt) || nt.Signature != IMAGE_NT_SIGNATURE
        || nt.OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC)
        return false;
    size = nt.OptionalHeader.SizeOfImage;
    return size != 0;
}

juce::Array<juce::var> readiness(HMODULE module, std::uint32_t imageSize,
                                  std::string_view digest)
{
    auto* row = new juce::DynamicObject();
    row->setProperty("product", "Ample Guitar LP");
    row->setProperty("manufacturer", "Ample Sound");
    row->setProperty("version", "4.0.0");
    row->setProperty("module_sha256", juce::String(digest.data(), static_cast<int>(digest.size())));
    row->setProperty("module_base", juce::String::toHexString(
        static_cast<juce::int64>(reinterpret_cast<std::uintptr_t>(module))));
    row->setProperty("image_size", static_cast<int>(imageSize));
    row->setProperty("ready", true);
    juce::Array<juce::var> result;
    result.add(juce::var(row));
    return result;
}

juce::var child(const juce::var& parent, const char* name)
{
    return parent.getProperty(juce::Identifier(name), {});
}

}

int main()
{
    const auto module = GetModuleHandleW(nullptr);
    std::uint32_t moduleImageSize = 0;
    if (module == nullptr || !image_size(module, moduleImageSize))
        return 2;
    const auto base = reinterpret_cast<std::uintptr_t>(module);
    const auto rootSlotAddress = reinterpret_cast<std::uintptr_t>(&fixture_queue_root);
    if (rootSlotAddress < base || rootSlotAddress - base >= moduleImageSize)
        return 3;
    if (rootSlotAddress == reinterpret_cast<std::uintptr_t>(&fixture_queue))
        return 4;

    const ml::diagnostic::AmpleQueueLayoutPin pin{
        fixture_sha, moduleImageSize,
        static_cast<std::uint32_t>(rootSlotAddress - base),
        0x38, 0x40, 0x44,
        static_cast<std::uint32_t>(reinterpret_cast<std::uintptr_t>(fixture_vtable.data()) - base),
        0x10, 0x18};
    ml::diagnostic::AmpleQueueObjectObserver observer(pin);
    const auto ready = readiness(module, moduleImageSize, fixture_sha);
    for (std::size_t index = 0; index < 40; ++index)
    {
        fixture_objects[index].vtable = index == 0
            ? reinterpret_cast<std::uintptr_t>(fixture_vtable.data())
            : reinterpret_cast<std::uintptr_t>(unknown_vtable.data());
        fixture_owners[index].vtable = reinterpret_cast<std::uintptr_t>(owner_vtable.data());
        fixture_objects[index].owner = reinterpret_cast<std::uintptr_t>(&fixture_owners[index]);
        fixture_objects[index].pending = index == 0 ? 1 : 0;
        fixture_owners[index].payload.fill(0x12340000u + index);
        fixture_objects[index].payload.fill(0xabc00000u + index);
        fixture_pointers[index] = reinterpret_cast<std::uintptr_t>(&fixture_objects[index]);
    }
    fixture_queue.array = fixture_pointers.data();
    fixture_queue.capacity = 40;
    fixture_queue.count = 2;
    fixture_queue_root = &fixture_queue;

    bool ok = true;
    const auto beforeBytes = fixture_objects[0];
    const auto token = observer.begin_dispatch(7, 0, 64, 32768, 512);
    observer.capture_before(token, ready);
    observer.capture_after(token, ready);
    ok &= check(fixture_objects[0].vtable == beforeBytes.vtable
                && fixture_objects[0].payload == beforeBytes.payload,
                "observing objects does not mutate fixture memory");

    fixture_queue.count = 40;
    const auto truncationToken = observer.begin_dispatch(8, 0, 128, 65536, 512);
    observer.capture_before(truncationToken, ready);
    observer.capture_after(truncationToken, ready);

    const auto report = observer.report();
    ok &= check(report.getProperty(juce::Identifier("read_only"), false),
                "report declares read-only observation");
    ok &= check(!report.getProperty(juce::Identifier("callback_invoked"), true),
                "observer never invokes the captured virtual callback");
    const auto dispatches = child(report, "dispatches");
    const auto* rows = dispatches.getArray();
    ok &= check(rows != nullptr && rows->size() == 2,
                "both dispatch windows are retained");
    if (rows != nullptr && rows->size() == 2)
    {
        const auto& first = (*rows)[0];
        ok &= check(static_cast<int>(first.getProperty(juce::Identifier("message_observer_dispatch_id"), 0)) == 7,
                    "queue observation aligns with the existing message dispatch ID");
        ok &= check(static_cast<int>(first.getProperty(juce::Identifier("source_sample_start"), 0)) == 32768,
                    "queue observation retains source sample start");
        const auto before = child(first, "before");
        ok &= check(before.getProperty(juce::Identifier("header_stable"), false),
                    "header is read twice and stable fixture header is identified");
        ok &= check(before.getProperty(juce::Identifier("queue_root_stable"), false)
                    && before.getProperty(juce::Identifier("objects_validated_against_stable_queue"), false),
                    "indirect root slot and pointed queue header are both stable");
        ok &= check(before.getProperty(juce::Identifier("queue_root_slot_address_hex"), {}).toString()
                        .endsWith(juce::String::toHexString(static_cast<juce::int64>(rootSlotAddress)))
                    && before.getProperty(juce::Identifier("queue_root_before_hex"), {}).toString()
                        .endsWith(juce::String::toHexString(static_cast<juce::int64>(
                            reinterpret_cast<std::uintptr_t>(&fixture_queue))))
                    && before.getProperty(juce::Identifier("queue_address_hex"), {}).toString()
                        .endsWith(juce::String::toHexString(static_cast<juce::int64>(
                            reinterpret_cast<std::uintptr_t>(&fixture_queue)))),
                    "global RVA is dereferenced as a pointer slot before reading the queue object");
        ok &= check(static_cast<int>(before.getProperty(juce::Identifier("objects_recorded"), 0)) == 2,
                    "two queue objects are recorded in the first snapshot");
        const auto beforeObjects = child(before, "objects");
        const auto* objectRows = beforeObjects.getArray();
        ok &= check(objectRows != nullptr && objectRows->size() == 2,
                    "object rows match the stable queue count");
        if (objectRows != nullptr && objectRows->size() == 2)
        {
            const auto& object = (*objectRows)[0];
            ok &= check(static_cast<int>(object.getProperty(juce::Identifier("raw_bytes_read"), 0)) == 64,
                        "each object raw capture is bounded at 64 bytes");
            ok &= check(object.getProperty(juce::Identifier("vtable_module")).getProperty(
                            juce::Identifier("resolved"), false),
                        "fixture vtable identity resolves to the own module");
            ok &= check(object.getProperty(juce::Identifier("callback_module")).getProperty(
                            juce::Identifier("resolved"), false),
                        "virtual slot plus eight callback identity resolves");
            ok &= check(object.getProperty(juce::Identifier("recognized_async_updater_message"), false),
                        "only the exact pinned fixture vtable enters the known object path");
            ok &= check(static_cast<int>(object.getProperty(
                            juce::Identifier("async_pending_original_value"), -1)) == 1
                        && object.getProperty(juce::Identifier("async_pending_read_status")).toString() == "ok",
                        "original pending value is read without mutation");
            ok &= check(object.getProperty(juce::Identifier("async_owner_address_hex"), {}).toString()
                            .endsWith(juce::String::toHexString(static_cast<juce::int64>(
                                reinterpret_cast<std::uintptr_t>(&fixture_owners[0])))),
                        "owner is read only at the exact REA-pinned object offset");
            ok &= check(static_cast<int>(object.getProperty(
                            juce::Identifier("async_owner_raw_bytes_read"), 0)) == 64,
                        "owner raw capture is bounded at 64 bytes");
            ok &= check(object.getProperty(juce::Identifier("async_owner_callback_module")).getProperty(
                            juce::Identifier("resolved"), false),
                        "owner virtual slot plus eight callback identity resolves");

            const auto& unknown = (*objectRows)[1];
            ok &= check(!unknown.getProperty(
                            juce::Identifier("recognized_async_updater_message"), true)
                        && unknown.getProperty(juce::Identifier("owner")).toString() == "unknown"
                        && unknown.getProperty(juce::Identifier(
                            "async_pending_read_status")).toString()
                            == "not_applicable_unrecognized_vtable",
                        "unrecognized vtable remains raw-only without speculative owner reads");
        }

        const auto secondBefore = child((*rows)[1], "before");
        ok &= check(static_cast<int>(secondBefore.getProperty(juce::Identifier("objects_recorded"), 0)) == 32,
                    "object capture stops at the hard limit of 32");
        ok &= check(static_cast<int>(secondBefore.getProperty(juce::Identifier("count_truncated"), 0)) == 8,
                    "unread queue remainder is counted as truncated");
    }

    ml::diagnostic::AmpleQueueObjectObserver wrongIdentity({
        "wrong-module-sha", moduleImageSize, pin.queue_global_rva, 0x38, 0x40, 0x44});
    const auto mismatchReady = readiness(module, moduleImageSize, fixture_sha);
    const auto mismatchToken = wrongIdentity.begin_dispatch(9, 0, 192, 98304, 512);
    wrongIdentity.capture_before(mismatchToken, mismatchReady);
    const auto mismatchReport = wrongIdentity.report();
    const auto mismatchRows = child(mismatchReport, "dispatches").getArray();
    ok &= check(mismatchRows != nullptr && mismatchRows->size() == 1
                && child((*mismatchRows)[0], "before").getProperty(
                       juce::Identifier("status")).toString()
                    == "unsupported_or_mismatched_module_identity",
                "a nonmatching module pin is rejected before queue memory access");

    fixture_queue.count = 41;
    const auto invalidToken = observer.begin_dispatch(10, 0, 256, 131072, 512);
    observer.capture_before(invalidToken, ready);
    const auto invalidReport = observer.report();
    const auto invalidRows = child(invalidReport, "dispatches").getArray();
    ok &= check(invalidRows != nullptr && invalidRows->size() == 3
                && child((*invalidRows)[2], "before").getProperty(
                       juce::Identifier("status")).toString()
                    == "invalid_queue_header_bounds",
                "count above capacity is rejected without enumerating objects");

    const std::array<std::uint8_t, 16> sameHeader{};
    ok &= check(!ml::diagnostic::AmpleQueueObjectObserver::stable_snapshot(
                    reinterpret_cast<std::uintptr_t>(&fixture_queue),
                    reinterpret_cast<std::uintptr_t>(&fixture_queue) + sizeof(FixtureQueue),
                    sameHeader, sameHeader),
                "a changed indirect queue root keeps the snapshot unknown despite identical headers");

    if (!ok)
        return 1;
    std::cout << "Ample queue-object observer contract passed\n";
    return 0;
}
