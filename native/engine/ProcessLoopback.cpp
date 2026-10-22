// Diagnostic only: Windows process-tree loopback, not an endpoint-latency meter.
// API reference: Microsoft Windows-classic-samples / ApplicationLoopback.
#define NOMINMAX
#include <windows.h>
#include <audioclient.h>
#include <audioclientactivationparams.h>
#include <mmdeviceapi.h>
#include <mmreg.h>
#include <wrl.h>
#include <array>
#include <chrono>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <vector>
#include "AudioScheduling.h"

using Microsoft::WRL::ComPtr;
static void requireHr(HRESULT value){if(FAILED(value))throw value;}
struct Handle{
    HANDLE value=nullptr;
    ~Handle(){if(value&&value!=INVALID_HANDLE_VALUE)CloseHandle(value);}
};
struct Apartment{
    Apartment(){requireHr(CoInitializeEx(nullptr,COINIT_MULTITHREADED));}
    ~Apartment(){CoUninitialize();}
};
class Activation final:public Microsoft::WRL::RuntimeClass<
    Microsoft::WRL::RuntimeClassFlags<Microsoft::WRL::ClassicCom>,
    Microsoft::WRL::FtmBase,IActivateAudioInterfaceCompletionHandler>{
public:
    Handle done;HRESULT result=E_PENDING;ComPtr<IAudioClient> client;
    Activation(){done.value=CreateEventW(nullptr,TRUE,FALSE,nullptr);}
    IFACEMETHODIMP ActivateCompleted(IActivateAudioInterfaceAsyncOperation* operation) override{
        HRESULT activation=E_UNEXPECTED;ComPtr<IUnknown> activated;
        result=operation->GetActivateResult(&activation,&activated);
        if(SUCCEEDED(result))result=activation;
        if(SUCCEEDED(result))result=activated.As(&client);
        SetEvent(done.value);return S_OK;
    }
};
struct StopClient{
    IAudioClient* client;bool started=false;
    ~StopClient(){if(started)client->Stop();}
};
static void writeAll(HANDLE file,const void* data,DWORD bytes){
    DWORD written=0;
    if(!WriteFile(file,data,bytes,&written,nullptr)||written!=bytes)throw std::runtime_error("Cannot write process capture");
}
int wmain(int argc,wchar_t** argv){
    if(argc!=4)return 2;
    try{
        size_t parsed=0;const auto pid=std::stoul(argv[1],&parsed,10);
        if(!pid||pid>MAXDWORD||parsed!=std::wcslen(argv[1]))return 2;
        const double seconds=std::stod(argv[3],&parsed);if(parsed!=std::wcslen(argv[3])||!(seconds>=.2&&seconds<=5))return 3;
        const std::filesystem::path output(argv[2]);if(std::filesystem::exists(output))return 7;
        Apartment apartment;
        Handle target;target.value=OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION|SYNCHRONIZE,FALSE,(DWORD)pid);
        if(!target.value)throw std::runtime_error("Cannot identify capture target");
        std::array<wchar_t,32768> image{};DWORD imageLength=(DWORD)image.size();
        if(!QueryFullProcessImageNameW(target.value,0,image.data(),&imageLength)
           ||_wcsicmp(std::filesystem::path(image.data()).filename().c_str(),L"ml_engine.exe"))
            throw std::runtime_error("Process capture requires an owned ml_engine target");
        auto completion=Microsoft::WRL::Make<Activation>();
        if(!completion||!completion->done.value)throw std::runtime_error("Cannot create activation event");
        AUDIOCLIENT_ACTIVATION_PARAMS params{};
        params.ActivationType=AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK;
        params.ProcessLoopbackParams.TargetProcessId=(DWORD)pid;
        params.ProcessLoopbackParams.ProcessLoopbackMode=PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE;
        PROPVARIANT property{};property.vt=VT_BLOB;property.blob.cbSize=sizeof(params);
        property.blob.pBlobData=reinterpret_cast<BYTE*>(&params);
        ComPtr<IActivateAudioInterfaceAsyncOperation> pending;
        requireHr(ActivateAudioInterfaceAsync(VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,__uuidof(IAudioClient),
            &property,completion.Get(),&pending));
        if(WaitForSingleObject(completion->done.value,5000)!=WAIT_OBJECT_0)throw std::runtime_error("Process loopback activation timed out");
        requireHr(completion->result);auto client=completion->client;
        WAVEFORMATEX format{WAVE_FORMAT_IEEE_FLOAT,2,48000,384000,8,32,0};
        requireHr(client->Initialize(AUDCLNT_SHAREMODE_SHARED,AUDCLNT_STREAMFLAGS_LOOPBACK
            |AUDCLNT_STREAMFLAGS_EVENTCALLBACK|AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM,0,0,&format,nullptr));
        Handle ready;ready.value=CreateEventW(nullptr,FALSE,FALSE,nullptr);
        if(!ready.value)throw std::runtime_error("Cannot create capture event");
        requireHr(client->SetEventHandle(ready.value));
        ComPtr<IAudioCaptureClient> capture;requireHr(client->GetService(IID_PPV_ARGS(&capture)));
        mlengine::AudioScheduling scheduling;StopClient stop{client.Get()};
        requireHr(client->Start());stop.started=true;
        std::vector<BYTE> samples;const size_t bound=(size_t)((seconds+1)*format.nAvgBytesPerSec);
        samples.reserve(bound);
        uint64_t framesTotal=0,gaps=0,outOfOrder=0,firstQpc=0,lastQpc=0,expected=0;
        uint64_t qpcGaps=0,qpcOutOfOrder=0,zeroPositions=0,invalidSamples=0;UINT32 previousFrames=0;
        unsigned discontinuities=0,initialDiscontinuities=0,timestampErrors=0;bool hasPacket=false;
        const auto begin=std::chrono::steady_clock::now();
        while(std::chrono::duration<double>(std::chrono::steady_clock::now()-begin).count()<seconds){
            if(WaitForSingleObject(ready.value,100)==WAIT_FAILED)throw std::runtime_error("Capture event failed");
            UINT32 available=0;requireHr(capture->GetNextPacketSize(&available));
            while(available){
                BYTE* data=nullptr;UINT32 frames=0;DWORD flags=0;UINT64 position=0,qpc=0;
                requireHr(capture->GetBuffer(&data,&frames,&flags,&position,&qpc));
                try{
                    if(flags&AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY){if(hasPacket)++discontinuities;else ++initialDiscontinuities;}
                    if(flags&AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR)++timestampErrors;
                    if(hasPacket){if(position>expected)gaps+=position-expected;else if(position<expected)++outOfOrder;}
                    if(position==0)++zeroPositions;
                    // The virtual process source may return device position 0
                    // for every packet. Preserve those raw diagnostics; use
                    // packet QPC continuity only for this software capture.
                    if(hasPacket){
                        if(qpc<=lastQpc)++qpcOutOfOrder;
                        else{
                            const double deltaFrames=(double)(qpc-lastQpc)*format.nSamplesPerSec/10000000.0-previousFrames;
                            if(deltaFrames>1.0)qpcGaps+=(uint64_t)(deltaFrames+.5);
                            else if(deltaFrames< -1.0)++qpcOutOfOrder;
                        }
                    }
                    previousFrames=frames;
                    if(!hasPacket)firstQpc=qpc;hasPacket=true;lastQpc=qpc;expected=position+frames;framesTotal+=frames;
                    const size_t bytes=(size_t)frames*format.nBlockAlign,offset=samples.size();
                    if(bytes>bound-offset)throw std::runtime_error("Process capture exceeded its fixed memory bound");
                    samples.resize(offset+bytes,0);
                    if(!(flags&AUDCLNT_BUFFERFLAGS_SILENT)){
                        if(!data)throw std::runtime_error("Missing process PCM");
                        std::memcpy(samples.data()+offset,data,bytes);
                        for(size_t at=0;at<bytes;at+=sizeof(float)){
                            float value=0;std::memcpy(&value,data+at,sizeof(value));
                            if(!std::isfinite(value))++invalidSamples;
                        }
                    }
                }catch(...){capture->ReleaseBuffer(frames);throw;}
                requireHr(capture->ReleaseBuffer(frames));requireHr(capture->GetNextPacketSize(&available));
            }
        }
        requireHr(client->Stop());stop.started=false;
        if(framesTotal<format.nSamplesPerSec/2)throw std::runtime_error("Insufficient process capture frames");
        DWORD code=STILL_ACTIVE;
        if(!GetExitCodeProcess(target.value,&code)||code!=STILL_ACTIVE)throw std::runtime_error("Capture target exited");
        Handle file;file.value=CreateFileW(output.c_str(),GENERIC_WRITE,0,nullptr,CREATE_NEW,FILE_ATTRIBUTE_NORMAL,nullptr);
        if(file.value==INVALID_HANDLE_VALUE)throw std::runtime_error("Cannot create new process capture file");
        std::array<BYTE,44> header{};
        auto number=[&](size_t at,DWORD value){std::memcpy(header.data()+at,&value,4);};
        std::memcpy(header.data(),"RIFF",4);number(4,(DWORD)samples.size()+36);
        std::memcpy(header.data()+8,"WAVEfmt ",8);number(16,16);std::memcpy(header.data()+20,&format,16);
        std::memcpy(header.data()+36,"data",4);number(40,(DWORD)samples.size());
        writeAll(file.value,header.data(),(DWORD)header.size());writeAll(file.value,samples.data(),(DWORD)samples.size());
        std::cout<<"{\"scope\":\"windows_process_tree_loopback\",\"target_pid\":"<<pid
            <<",\"sample_rate\":"<<format.nSamplesPerSec<<",\"channels\":2,\"bits\":32,\"frames\":"<<framesTotal
            <<",\"initial_discontinuities\":"<<initialDiscontinuities<<",\"discontinuities\":"<<discontinuities
            <<",\"timestamp_errors\":"<<timestampErrors<<",\"raw_device_gap_frames\":"<<gaps<<",\"raw_device_out_of_order_packets\":"<<outOfOrder
            <<",\"zero_device_position_packets\":"<<zeroPositions<<",\"packet_qpc_gap_frames\":"<<qpcGaps<<",\"packet_qpc_out_of_order\":"<<qpcOutOfOrder
            <<",\"invalid_samples\":"<<invalidSamples
            <<",\"first_packet_qpc_100ns\":"<<firstQpc<<",\"last_packet_qpc_100ns\":"<<lastQpc
            <<",\"pro_audio_thread_registration\":"<<(scheduling.active()?"true":"false")<<"}\n";
        return 0;
    }catch(HRESULT error){std::cerr<<"Process loopback HRESULT: "<<std::hex<<error<<"\n";return 5;}
    catch(const std::exception& error){std::cerr<<error.what()<<"\n";return 6;}
}
