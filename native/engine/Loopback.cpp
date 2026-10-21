// Bounded system-render loopback probe. No endpoint or master-volume changes.
#include <windows.h>
#include <mmdeviceapi.h>
#include <audioclient.h>
#include <wrl/client.h>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <vector>

using Microsoft::WRL::ComPtr;
static void check(HRESULT value) { if (FAILED(value)) throw value; }
static void u32(std::ostream& file, unsigned int value) { file.write(reinterpret_cast<const char*>(&value), 4); }
int wmain(int argc, wchar_t** argv) {
    if (argc != 3) return 2;
    try {
        const double seconds = std::stod(argv[2]); if (!(seconds > 0 && seconds <= 15)) return 3;
        check(CoInitializeEx(nullptr, COINIT_MULTITHREADED));
        ComPtr<IMMDeviceEnumerator> enumerator;
        check(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL, IID_PPV_ARGS(&enumerator)));
        ComPtr<IMMDevice> device; check(enumerator->GetDefaultAudioEndpoint(eRender, eConsole, &device));
        ComPtr<IAudioClient> client; check(device->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr, &client));
        WAVEFORMATEX* raw = nullptr; check(client->GetMixFormat(&raw));
        const auto rate = raw->nSamplesPerSec; const auto channels = raw->nChannels;
        const auto alignment = raw->nBlockAlign; const auto formatSize = unsigned(sizeof(WAVEFORMATEX) + raw->cbSize);
        std::vector<char> format(reinterpret_cast<char*>(raw), reinterpret_cast<char*>(raw) + formatSize);
        check(client->Initialize(AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_LOOPBACK, 1000000, 0, raw, nullptr));
        CoTaskMemFree(raw);
        ComPtr<IAudioCaptureClient> capture; check(client->GetService(IID_PPV_ARGS(&capture)));
        std::vector<char> samples; samples.reserve(size_t(seconds * rate * alignment));
        check(client->Start()); std::cout << "READY" << std::endl;
        const auto start = std::chrono::steady_clock::now(); unsigned int discontinuities = 0;
        while (std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count() < seconds) {
            UINT32 next = 0; check(capture->GetNextPacketSize(&next));
            while (next) {
                BYTE* data = nullptr; UINT32 frames = 0; DWORD flags = 0;
                check(capture->GetBuffer(&data, &frames, &flags, nullptr, nullptr));
                if ((flags & AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY) && !samples.empty()) ++discontinuities;
                const auto offset = samples.size(); samples.resize(offset + size_t(frames) * alignment, 0);
                if (!(flags & AUDCLNT_BUFFERFLAGS_SILENT)) std::memcpy(samples.data() + offset, data, size_t(frames) * alignment);
                check(capture->ReleaseBuffer(frames)); check(capture->GetNextPacketSize(&next));
            }
            Sleep(2);
        }
        check(client->Stop());
        std::ofstream file(std::filesystem::path(argv[1]), std::ios::binary);
        if (!file) return 4;
        file.write("RIFF", 4); u32(file, 4 + 8 + formatSize + 8 + (unsigned)samples.size());
        file.write("WAVEfmt ", 8); u32(file, formatSize); file.write(format.data(), format.size());
        file.write("data", 4); u32(file, (unsigned)samples.size()); file.write(samples.data(), samples.size());
        std::cout << "{\"sample_rate\":" << rate << ",\"channels\":" << channels
            << ",\"frames\":" << samples.size() / alignment << ",\"discontinuities\":" << discontinuities << "}" << std::endl;
        return 0;
    } catch (HRESULT error) { std::cerr << "WASAPI loopback HRESULT: " << std::hex << error << std::endl; return 5; }
}
