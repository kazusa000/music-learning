#pragma once
#include <juce_audio_devices/juce_audio_devices.h>
#include <juce_audio_formats/juce_audio_formats.h>
#include <atomic>
#include <thread>
#include <memory>
#include <map>
#include "PluginSource.h"

namespace mlengine {
using namespace juce;
using PluginPool = std::map<String,std::shared_ptr<PluginSource>>;

// All readers and resamplers are consumed exclusively by the producer thread.
class FileTimeline final : public AudioSource {
    struct Clip { std::unique_ptr<AudioFormatReader> reader; int64 start, offset, length; float gain; };
    std::vector<Clip> clips;
    int64 cursor, loopStart, loopEnd;
    bool loop;
public:
    std::atomic<bool> failed{false};
    FileTimeline(const var& track, double rate, int64 start, bool looping, int64 begin, int64 end)
        : cursor(start), loopStart(begin), loopEnd(end), loop(looping) {
        if (auto* items = track.getProperty("clips", {}).getArray()) for (const auto& item : *items) {
            auto input = std::make_unique<FileInputStream>(File(item["path"].toString()));
            if (!input->openedOk()) throw std::runtime_error("Cannot open project audio");
            AudioFormatManager formats; formats.registerBasicFormats();
            std::unique_ptr<AudioFormatReader> reader(formats.createReaderFor(std::move(input)));
            if (!reader || reader->sampleRate != rate || reader->numChannels > 2)
                throw std::runtime_error("Project audio must be supported source-rate mono/stereo audio");
            const auto offset = (int64)item.getProperty("source_offset_samples", (int64)0);
            const auto length = (int64)item["duration_samples"];
            if (offset < 0 || length < 0 || offset + length > reader->lengthInSamples)
                throw std::runtime_error("Project audio clip exceeds source");
            clips.push_back({std::move(reader), (int64)item.getProperty("start_sample", (int64)0), offset, length,
                (float)(double)item.getProperty("gain", 1.0)});
        }
    }
    void prepareToPlay(int, double) override {}
    void releaseResources() override {}
    void getNextAudioBlock(const AudioSourceChannelInfo& info) override {
        info.clearActiveBufferRegion();
        int written = 0;
        while (written < info.numSamples) {
            if (loop && cursor >= loopEnd) cursor = loopStart + (cursor - loopStart) % (loopEnd - loopStart);
            const int count = loop ? (int)jmin((int64)(info.numSamples - written), loopEnd - cursor) : info.numSamples - written;
            for (auto& clip : clips) {
                const auto begin = jmax(cursor, clip.start), end = jmin(cursor + count, clip.start + clip.length);
                if (end <= begin) continue;
                const int n = (int)(end - begin), offset = info.startSample + written + (int)(begin - cursor);
                // N2 compiler uses disjoint clips per audio track. Overlapping
                // arrangement layers will compile into separate sources in N5.
                if (!clip.reader->read(info.buffer, offset, n, clip.offset + begin - clip.start, true, true)) failed.store(true);
                info.buffer->applyGain(offset, n, clip.gain);
            }
            cursor += count; written += count;
        }
    }
};

class FileGraph {
public:
    static constexpr int capacity = 8;
    struct Track {
        String id;
        std::unique_ptr<FileTimeline> source;
        std::unique_ptr<ResamplingAudioSource> resampler;
        std::shared_ptr<PluginSource> plugin;
        std::atomic<float> gainDb{0}, pan{0};
        std::atomic<bool> mute{false}, solo{false};
        std::atomic<float> peak{0};
        float bakedGain=0, bakedPan=0, monitorGain=0, soloGain=0, left=0, right=0;
    };
    struct Block { std::vector<AudioBuffer<float>> tracks; double endSample=0; };
    var manifest;
    std::vector<std::unique_ptr<Track>> tracks;
    std::array<Block, capacity> blocks;
    std::atomic<uint64_t> written{0}, read{0}, underruns{0};
    std::atomic<bool> stopping{false};
    std::atomic<bool> failed{false};
    std::atomic<double> playedSample{0};
    std::thread producer;
    int frames;
    double sourceRate, deviceRate, duration, songDuration, origin, loopStart, loopEnd;
    bool looping;

