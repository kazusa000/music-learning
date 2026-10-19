#include "Host.h"
#include <algorithm>
#include <cmath>
#include <limits>
#include <set>
#include <map>

namespace ml
{
namespace
{
int64 integer(const var& value, const char* key, int64 fallback = -1)
{
    auto v = field(value, key, var(fallback));
    require(v.isInt() || v.isInt64(), String("Expected integer: ") + key);
    return static_cast<int64>(v);
}

double number(const var& value, const char* key, double fallback)
{
    const auto v = field(value, key, fallback);
    require(v.isInt() || v.isInt64() || v.isDouble(), String("Expected number: ") + key);
    const auto result = static_cast<double>(v);
    require(std::isfinite(result), String("Non-finite number: ") + key);
    return result;
}

Array<var> array(const var& value, const char* key)
{
    const auto v = field(value, key, var(Array<var>{}));
    require(v.isArray(), String("Expected array: ") + key);
    return *v.getArray();
}

struct Tempo { int64 sample; double bpm; double ppq; };
struct Meter { int64 sample; int numerator; int denominator; double ppq; int64 bar; };

struct Timeline : AudioPlayHead
{
    double rate;
    double initialPpq = 0.0;
    int64 cursor = 0;
    std::vector<Tempo> tempos;
    std::vector<Meter> meters;

    Timeline(const var& project, double sampleRate) : rate(sampleRate)
    {
        initialPpq = number(project, "ppq_offset", 0.0);
        auto tempoValues = array(project, "tempo");
        if (tempoValues.isEmpty()) tempoValues.add(object({{"sample", 0}, {"bpm", 120.0}}));
        for (const auto& value : tempoValues)
        {
            checkKeys(value, {"sample", "bpm"});
            const auto sample = integer(value, "sample");
            const auto bpm = number(value, "bpm", 120.0);
            require(bpm >= 1.0 && bpm <= 1000.0 && sample >= 0, "Invalid tempo");
            require(tempos.empty() ? sample == 0 : sample > tempos.back().sample,
                    "Tempo map must start at 0 and be strictly ordered");
            const auto ppq = tempos.empty() ? initialPpq : tempos.back().ppq
                + (sample - tempos.back().sample) / rate * tempos.back().bpm / 60.0;
            tempos.push_back({sample, bpm, ppq});
        }
        auto meterValues = array(project, "meter");
        if (meterValues.isEmpty()) meterValues.add(object({{"sample", 0}, {"numerator", 4}, {"denominator", 4}}));
        for (const auto& value : meterValues)
        {
            checkKeys(value, {"sample", "numerator", "denominator", "ppq", "bar"});
            const auto sample = integer(value, "sample");
            const auto numerator = static_cast<int>(integer(value, "numerator"));
            const auto denominator = static_cast<int>(integer(value, "denominator"));
            require(sample >= 0 && numerator > 0 && numerator <= 64 && denominator > 0
                    && denominator <= 64 && (denominator & (denominator - 1)) == 0, "Invalid meter");
            require(meters.empty() ? sample == 0 : sample > meters.back().sample,
                    "Meter map must start at 0 and be strictly ordered");
            auto tempo = tempos.front();
            for (const auto& entry : tempos) if (entry.sample <= sample) tempo = entry;
            const auto atSample = tempo.ppq + (sample - tempo.sample) / rate * tempo.bpm / 60.0;
            meters.push_back({sample, numerator, denominator, number(value, "ppq", meters.empty() ? 0.0 : atSample), integer(value, "bar", 1)});
        }
    }

    Optional<PositionInfo> getPosition() const override
    {
        auto tempo = tempos.front();
        for (const auto& entry : tempos) if (entry.sample <= cursor) tempo = entry;
        auto meter = meters.front();
        for (const auto& entry : meters) if (entry.sample <= cursor) meter = entry;
        PositionInfo position;
        position.setTimeInSamples(cursor);
        position.setTimeInSeconds(cursor / rate);
        position.setBpm(tempo.bpm);
        const auto ppq = tempo.ppq + (cursor - tempo.sample) / rate * tempo.bpm / 60.0;
        position.setPpqPosition(ppq);
        const auto barLength = meter.numerator * 4.0 / meter.denominator;
        const auto bars = std::floor((ppq - meter.ppq) / barLength);
        position.setPpqPositionOfLastBarStart(meter.ppq + bars * barLength);
        position.setBarCount(meter.bar - 1 + static_cast<int64>(bars));
        position.setTimeSignature(TimeSignature{meter.numerator, meter.denominator});
        position.setIsPlaying(true);
        position.setIsRecording(false);
        position.setIsLooping(false);
        return position;
    }

