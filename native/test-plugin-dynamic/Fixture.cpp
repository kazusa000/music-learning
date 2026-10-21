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
    void select(){
        const int next=getParameters()[0]->getValue()<.75f?128:0;
        if(next!=latency){latency=next;cursor=0;for(auto& channel:extra)channel.fill(0);setLatencySamples(128+latency);}
    }
public:
    void prepareToPlay(double rate,int frames)override{Fixture::prepareToPlay(rate,frames);select();}
    void reset()override{Fixture::prepareToPlay(getSampleRate(),getBlockSize());cursor=0;for(auto& channel:extra)channel.fill(0);}
    const juce::String getName()const override{return "MLDynamicFixture";}
    void processBlock(juce::AudioBuffer<float>& audio,juce::MidiBuffer& midi)override{
        select();Fixture::processBlock(audio,midi);
        if(latency)for(int i=0;i<audio.getNumSamples();++i){
            for(int ch=0;ch<2;++ch){const float value=audio.getSample(ch,i);audio.setSample(ch,i,extra[(size_t)ch][(size_t)cursor]);extra[(size_t)ch][(size_t)cursor]=value;}
            cursor=(cursor+1)%128;
        }
    }
};
juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter(){return new DynamicFixture;}
