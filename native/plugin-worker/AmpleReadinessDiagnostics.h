#pragma once

#include "../engine/SharedPlugin.h"
#include "../instrument-probe/AmpleProbe.h"

#if defined(ML_ENABLE_MIDI_DELIVERY_DIAGNOSTICS)
#include <algorithm>
#include <cstdio>

namespace ml {
enum class AmpleReadinessPhase : uint32_t {
    afterStateRestore = 1,
    beforeFirstProcessBlock = 2,
    afterFinalProcessBlock = 3,
};

inline uint64_t readinessPointer(const juce::DynamicObject* value,
                                 const char* property) {
    if (value == nullptr) return 0;
    const auto text = value->getProperty(property).toString();
    return text.getHexValue64();
}

inline void copyReadinessText(char* destination, size_t capacity,
                              const juce::String& value) noexcept {
    if (capacity == 0) return;
    std::snprintf(destination, capacity, "%s", value.toUTF8().getAddress());
}

// Read-only AmpleProbe snapshots are kept in the diagnostic-only shared ring.
// This function is compiled only into the isolated MIDI-delivery build.
inline void recordAmpleReadiness(mlengine::PluginRing* shared,
                                 juce::AudioPluginInstance& plugin,
                                 uint32_t pluginIndex, int64_t sourceSample,
                                 AmpleReadinessPhase phase) noexcept {
    if (shared == nullptr) return;
    auto& diagnostics = shared->midiDelivery;
    const auto index = diagnostics.ampleReadinessPublished.load(
        std::memory_order_relaxed);
    if (index >= mlengine::ampleReadinessRecordCapacity) {
        diagnostics.ampleReadinessDropped.fetch_add(1,
            std::memory_order_relaxed);
        return;
    }

    auto& record = diagnostics.ampleReadiness[static_cast<size_t>(index)];
    record.sourceSample = sourceSample;
    record.pluginIndex = pluginIndex;
    record.phase = static_cast<uint32_t>(phase);
    record.fieldsRead = 0;
    record.ready = 0;
    record.loadingByteRead = 0;
    record.loaderErrorCountRead = 0;
    record.statusRead = 0;
    record.loadingByte = -1;
    record.loaderErrorCount = -1;
    record.componentVtable = 0;
    record.normalizedComponentVtable = 0;
    record.normalizedComponentWrapper = 0;
    record.componentNormalizedOffset = 0;
    record.componentPointerReads = 0;
    record.componentBytesRead = 0;
    record.processorSubobject = 0;
    record.processorSubobjectVtable = 0;
    record.primaryProcessor = 0;
    record.product[0] = '\0';
    record.version[0] = '\0';
    record.modulePath[0] = '\0';
    record.moduleSha256[0] = '\0';
    record.status[0] = '\0';
    record.statusString[0] = '\0';

    try {
        const char* phaseName = phase == AmpleReadinessPhase::afterStateRestore
            ? "after_state_restore"
            : phase == AmpleReadinessPhase::beforeFirstProcessBlock
                ? "before_first_process_block" : "after_final_process_block";
        const auto response = ampleReadiness(plugin, phaseName);
        const auto* outer = response.getDynamicObject();
        if (outer == nullptr) {
            copyReadinessText(record.status, sizeof(record.status),
                              "invalid_probe_response");
        } else {
            copyReadinessText(record.status, sizeof(record.status),
                              outer->getProperty("status").toString());
            copyReadinessText(record.product, sizeof(record.product),
                outer->getProperty("product").toString());
            copyReadinessText(record.version, sizeof(record.version),
                outer->getProperty("version").toString());
            copyReadinessText(record.modulePath, sizeof(record.modulePath),
                outer->getProperty("module_path").toString());
            copyReadinessText(record.moduleSha256, sizeof(record.moduleSha256),
                outer->getProperty("module_sha256").toString());
            const auto* layout = outer->getProperty("component_layout").getDynamicObject();
            if (layout != nullptr) {
                record.normalizedComponentWrapper = readinessPointer(layout,
                    "normalized_wrapper");
                record.componentNormalizedOffset = static_cast<uint64_t>(
                    layout->getProperty("normalized_offset").toString().getIntValue());
                record.componentPointerReads = static_cast<uint32_t>(
                    layout->getProperty("pointer_reads").toString().getIntValue());
                record.componentBytesRead = static_cast<uint32_t>(
                    layout->getProperty("bytes_read").toString().getIntValue());
            }
            const auto* snapshot = outer->getProperty("readiness").getDynamicObject();
            if (snapshot != nullptr) {
                record.normalizedComponentVtable = readinessPointer(snapshot,
                    "component_vtable");
                record.processorSubobject = readinessPointer(snapshot,
                    "processor_subobject");
                record.processorSubobjectVtable = readinessPointer(snapshot,
                    "processor_subobject_vtable");
                record.primaryProcessor = readinessPointer(snapshot,
                    "primary_processor");
                const auto loading = snapshot->getProperty("loading_byte");
                record.loadingByteRead = static_cast<bool>(
                    snapshot->getProperty("loading_byte_read"));
                if (record.loadingByteRead && !loading.isVoid()
                    && !loading.isUndefined())
                    record.loadingByte = static_cast<int32_t>(loading);
                const auto errors = snapshot->getProperty("loader_error_count");
                record.loaderErrorCountRead = static_cast<bool>(
                    snapshot->getProperty("loader_error_count_read"));
                if (record.loaderErrorCountRead && !errors.isVoid()
                    && !errors.isUndefined())
                    record.loaderErrorCount = static_cast<int32_t>(errors);
                record.statusRead = static_cast<bool>(
                    snapshot->getProperty("status_read"));
                record.fieldsRead = static_cast<bool>(
                    snapshot->getProperty("fields_read"));
                record.ready = static_cast<bool>(snapshot->getProperty("ready"));
                copyReadinessText(record.statusString, sizeof(record.statusString),
                    snapshot->getProperty("status_string").toString());
            }
            record.componentVtable = readinessPointer(outer, "component_vtable");
        }
    } catch (const std::exception& error) {
        copyReadinessText(record.status, sizeof(record.status),
                          "probe_exception");
        copyReadinessText(record.statusString, sizeof(record.statusString),
                          error.what());
    } catch (...) {
        copyReadinessText(record.status, sizeof(record.status),
                          "probe_exception");
    }

    record.sequence.store(index + 1, std::memory_order_release);
    diagnostics.ampleReadinessPublished.store(index + 1,
        std::memory_order_release);
}
} // namespace ml
#endif
