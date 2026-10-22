// Separate module/build: never replace the existing verification fixture.
#include <juce_audio_processors/juce_audio_processors.h>
// Reuse the exact fixture implementation as a base in this test translation
// only. JUCE headers are already included; their classes are unaffected.
#define final
#define createPluginFilter createBaseFixture
#include "../test-plugin/Fixture.cpp"
#undef createPluginFilter
#undef final

class DynamicFixture final:public Fixture {
    std::array<std::array<float,128>,2> extra{};
    int cursor=0,latency=0;
    bool firstProcess=true;
    uint64_t processed=0;
    void select(){
        const int next=getParameters()[0]->getValue()<.75f?128:0;
        if(next!=latency){latency=next;cursor=0;for(auto& channel:extra)channel.fill(0);setLatencySamples(128+latency);}
    }
public:
    DynamicFixture(){
        if(const auto* path=std::getenv("ML_DYNAMIC_LOAD_GATE")){
            const juce::File gate(juce::String::fromUTF8(path));
            gate.getSiblingFile(gate.getFileName()+".entered").appendText("loading\n");
            const auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(5);
            while(gate.existsAsFile()&&std::chrono::steady_clock::now()<deadline)
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
            if(gate.existsAsFile())throw std::runtime_error("Dynamic fixture loading gate timed out");
        }
        if(std::getenv("ML_DYNAMIC_LOAD_DELAY"))std::this_thread::sleep_for(std::chrono::milliseconds(200));
    }
    ~DynamicFixture()override{
        if(const auto* marker=std::getenv("ML_DYNAMIC_RELEASE_MARKER")){
            if(std::getenv("ML_DYNAMIC_RELEASE_HANG"))std::this_thread::sleep_for(std::chrono::seconds(5));
            // Each destructor owns its evidence file. Concurrent processes
            // must not race through seek-to-end on one appendText stream.
            juce::File(juce::String::fromUTF8(marker)+".released-"+juce::Uuid().toString()).replaceWithText("released\n");
        }
    }
    void prepareToPlay(double rate,int frames)override{Fixture::prepareToPlay(rate,frames);select();firstProcess=true;processed=0;}
    void reset()override{Fixture::prepareToPlay(getSampleRate(),getBlockSize());cursor=0;for(auto& channel:extra)channel.fill(0);}
    const juce::String getName()const override{return "MLDynamicFixture";}
    void processBlock(juce::AudioBuffer<float>& audio,juce::MidiBuffer& midi)override{
        if(++processed==20&&std::getenv("ML_DYNAMIC_STEADY_DELAY"))std::this_thread::sleep_for(std::chrono::milliseconds(12));
        if(firstProcess){firstProcess=false;if(std::getenv("ML_DYNAMIC_FIRST_PROCESS_DELAY"))std::this_thread::sleep_for(std::chrono::milliseconds(75));}
        select();Fixture::processBlock(audio,midi);
        if(latency)for(int i=0;i<audio.getNumSamples();++i){
            for(int ch=0;ch<2;++ch){const float value=audio.getSample(ch,i);audio.setSample(ch,i,extra[(size_t)ch][(size_t)cursor]);extra[(size_t)ch][(size_t)cursor]=value;}
            cursor=(cursor+1)%128;
        }
    }
};
juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter(){return new DynamicFixture;}
