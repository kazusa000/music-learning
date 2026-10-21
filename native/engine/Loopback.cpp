// Bounded system-render loopback probe. No endpoint or master-volume changes.
#define NOMINMAX
#include <windows.h>
#include <mmdeviceapi.h>
#include <audioclient.h>
#include <wrl/client.h>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <vector>
#include <cmath>
#include <cstring>
#include <iomanip>
#include <mmreg.h>
#include <cstdint>
#include "AudioScheduling.h"

using Microsoft::WRL::ComPtr;
static void check(HRESULT value) { if (FAILED(value)) throw value; }
static void u32(std::ostream& file, unsigned int value) { file.write(reinterpret_cast<const char*>(&value), 4); }
static double sample(const BYTE* data,unsigned bits,bool floating){
    if(floating&&bits==32){float value=0;std::memcpy(&value,data,4);return value;}
    if(!floating&&bits==16){int16_t value=0;std::memcpy(&value,data,2);return value/32768.0;}
    if(!floating&&bits==24){int32_t value=(int32_t(data[0])<<8)|(int32_t(data[1])<<16)|(int32_t(data[2])<<24);return value/2147483648.0;}
    if(!floating&&bits==32){int32_t value=0;std::memcpy(&value,data,4);return value/2147483648.0;}
    throw HRESULT(E_NOTIMPL);
}
int wmain(int argc, wchar_t** argv) {
    const bool meter=argc==4&&std::wstring(argv[1])==L"--meter";
    if (argc != 3&&!meter) return 2;
    try {
        const int target=meter?2:1;
        if(std::filesystem::exists(std::filesystem::path(argv[target])))return 7;
        const double seconds = std::stod(argv[target+1]); if (!(seconds > 0 && seconds <= (meter?3600:15))) return 3;
        check(CoInitializeEx(nullptr, COINIT_MULTITHREADED));
        ComPtr<IMMDeviceEnumerator> enumerator;
        check(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL, IID_PPV_ARGS(&enumerator)));
        ComPtr<IMMDevice> device; check(enumerator->GetDefaultAudioEndpoint(eRender, eConsole, &device));
        ComPtr<IAudioClient> client; check(device->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr, &client));
        WAVEFORMATEX* raw = nullptr; check(client->GetMixFormat(&raw));
        const auto rate = raw->nSamplesPerSec; const auto channels = raw->nChannels;
        const auto alignment = raw->nBlockAlign; const auto formatSize = unsigned(sizeof(WAVEFORMATEX) + raw->cbSize);
        const auto bits=raw->wBitsPerSample;
        const auto tag=raw->wFormatTag==WAVE_FORMAT_EXTENSIBLE?reinterpret_cast<WAVEFORMATEXTENSIBLE*>(raw)->SubFormat.Data1:raw->wFormatTag;
        const bool floating=tag==WAVE_FORMAT_IEEE_FLOAT;
        if(meter&&tag!=WAVE_FORMAT_PCM&&tag!=WAVE_FORMAT_IEEE_FLOAT)return 6;
        std::vector<char> format(reinterpret_cast<char*>(raw), reinterpret_cast<char*>(raw) + formatSize);
        // Capture buffering does not add output latency. A one-second bounded
        // reservoir tolerates scheduler stalls while preserving packet QPCs.
        check(client->Initialize(AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_LOOPBACK, 10000000, 0, raw, nullptr));
        CoTaskMemFree(raw);
        ComPtr<IAudioCaptureClient> capture; check(client->GetService(IID_PPV_ARGS(&capture)));
        mlengine::AudioScheduling scheduling;
        std::vector<char> samples;if(!meter)samples.reserve(size_t(seconds * rate * alignment));
        std::ofstream metrics;
        if(meter){
            metrics.open(std::filesystem::path(argv[target]),std::ios::binary);
            if(!metrics)return 4;
            metrics<<"{\"kind\":\"format\",\"sample_rate\":"<<rate<<",\"channels\":"<<channels<<",\"bits\":"<<bits<<",\"tag\":"<<tag<<",\"pro_audio_thread_registration\":"<<(scheduling.active()?"true":"false")<<"}\n";metrics.flush();
        }
        check(client->Start()); std::cout << "READY" << std::endl;
        const auto start = std::chrono::steady_clock::now();auto last=start;
        unsigned int discontinuities = 0,timestampErrors=0;
        uint64_t totalFrames=0,firstQpc=0,lastQpc=0,expectedPosition=0,gapFrames=0,outOfOrder=0,invalid=0,windowSamples=0;
        double square=0,peak=0;bool hasPacket=false;
        auto publish=[&](bool final){
            metrics<<std::setprecision(12)<<"{\"kind\":\""<<(final?"final":"window")<<"\",\"elapsed\":"<<std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count()
                <<",\"frames\":"<<totalFrames<<",\"discontinuities\":"<<discontinuities<<",\"timestamp_errors\":"<<timestampErrors
                <<",\"gap_frames\":"<<gapFrames<<",\"out_of_order_packets\":"<<outOfOrder<<",\"invalid_samples\":"<<invalid
                <<",\"first_packet_qpc_100ns\":"<<firstQpc<<",\"last_packet_qpc_100ns\":"<<lastQpc
                <<",\"peak\":"<<peak<<",\"rms\":"<<(windowSamples?std::sqrt(square/(double)windowSamples):0)<<"}\n";
            metrics.flush();square=peak=0;windowSamples=0;
        };
        while (std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count() < seconds) {
            UINT32 next = 0; check(capture->GetNextPacketSize(&next));
            while (next) {
                BYTE* data = nullptr; UINT32 frames = 0; DWORD flags = 0;UINT64 position=0,qpc=0;
                check(capture->GetBuffer(&data, &frames, &flags, &position, &qpc));
                if ((flags & AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY) && hasPacket) ++discontinuities;
                if(flags&AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR)++timestampErrors;
                if(hasPacket){if(position>expectedPosition)gapFrames+=position-expectedPosition;else if(position<expectedPosition)++outOfOrder;}
                if(!hasPacket)firstQpc=qpc;hasPacket=true;lastQpc=qpc;expectedPosition=position+frames;totalFrames+=frames;
                if(meter){
                    for(UINT32 i=0;i<frames;++i)for(WORD channel=0;channel<channels;++channel){
                        const double value=flags&AUDCLNT_BUFFERFLAGS_SILENT?0:sample(data+(size_t)i*alignment+channel*(bits/8),bits,floating);
                        if(!std::isfinite(value)){++invalid;continue;}square+=value*value;peak=std::max(peak,std::abs(value));++windowSamples;
                    }
                }else{
                    const auto offset = samples.size(); samples.resize(offset + size_t(frames) * alignment, 0);
                    if (!(flags & AUDCLNT_BUFFERFLAGS_SILENT)) std::memcpy(samples.data() + offset, data, size_t(frames) * alignment);
                }
                check(capture->ReleaseBuffer(frames)); check(capture->GetNextPacketSize(&next));
            }
            if(meter&&std::chrono::duration<double>(std::chrono::steady_clock::now()-last).count()>=.2){publish(false);last=std::chrono::steady_clock::now();}
            Sleep(2);
        }
        check(client->Stop());
        if(meter)publish(true);
        else{
            std::ofstream file(std::filesystem::path(argv[target]), std::ios::binary);
            if (!file) return 4;
            file.write("RIFF", 4); u32(file, 4 + 8 + formatSize + 8 + (unsigned)samples.size());
            file.write("WAVEfmt ", 8); u32(file, formatSize); file.write(format.data(), format.size());
            file.write("data", 4); u32(file, (unsigned)samples.size()); file.write(samples.data(), samples.size());
        }
        std::cout << "{\"sample_rate\":" << rate << ",\"channels\":" << channels
            << ",\"frames\":" << totalFrames << ",\"discontinuities\":" << discontinuities
            <<",\"timestamp_errors\":"<<timestampErrors<<",\"gap_frames\":"<<gapFrames<<",\"out_of_order_packets\":"<<outOfOrder
            <<",\"first_packet_qpc_100ns\":"<<firstQpc<<",\"last_packet_qpc_100ns\":"<<lastQpc<<"}" << std::endl;
        return 0;
    } catch (HRESULT error) { std::cerr << "WASAPI loopback HRESULT: " << std::hex << error << std::endl; return 5; }
}