    int64 nextBoundary(int64 end) const
    {
        for (const auto& tempo : tempos) if (tempo.sample > cursor) end = std::min(end, tempo.sample);
        for (const auto& meter : meters) if (meter.sample > cursor) end = std::min(end, meter.sample);
        return end;
    }
};

struct MidiEvent { int64 sample; int priority; MidiMessage message; };
struct Automation { int64 sample; AudioProcessorParameter* parameter; float value; bool linear = false; };
struct Ramp { int64 start, end; AudioProcessorParameter* parameter; float from, to; };
struct MixAutomation
{
    String parameter;
    bool linear = false;
    std::vector<std::pair<int64, double>> points;
    size_t index = 0;
    Optional<double> at(int64 sample)
    {
        if (sample < points.front().first) return {};
        while (index + 1 < points.size() && points[index + 1].first <= sample) ++index;
        if (!linear || index + 1 == points.size()) return points[index].second;
        const auto a = points[index], b = points[index + 1];
        return a.second + (b.second - a.second) * static_cast<double>(sample - a.first) / static_cast<double>(b.first - a.first);
    }
};
struct AudioClip
{
    std::unique_ptr<AudioFormatReader> reader;
    int64 start, sourceOffset, length;
    float gain;
};

void writeWave(const File& output, const AudioBuffer<float>& audio, double rate)
{
    require(!output.exists(), "Output already exists; choose a new artifact path");
    require(output.getParentDirectory().createDirectory().wasOk(), "Cannot create audio directory");
    TemporaryFile temporary(output);
    std::unique_ptr<OutputStream> stream = temporary.getFile().createOutputStream();
    require(stream != nullptr, "Cannot create audio output");
    WavAudioFormat format;
    auto options = AudioFormatWriterOptions{}.withSampleRate(rate).withNumChannels(2)
        .withBitsPerSample(32).withSampleFormat(AudioFormatWriterOptions::SampleFormat::floatingPoint);
    auto writer = format.createWriterFor(stream, options);
    require(writer != nullptr, "Cannot create WAV writer");
    require(writer->writeFromAudioSampleBuffer(audio, 0, audio.getNumSamples()), "WAV write failed");
    writer.reset();
    require(temporary.overwriteTargetFileWithTemporary(), "Cannot publish WAV output");
}
} // namespace

// Acyclic offline graph: every node's WAV is aligned to the source clock after
// removing its own plugin latency. Reading aligned inputs compensates unequal
// path lengths at every summing/sidechain point without Python audio processing.
var renderGraph(const var& request)
{
    auto project = field(request, "project").clone();
    checkKeys(project, {"schema_version", "revision_id", "sample_rate", "block_size", "duration_samples",
        "tail_samples", "tempo", "meter", "ppq_offset", "tracks", "graph"});
    const auto graph = field(project, "graph");
    checkKeys(graph, {"edges", "master"});
    auto nodes = array(project, "tracks");
    const auto edges = array(graph, "edges");
    const auto master = text(graph, "master");
    const auto duration = integer(project, "duration_samples");
    const auto tail = integer(project, "tail_samples", 0);
    const auto rate = number(project, "sample_rate", 48000.0);
    require(duration > 0 && duration <= rate * 600 && tail >= 0 && tail <= rate * 60, "Graph duration outside budget");
    require(nodes.size() > 0 && nodes.size() <= 64, "Expected 1..64 graph nodes");
    std::map<String, var> pending;
    for (const auto& node : nodes)
        require(pending.emplace(text(node, "id"), node).second, "Duplicate graph node");
    require(pending.count(master) == 1, "Master node missing");
    for (const auto& edge : edges)
    {
        checkKeys(edge, {"source", "target", "kind", "gain_db", "plugin_id"});
        require(pending.count(text(edge, "source")) && pending.count(text(edge, "target")), "Unknown graph endpoint");
        const auto kind = text(edge, "kind");
        require(kind == "main" || kind == "send" || kind == "sidechain", "Unknown edge kind");
    }
    const File output(text(request, "output_path"));
    require(!output.exists(), "Output already exists");
    const auto directory = output.getSiblingFile(output.getFileNameWithoutExtension() + "-nodes");
    require(!directory.exists() && directory.createDirectory().wasOk(), "Graph node directory already exists or cannot be created");
    std::map<String, var> completed;
    Array<var> reports, snapshots;
    while (!pending.empty())
    {
        auto ready = pending.end();
        for (auto it = pending.begin(); it != pending.end(); ++it)
        {
            bool available = true;
            for (const auto& edge : edges)
                if (text(edge, "target") == it->first && !completed.count(text(edge, "source"))) available = false;
            if (available) { ready = it; break; }
        }
        require(ready != pending.end(), "Routing cycle");
        const auto id = ready->first;
        auto node = ready->second.clone();
        auto clips = array(node, "audio_clips");
        Array<var> sidechains;
        for (const auto& edge : edges)
        {
            if (text(edge, "target") != id) continue;
            auto clip = object({{"path", field(completed.at(text(edge, "source")), "output_path")},
                {"sample", 0}, {"duration_samples", duration + tail},
                {"gain", Decibels::decibelsToGain(number(edge, "gain_db", 0.0))}});
            if (text(edge, "kind") == "sidechain")
            {
                clip.getDynamicObject()->setProperty("plugin_id", text(edge, "plugin_id"));
                sidechains.add(clip);
            }
            else clips.add(clip);
        }
        node.getDynamicObject()->setProperty("audio_clips", clips);
        node.getDynamicObject()->setProperty("sidechain_clips", sidechains);
        node.getDynamicObject()->setProperty("allow_empty", true);
        auto child = project.clone();
        child.getDynamicObject()->removeProperty("graph");
        child.getDynamicObject()->setProperty("duration_samples", duration + tail);
        child.getDynamicObject()->setProperty("tail_samples", 0);
        child.getDynamicObject()->setProperty("tracks", var(Array<var>{node}));
        const auto path = directory.getChildFile(id + ".wav");
        auto result = render(object({{"project", child}, {"output_path", path.getFullPathName()}}));
        reports.add(object({{"id", id}, {"output_path", path.getFullPathName()}, {"report", result}}));
        auto saved = ready->second.clone();
        saved.getDynamicObject()->setProperty("plugins", field(array(field(result, "snapshot"), "tracks")[0], "plugins"));
        snapshots.add(saved);
        completed.emplace(id, result);
        pending.erase(ready);
    }
    require(File(text(completed.at(master), "output_path")).copyFileTo(output), "Cannot publish graph master");
    auto result = completed.at(master).clone();
    project.getDynamicObject()->setProperty("tracks", snapshots);
    result.getDynamicObject()->setProperty("snapshot", project);
    result.getDynamicObject()->setProperty("output_path", output.getFullPathName());
    result.getDynamicObject()->setProperty("node_renders", reports);
    return result;
}

var render(const var& request)
{
    const auto started = Time::getMillisecondCounterHiRes();
    auto project = field(request, "project").clone();
    if (field(project, "graph").isObject()) return renderGraph(request);
    require(project.isObject(), "project must be an object");
    checkKeys(project, {"schema_version", "revision_id", "sample_rate", "block_size", "duration_samples",
                        "tail_samples", "tempo", "meter", "tracks", "ppq_offset"});
    const auto revision = text(project, "revision_id");
    const double rate = number(project, "sample_rate", 48000.0);
    const int blockSize = static_cast<int>(integer(project, "block_size", 256));
    const int64 duration = integer(project, "duration_samples");
    const int64 tail = integer(project, "tail_samples", 0);
    require(rate >= 8000 && rate <= 192000 && rate == std::floor(rate), "Invalid sample rate");
    require(blockSize >= 16 && blockSize <= 8192, "Invalid block size");
    require(duration > 0 && tail >= 0 && duration <= rate * 660 && tail <= rate * 60,
            "P0 render budget: at most 600 seconds content and 60 seconds tail");
    const int64 total = duration + tail;
    require(total <= std::numeric_limits<int>::max(), "Render too large");
    auto tracks = array(project, "tracks");
    require(!tracks.isEmpty() && tracks.size() <= 64, "Expected 1..64 tracks");
    const File output(text(request, "output_path"));
    require(!output.exists(), "Output already exists");
    AudioBuffer<float> mix(2, static_cast<int>(total));
    mix.clear();
    Timeline timeline(project, rate);
    Array<var> trackReports;
    std::set<String> ids;

    for (auto& track : tracks)
    {
        checkKeys(track, {"id", "gain_db", "pan", "plugins", "notes", "controllers", "audio_clips", "automation", "mix_automation", "sidechain_clips", "allow_empty", "muted"});
        const auto trackId = text(track, "id");
        require(ids.insert(trackId).second, "Duplicate track ID");
        const auto gainDb = number(track, "gain_db", 0.0);
        const auto pan = number(track, "pan", 0.0);
        require(gainDb >= -120 && gainDb <= 24 && pan >= -1 && pan <= 1, "Invalid gain/pan");
        const float gain = static_cast<bool>(field(track, "muted", false)) ? 0.0f : Decibels::decibelsToGain(static_cast<float>(gainDb));
        const float leftGain = gain * static_cast<float>(pan > 0 ? 1 - pan : 1);
        const float rightGain = gain * static_cast<float>(pan < 0 ? 1 + pan : 1);
        auto specs = array(track, "plugins");
        std::vector<std::unique_ptr<AudioPluginInstance>> plugins;
        Array<var> snapshots;
        std::set<String> pluginIds;
        int latency = 0;
        for (const auto& spec : specs)
        {
            const auto id = text(spec, "id");
            require(pluginIds.insert(id).second, "Duplicate plugin instance ID");
            auto plugin = load(spec, rate, blockSize);
            require(plugin->getTotalNumInputChannels() <= 4 && plugin->getTotalNumOutputChannels() == 2,
                    "Expected stereo output and optional stereo sidechain");
            plugin->setPlayHead(&timeline);
            plugin->setRateAndBufferSizeDetails(rate, blockSize);
            plugin->prepareToPlay(rate, blockSize);
            auto snapshot = pluginSnapshot(*plugin);
            snapshot.getDynamicObject()->setProperty("id", id);
            if (static_cast<bool>(field(spec, "sidechain", false))) snapshot.getDynamicObject()->setProperty("sidechain", true);
            snapshots.add(snapshot);
            latency += plugin->getLatencySamples();
            plugins.push_back(std::move(plugin));
        }
        require(latency >= 0 && latency < rate * 10, "Plugin latency outside P0 budget");
        track.getDynamicObject()->setProperty("plugins", snapshots);

        std::vector<MidiEvent> events;
        for (const auto& note : array(track, "notes"))
        {
            checkKeys(note, {"id", "sample", "duration_samples", "note", "channel", "velocity"});
            const auto start = integer(note, "sample");
            const auto length = integer(note, "duration_samples");
            const auto pitch = static_cast<int>(integer(note, "note"));
            const auto channel = static_cast<int>(integer(note, "channel", 1));
            const auto velocity = number(note, "velocity", 0.8);
            require(start >= 0 && length > 0 && start < duration && length <= duration - start,
                    "Note outside content duration");
            require(pitch >= 0 && pitch <= 127 && channel >= 1 && channel <= 16 && velocity > 0 && velocity <= 1,
                    "Invalid note pitch/channel/velocity");
            events.push_back({start, 2, MidiMessage::noteOn(channel, pitch, static_cast<float>(velocity))});
            events.push_back({start + length, 0, MidiMessage::noteOff(channel, pitch)});
        }
        for (const auto& control : array(track, "controllers"))
        {
            checkKeys(control, {"sample", "channel", "controller", "value"});
            const auto sample = integer(control, "sample");
            const auto channel = static_cast<int>(integer(control, "channel", 1));
            const auto cc = static_cast<int>(integer(control, "controller"));
            const auto value = static_cast<int>(integer(control, "value"));
            require(sample >= 0 && sample <= duration && channel >= 1 && channel <= 16
                    && cc >= 0 && cc <= 127 && value >= 0 && value <= 127, "Invalid MIDI CC");
            events.push_back({sample, 1, MidiMessage::controllerEvent(channel, cc, value)});
        }
        std::stable_sort(events.begin(), events.end(), [](const auto& a, const auto& b) {
            return a.sample == b.sample ? a.priority < b.priority : a.sample < b.sample;
        });

        std::vector<Automation> automation;
        std::vector<Ramp> ramps;
        for (const auto& lane : array(track, "automation"))
        {
            checkKeys(lane, {"plugin_id", "parameter_id", "interpolation", "points"});
            const auto interpolation = field(lane, "interpolation", "step").toString();
            require(interpolation == "step" || interpolation == "linear", "Invalid interpolation");
            const auto id = text(lane, "plugin_id");
            int index = -1;
            for (int i = 0; i < snapshots.size(); ++i)
                if (field(snapshots[i], "id").toString() == id) index = i;
            require(index >= 0, "Automation plugin not found");
            auto* param = parameter(*plugins[static_cast<size_t>(index)], text(lane, "parameter_id"));
            require(param->isAutomatable(), "Parameter does not support automation");
            int64 previous = -1;
            float previousValue = 0.0f;
            for (const auto& point : array(lane, "points"))
            {
                checkKeys(point, {"sample", "value"});
                const auto sample = integer(point, "sample");
                const auto value = number(point, "value", -1);
                require(sample >= 0 && sample <= duration && sample > previous && value >= 0 && value <= 1,
                        "Invalid automation points");
                if (interpolation == "linear" && previous >= 0 && previousValue != static_cast<float>(value))
                    ramps.push_back({previous, sample, param, previousValue, static_cast<float>(value)});
                automation.push_back({sample, param, static_cast<float>(value), interpolation == "linear"});
                previous = sample;
                previousValue = static_cast<float>(value);
            }
        }
        std::stable_sort(automation.begin(), automation.end(), [](auto a, auto b) { return a.sample < b.sample; });
        std::vector<MixAutomation> mixAutomation;
        std::set<String> mixTargets;
        for (const auto& lane : array(track, "mix_automation"))
        {
            checkKeys(lane, {"parameter_id", "interpolation", "points"});
            const auto target = text(lane, "parameter_id");
            require((target == "gain" || target == "pan") && mixTargets.insert(target).second, "Invalid or duplicate mixer target");
            const auto interpolation = text(lane, "interpolation");
            require(interpolation == "step" || interpolation == "linear", "Invalid mixer interpolation");
            MixAutomation value;
            value.parameter = target; value.linear = interpolation == "linear";
            for (const auto& point : array(lane, "points"))
            {
                checkKeys(point, {"sample", "value"});
                const auto sample = integer(point, "sample");
                const auto normalized = number(point, "value", -1);
                require(sample >= 0 && sample <= duration && normalized >= 0 && normalized <= 1
                    && (value.points.empty() || sample > value.points.back().first), "Invalid mixer point");
                value.points.emplace_back(sample, normalized);
            }
            require(!value.points.empty(), "Mixer automation needs points");
            mixAutomation.push_back(std::move(value));
        }

        AudioFormatManager formats;
        formats.registerBasicFormats();
        std::vector<AudioClip> clips;
        for (const auto& clip : array(track, "audio_clips"))
        {
            checkKeys(clip, {"id", "path", "sample", "source_offset_samples", "duration_samples", "gain"});
            auto reader = std::unique_ptr<AudioFormatReader>(formats.createReaderFor(File(text(clip, "path"))));
            require(reader != nullptr, "Cannot read audio clip");
            require(reader->sampleRate == rate && reader->numChannels >= 1 && reader->numChannels <= 2,
                    "P0 audio clips must match project sample rate and be mono/stereo");
            const auto start = integer(clip, "sample");
            const auto offset = integer(clip, "source_offset_samples", 0);
            const auto length = integer(clip, "duration_samples", reader->lengthInSamples - offset);
            require(start >= 0 && start < duration && offset >= 0 && length > 0
                    && length <= reader->lengthInSamples - offset && length <= duration - start, "Invalid audio clip range");
            clips.push_back({std::move(reader), start, offset, length, static_cast<float>(number(clip, "gain", 1.0))});
        }
        std::vector<std::pair<String, AudioClip>> sidechains;
        for (const auto& clip : array(track, "sidechain_clips"))
        {
            checkKeys(clip, {"path", "sample", "duration_samples", "gain", "plugin_id"});
            auto reader = std::unique_ptr<AudioFormatReader>(formats.createReaderFor(File(text(clip, "path"))));
            require(reader != nullptr && reader->sampleRate == rate && reader->numChannels == 2, "Invalid sidechain audio");
            const auto length = integer(clip, "duration_samples");
            const auto id = text(clip, "plugin_id");
            bool found = false;
            for (int i = 0; i < snapshots.size(); ++i)
                if (text(snapshots[i], "id") == id)
                {
                    require(plugins[static_cast<size_t>(i)]->getTotalNumInputChannels() == 4, "Sidechain bus not enabled");
                    found = true;
                }
            require(found && length > 0 && length <= reader->lengthInSamples, "Invalid sidechain target/range");
            sidechains.emplace_back(id, AudioClip{std::move(reader), integer(clip, "sample", 0), 0, length,
                static_cast<float>(number(clip, "gain", 1.0))});
        }
        require(!events.empty() || !clips.empty() || static_cast<bool>(field(track, "allow_empty", false)), "Track has no MIDI or audio source");
        require(events.empty() || !plugins.empty(), "MIDI requires an instrument plugin");
        AudioBuffer<float> block(4, blockSize), source(2, blockSize);
        size_t eventIndex = 0, automationIndex = 0;
        int64 cursor = 0;
        int blocks = 0;
        while (cursor < total + latency)
        {
            timeline.cursor = cursor;
            while (automationIndex < automation.size() && automation[automationIndex].sample == cursor)
            {
                auto& point = automation[automationIndex++];
                point.parameter->setValueNotifyingHost(point.value);
            }
            int64 end = timeline.nextBoundary(std::min(cursor + blockSize, total + latency));
            for (const auto& ramp : ramps)
            {
                if (ramp.start <= cursor && cursor < ramp.end)
                {
                    const auto fraction = static_cast<double>(cursor - ramp.start) / static_cast<double>(ramp.end - ramp.start);
                    ramp.parameter->setValueNotifyingHost(static_cast<float>(ramp.from + fraction * (ramp.to - ramp.from)));
                    end = std::min(end, cursor + 1);
                }
                else if (ramp.start > cursor) end = std::min(end, ramp.start);
            }
            if (automationIndex < automation.size()) end = std::min(end, automation[automationIndex].sample);
            require(end > cursor, "Invalid processing boundary");
            const int count = static_cast<int>(end - cursor);
            block.clear();
            for (auto& clip : clips)
            {
                const auto begin = std::max(cursor, clip.start);
                const auto finish = std::min(end, clip.start + clip.length);
                if (begin >= finish) continue;
                const int n = static_cast<int>(finish - begin);
                source.clear();
                require(clip.reader->read(&source, 0, n, clip.sourceOffset + begin - clip.start, true, true),
                        "Audio clip read failed");
                for (int channel = 0; channel < 2; ++channel)
                    block.addFrom(channel, static_cast<int>(begin - cursor), source, channel, 0, n, clip.gain);
            }
            MidiBuffer midi;
            while (eventIndex < events.size() && events[eventIndex].sample < end)
            {
                const auto& event = events[eventIndex++];
                require(event.sample >= cursor, "MIDI scheduler lost an event");
                midi.addEvent(event.message, static_cast<int>(event.sample - cursor));
            }
            int precedingLatency = 0;
            for (size_t pluginIndex = 0; pluginIndex < plugins.size(); ++pluginIndex)
            {
                auto& plugin = plugins[pluginIndex];
                block.clear(2, 0, count); block.clear(3, 0, count);
                for (auto& entry : sidechains)
                {
                    if (entry.first != text(snapshots[static_cast<int>(pluginIndex)], "id")) continue;
                    auto& clip = entry.second;
                    const auto sideCursor = cursor - precedingLatency;
                    const auto begin = std::max(sideCursor, clip.start);
                    const auto finish = std::min(end - precedingLatency, clip.start + clip.length);
                    if (begin >= finish) continue;
                    const auto n = static_cast<int>(finish - begin);
                    source.clear();
                    require(clip.reader->read(&source, 0, n, begin - clip.start, true, true), "Sidechain read failed");
                    for (int channel = 0; channel < 2; ++channel)
                        block.addFrom(channel + 2, static_cast<int>(begin - sideCursor), source, channel, 0, n, clip.gain);
                }
                AudioBuffer<float> view(block.getArrayOfWritePointers(), std::max(2, plugin->getTotalNumInputChannels()), count);
                plugin->processBlock(view, midi);
                precedingLatency += plugin->getLatencySamples();
            }
            int currentLatency = 0;
            for (const auto& plugin : plugins) currentLatency += plugin->getLatencySamples();
            require(currentLatency == latency, "Plugin latency changed during render; rerender required");
            const auto destination = cursor - latency;
            const int skip = static_cast<int>(std::max<int64>(0, -destination));
            const auto begin = std::max<int64>(0, destination);
            const auto n = static_cast<int>(std::min<int64>(count - skip, total - begin));
            if (n > 0)
            {
                if (mixAutomation.empty())
                {
                    mix.addFrom(0, static_cast<int>(begin), block, 0, skip, n, leftGain);
                    mix.addFrom(1, static_cast<int>(begin), block, 1, skip, n, rightGain);
                }
                else for (int i = 0; i < n; ++i)
                {
                    auto db = gainDb, balance = pan;
                    for (auto& lane : mixAutomation)
                        if (const auto value = lane.at(begin + i))
                        {
                            if (lane.parameter == "gain") db = *value * 144.0 - 120.0;
                            else balance = *value * 2.0 - 1.0;
                        }
                    const auto dynamicGain = static_cast<bool>(field(track, "muted", false)) ? 0.0f : Decibels::decibelsToGain(static_cast<float>(db));
                    mix.addSample(0, static_cast<int>(begin) + i, block.getSample(0, skip + i) * dynamicGain * static_cast<float>(balance > 0 ? 1 - balance : 1));
                    mix.addSample(1, static_cast<int>(begin) + i, block.getSample(1, skip + i) * dynamicGain * static_cast<float>(balance < 0 ? 1 + balance : 1));
                }
            }
            cursor = end;
            if (++blocks % 64 == 0) MessageManager::getInstance()->runDispatchLoopUntil(1);
        }
        Array<var> after;
        for (auto& plugin : plugins)
        {
            after.add(pluginSnapshot(*plugin));
            plugin->releaseResources();
            plugin->setPlayHead(nullptr);
        }
        trackReports.add(object({{"id", trackId}, {"latency_removed_samples", latency},
                                 {"plugin_readback_after", after}, {"midi_events", static_cast<int>(events.size())}}));
    }
    project.getDynamicObject()->setProperty("tracks", tracks);
    double sumSquares = 0.0, peak = 0.0;
    int64 clipped = 0, nonSilent = 0;
    for (int channel = 0; channel < 2; ++channel)
        for (int i = 0; i < mix.getNumSamples(); ++i)
        {
            const auto sample = static_cast<double>(mix.getSample(channel, i));
            require(std::isfinite(sample), "Non-finite plugin output");
            peak = std::max(peak, std::abs(sample));
            sumSquares += sample * sample;
            if (std::abs(sample) >= 1.0) ++clipped;
            if (std::abs(sample) > 1e-6) ++nonSilent;
        }
    writeWave(output, mix, rate);
    return object({{"revision_id", revision}, {"output_path", output.getFullPathName()},
                   {"frames", total}, {"sample_rate", rate}, {"channels", 2},
                   {"peak", peak}, {"rms", std::sqrt(sumSquares / (total * 2.0))},
                   {"clipped_samples", clipped}, {"non_silent_samples", nonSilent},
                   {"elapsed_seconds", (Time::getMillisecondCounterHiRes() - started) / 1000.0},
                   {"tracks", trackReports}, {"snapshot", project}});
}
} // namespace ml