    FileGraph(const var& value, double outputRate, int quantum, double start, bool loop, double begin, double end,PluginPool* pool=nullptr)
        : manifest(value), frames(quantum), sourceRate((double)value["sample_rate"]), deviceRate(outputRate),
          duration((double)value["duration_samples"] + (double)value.getProperty("tail_samples", 0)),
          songDuration((double)value["duration_samples"]), origin(start), loopStart(begin), loopEnd(end), looping(loop) {
        if (!(sourceRate > 0 && duration > 0 && quantum > 0 && quantum <= 4096)) throw std::runtime_error("Invalid file graph clock");
        if (looping && !(begin >= 0 && end > begin && end <= songDuration)) throw std::runtime_error("Invalid file loop");
        if (start < 0 || start > duration) throw std::runtime_error("Invalid file position");
        playedSample.store(start);
        if (auto* items = value["tracks"].getArray()) for (const auto& item : *items) {
            auto track = std::make_unique<Track>(); track->id = item["id"].toString();
            track->gainDb.store((float)(double)item.getProperty("gain_db", 0.0));
            track->pan.store((float)(double)item.getProperty("pan", 0.0));
            track->bakedGain = (float)(double)item.getProperty("baked_gain_db", 0.0);
            track->bakedPan = (float)(double)item.getProperty("baked_pan", 0.0);
            track->monitorGain = (float)(double)item.getProperty("monitor_gain_db", 0.0);
            track->soloGain = (float)(double)item.getProperty("solo_gain_db", track->monitorGain);
            if(item.hasProperty("plugin_project_path")) {
                if(!pool)throw std::runtime_error("Plugin pool missing");
                const auto key=value["project_id"].toString()+":"+track->id+":"+item["runtime_signature"].toString();
                auto& source=(*pool)[key];
                if(!source||source->retryRequired())source=std::make_shared<PluginSource>(item,File(value["session_directory"].toString()),sourceRate,deviceRate,frames);
                source->configure(File(item["plugin_project_path"].toString()),(int64)start,loop,(int64)begin,(int64)end);track->plugin=source;
            }else {
                track->source = std::make_unique<FileTimeline>(item, sourceRate, (int64)start, loop, (int64)begin, (int64)end);
                track->resampler = std::make_unique<ResamplingAudioSource>(track->source.get(), false, 2);
                track->resampler->setResamplingRatio(sourceRate / deviceRate);
                track->resampler->prepareToPlay(frames, deviceRate);
            }
            tracks.push_back(std::move(track));
        }
        if (tracks.empty() || tracks.size() > 128) throw std::runtime_error("File graph needs 1..128 tracks");
        for (auto& block : blocks) for (size_t i = 0; i < tracks.size(); ++i) block.tracks.emplace_back(2, frames);
        producer = std::thread([this] {
            uint64_t counter = 0;
            while (!stopping.load()) {
                if (counter - read.load(std::memory_order_acquire) >= capacity) { Thread::sleep(1); continue; }
                auto& block = blocks[counter % capacity];
                for (size_t i = 0; i < tracks.size(); ++i) {
                    if(tracks[i]->plugin){block.tracks[i].clear();continue;}
                    AudioSourceChannelInfo info(&block.tracks[i], 0, frames); tracks[i]->resampler->getNextAudioBlock(info);
                    if (tracks[i]->source->failed.load()) failed.store(true);
                    for(int ch=0;ch<2;++ch)for(int sample=0;sample<frames;++sample)
                        if(!std::isfinite(block.tracks[i].getSample(ch,sample)))failed.store(true);
                }
                const double source = origin + (double)((counter + 1) * (uint64_t)frames) * sourceRate / deviceRate;
                block.endSample = looping && source >= loopEnd ? loopStart + std::fmod(source - loopStart, loopEnd-loopStart) : jmin(source, duration);
                ++counter; written.store(counter, std::memory_order_release);
            }
        });
        const auto deadline = Time::getMillisecondCounterHiRes() + 3000;
        while (written.load(std::memory_order_acquire) < 2 && Time::getMillisecondCounterHiRes() < deadline) Thread::sleep(1);
        if (written.load() < 2) { stopping.store(true); producer.join(); throw std::runtime_error("Audio prefetch timed out"); }
        if (failed.load()) { stopping.store(true); producer.join(); throw std::runtime_error("Audio decoding failed"); }
    }
    ~FileGraph() { stopping.store(true); if (producer.joinable()) producer.join(); }
    var monitorValues() const {
        auto* values = new DynamicObject();
        for (auto& track : tracks) {
            auto* v = new DynamicObject(); v->setProperty("gain_db", track->gainDb.load()); v->setProperty("pan", track->pan.load());
            v->setProperty("mute", track->mute.load()); v->setProperty("solo", track->solo.load()); values->setProperty(track->id,var(v));
        }return var(values);
    }
    var meterValues() const {
        auto* values=new DynamicObject();for(const auto& track:tracks)values->setProperty(track->id,track->peak.load());return var(values);
    }
    var pluginValues(bool& ready) const {
        auto* values=new DynamicObject();ready=true;
        for(const auto& track:tracks)if(track->plugin){const auto state=track->plugin->status();if(state["status"].toString()!="ready")ready=false;values->setProperty(track->id,state);}
        return var(values);
    }
    void monitor(const var& values) {
        if(!values.isObject())throw std::runtime_error("Invalid monitor map");
        for(const auto& field:values.getDynamicObject()->getProperties()) {
            bool found=false;for(const auto& track:tracks)if(track->id==field.name.toString())found=true;
            if(!found)throw std::runtime_error("Unknown monitor track");
        }
        for(auto& track:tracks) {
            const auto value=values.getProperty(track->id,{});if(value.isVoid())continue;
            if(value.hasProperty("gain_db"))track->gainDb.store((float)(double)value["gain_db"]);
            if(value.hasProperty("pan"))track->pan.store((float)(double)value["pan"]);
            if(value.hasProperty("mute"))track->mute.store((bool)value["mute"]);
            if(value.hasProperty("solo"))track->solo.store((bool)value["solo"]);
        }
    }
    bool consume(float* const* output, int channels, int count, float& peak) {
        if(failed.load())return false;
        const auto current = read.load(std::memory_order_relaxed);
        if (count != frames || current == written.load(std::memory_order_acquire)) { underruns.fetch_add(1); return false; }
        auto& block = blocks[current % capacity];
        bool soloed = false; int audible = 0;
        for (const auto& track : tracks) if (track->solo.load()) soloed = true;
        for (const auto& track : tracks) if (!track->mute.load() && (!soloed || track->solo.load())) ++audible;
        for (size_t t = 0; t < tracks.size(); ++t) {
            auto& track = *tracks[t]; const auto pan = track.pan.load();
            const auto* mapped=track.plugin?track.plugin->block(current,count):nullptr;
            const float level = track.mute.load() || (soloed && !track.solo.load()) ? 0.0f :
                std::pow(10.0f, (track.gainDb.load() - track.bakedGain + (audible == 1 ? track.soloGain : track.monitorGain)) / 20.0f);
            const float left = level * (pan > 0 ? 1-pan : 1) / jmax(.0001f, track.bakedPan > 0 ? 1-track.bakedPan : 1);
            const float right = level * (pan < 0 ? 1+pan : 1) / jmax(.0001f, track.bakedPan < 0 ? 1+track.bakedPan : 1);
            const float ramp = (float)(1.0-std::exp(-1.0/(deviceRate*.003)));
            float trackPeak=0;
            for (int i = 0; i < count; ++i) {
                track.left += (left-track.left)*ramp; track.right += (right-track.right)*ramp;
                const float l=(mapped?mapped[i*2]:block.tracks[t].getSample(0,i))*track.left;
                const float r=(mapped?mapped[i*2+1]:block.tracks[t].getSample(1,i))*track.right;
                trackPeak=jmax(trackPeak,jmax(std::abs(l),std::abs(r)));
                if (channels > 0 && output[0]) output[0][i] += l;
                if (channels > 1 && output[1]) output[1][i] += r;
            }
            track.peak.store(trackPeak);
            if(track.plugin)track.plugin->release(current);
        }
        for (int ch = 0; ch < channels; ++ch) if (output[ch]) for (int i = 0; i < count; ++i) {
            const float x = output[ch][i], a = std::abs(x);
            output[ch][i] = std::copysign(a <= .9f ? a : .9f + .07f*(1-std::exp(-(a-.9f)/.07f)), x) * .891250938f;
            peak = jmax(peak, std::abs(output[ch][i]));
        }
        playedSample.store(block.endSample); read.store(current+1,std::memory_order_release); return true;
    }
    bool ended() const { return !looping && playedSample.load() >= duration; }
    int64 cacheBytes() const {
        int64 total=(int64)capacity * frames * 2 * sizeof(float) * (int64)tracks.size();
        for(const auto& track:tracks)if(track->plugin)total+=(int64)sizeof(PluginRing);
        return total;
    }
};
}
