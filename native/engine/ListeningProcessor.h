#pragma once
#include <juce_dsp/juce_dsp.h>

namespace mlengine {
// One shared native monitoring/output stage. Track calibration is upstream;
// compression, 4x soft ceiling and the 1 dB reserve are applied exactly once.
class ListeningProcessor final {
    juce::dsp::Oversampling<float> oversampling{2,2,juce::dsp::Oversampling<float>::filterHalfBandPolyphaseIIR,true,true};
    double attack=0,release=0,reduction=0;
public:
    void prepare(double rate,int frames){
        attack=std::exp(-1.0/(rate*.001));release=std::exp(-1.0/(rate*.06));reduction=0;
        oversampling.initProcessing((size_t)frames);oversampling.reset();
    }
    int latency()const{return (int)std::lround(oversampling.getLatencyInSamples());}
    void process(juce::AudioBuffer<float>& buffer,int start,int count){
        for(int i=start;i<start+count;++i){
            const auto amplitude=std::max(std::abs(buffer.getSample(0,i)),std::abs(buffer.getSample(1,i)));
            const double db=20*std::log10(std::max(1e-12f,amplitude));
            const double requested=db>-1?(-1+(db+1)/20)-db:0;
            const double coefficient=requested<reduction?attack:release;
            reduction=coefficient*reduction+(1-coefficient)*requested;
            const float gain=(float)std::pow(10.0,reduction/20.0);
            for(int ch=0;ch<2;++ch)buffer.setSample(ch,i,buffer.getSample(ch,i)*gain);
        }
        juce::dsp::AudioBlock<float> whole(buffer);auto block=whole.getSubBlock((size_t)start,(size_t)count);
        auto high=oversampling.processSamplesUp(block);
        for(size_t ch=0;ch<high.getNumChannels();++ch)for(size_t i=0;i<high.getNumSamples();++i){
            const float x=high.getSample((int)ch,(int)i),a=std::abs(x);
            high.setSample((int)ch,(int)i,std::copysign(a<=.9f?a:.9f+.07f*(1-std::exp(-(a-.9f)/.07f)),x));
        }
        oversampling.processSamplesDown(block);
        for(int ch=0;ch<2;++ch)for(int i=start;i<start+count;++i)
            buffer.setSample(ch,i,juce::jlimit(-.891250938f,.891250938f,buffer.getSample(ch,i)*.891250938f));
    }
};
}
