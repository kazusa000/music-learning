#pragma once
#include "NodeSource.h"
#include "ListeningProcessor.h"
#include <juce_audio_formats/juce_audio_formats.h>
#include <map>
#include <set>
#include <deque>
#include <mutex>

namespace mlengine {
// A source-rate graph. Used by the background device producer and by the
// streaming export driver. No device/UI/Python API exists inside this kernel.
class GraphKernel final {
    using String=juce::String;using var=juce::var;using Buffer=juce::AudioBuffer<float>;
    struct Curve {
        String parameter;bool linear=false;std::vector<std::pair<int64_t,double>> points;
        double value(int64_t sample,double fallback)const{
            const auto next=std::upper_bound(points.begin(),points.end(),sample,[](int64_t s,const auto& p){return s<p.first;});
            if(next==points.begin())return fallback;
            const auto previous=std::prev(next);double result=previous->second;
            if(linear&&next!=points.end())result+=(next->second-result)*(double)(sample-previous->first)/(double)(next->first-previous->first);
            return result;
        }
    };
    struct Clip {std::unique_ptr<juce::AudioFormatReader> reader;int64_t start,offset,length;float gain;};
    struct Node {
        String id;var spec,program;
        std::unique_ptr<NodeSource> worker;
        Buffer main,raw,output,scratch;
        std::vector<Buffer> sides;
        std::vector<Clip> clips;
        std::vector<Curve> curves;
        int inputDelay=0,arrival=0;
        std::vector<int> pluginDelays;
        std::atomic<float> gain{0},pan{0},peak{0};
        std::atomic<bool> mute{false},solo{false};
        float monitorGain=0,soloGain=0,bakedGain=0,bakedPan=0;
        float left=0,right=0,targetLeft=0,targetRight=0;
        int ramp=0;
        bool mutedBySolo=false;
        size_t bufferBytes=0;
        uint64_t late=0,latencyChanges=0;bool recovering=false,submitted=false;
        std::atomic<bool> invalidLatency{false};bool isSource=false;
    };
    struct Edge {
        size_t source,target;int slot=-1,delay=0;float gain=1;
        String kind,pluginId;
        Buffer history;size_t cursor=0;
    };
    std::vector<std::unique_ptr<Node>> nodes;
    std::vector<Edge> edges;
    std::vector<std::vector<size_t>> levels;
    std::map<String,size_t> index;
    std::atomic<bool>* cancel=nullptr;
    int blockSize=512,totalDelay=0;
    double rate=44100;
    double processingDeadline=0;
    int64_t origin=0,processingSample=0,discard=0,loopStart=0,loopEnd=0;
    bool looping=false,first=true,strict=false;
    bool replanWanted=false;
    uint64_t replans=0;
    size_t master=0,allocated=0;
    ListeningProcessor protection;
    bool protectedOutput=false;
    var soloGains;
    static constexpr size_t budget=256ull*1024*1024;
    size_t limit=budget;
    int64_t sourceAt(int64_t sample)const{return looping&&sample>=loopEnd?loopStart+(sample-loopStart)%(loopEnd-loopStart):sample;}
    void account(size_t amount){if(amount>limit-allocated)throw std::runtime_error("Graph exceeds available audio-buffer budget");allocated+=amount;}
    void addLocal(Node& node,int64_t at,int count){
        for(int done=0;done<count;){
            auto raw=at+done;int n=count-done;
            if(raw<origin){n=(int)std::min<int64_t>(n,origin-raw);done+=n;continue;}
            const auto sample=sourceAt(raw);
            if(looping)n=(int)std::min<int64_t>(n,loopEnd-sample);
            for(auto& clip:node.clips){
                const auto begin=std::max(sample,clip.start),end=std::min(sample+n,clip.start+clip.length);
                if(end<=begin)continue;const int length=(int)(end-begin);node.scratch.clear();
                if(!clip.reader->read(&node.scratch,0,length,clip.offset+begin-clip.start,true,true))throw std::runtime_error("Graph audio read failed");
                for(int ch=0;ch<2;++ch)node.main.addFrom(ch,done+(int)(begin-sample),node.scratch,ch,0,length,clip.gain);
            }
            done+=n;
        }
    }
    void feed(Edge& edge,int count){
        const auto& source=nodes[edge.source]->output;auto& target=*nodes[edge.target];
        auto& output=edge.slot<0?target.main:target.sides[(size_t)edge.slot];
        for(int i=0;i<count;++i){
            for(int ch=0;ch<2;++ch){
                const auto sample=source.getSample(ch,i)*edge.gain;
                const auto delayed=edge.delay?edge.history.getSample(ch,(int)edge.cursor):sample;
                if(edge.delay)edge.history.setSample(ch,(int)edge.cursor,sample);
                output.addSample(ch,i,delayed);
            }
            if(edge.delay)edge.cursor=(edge.cursor+1)%(size_t)edge.delay;
        }
    }
    void mix(Node& node,int count,int audible){
        const bool muted=node.mute.load()||node.mutedBySolo;
        const double gain=node.gain.load(),pan=node.pan.load();
        float maximum=0;
        for(int i=0;i<count;++i){
            const auto sample=sourceAt(processingSample+i-node.arrival);
            double g=gain,p=pan;
            for(const auto& curve:node.curves){if(curve.parameter=="gain_db")g=curve.value(sample,g);else p=curve.value(sample,p);}
            const auto level=muted?0.0f:(float)std::pow(10.0,(g-node.bakedGain+(audible==1?node.soloGain:node.monitorGain))/20.0);
            const auto left=level*(p>0?1-(float)p:1)/juce::jmax(.0001f,node.bakedPan>0?1-node.bakedPan:1);
            const auto right=level*(p<0?1+(float)p:1)/juce::jmax(.0001f,node.bakedPan<0?1+node.bakedPan:1);
            if(first||!node.curves.empty()) {node.left=left;node.right=right;node.ramp=0;}
            else if(left!=node.targetLeft||right!=node.targetRight){node.targetLeft=left;node.targetRight=right;node.ramp=juce::jmax(1,(int)std::ceil(rate*.003));}
            if(node.ramp){node.left+=(left-node.left)/(float)node.ramp;node.right+=(right-node.right)/(float)node.ramp;--node.ramp;}
            else{node.left=left;node.right=right;}
            const float l=node.raw.getSample(0,i)*node.left,r=node.raw.getSample(1,i)*node.right;
            if(!std::isfinite(l)||!std::isfinite(r))throw std::runtime_error("Non-finite graph output");
            node.output.setSample(0,i,l);node.output.setSample(1,i,r);maximum=std::max({maximum,std::abs(l),std::abs(r)});
        }
        node.peak.store(maximum);
    }
    static std::vector<Curve> readCurves(const var& spec){
        std::vector<Curve> result;
        if(auto* curves=spec["mix_automation"].getArray())for(const auto& item:*curves){
                Curve curve;curve.parameter=item["parameter_id"].toString();curve.linear=item["interpolation"].toString()=="linear";
                if(curve.parameter!="gain_db"&&curve.parameter!="pan")throw std::runtime_error("Unknown graph mix curve");
                if(auto* points=item["points"].getArray())for(const auto& point:*points){
                    const auto sample=(int64_t)(juce::int64)point["sample"];const auto value=(double)point["value"];
                    if(!std::isfinite(value)||sample<0||(!curve.points.empty()&&sample<=curve.points.back().first))throw std::runtime_error("Invalid graph mix curve");
                    curve.points.emplace_back(sample,value);
                }
                if(curve.points.empty())throw std::runtime_error("Empty graph mix curve");result.push_back(std::move(curve));
            }
        return result;
    }
    static std::unique_ptr<Node> makeNode(const var& spec,double rate,int blockSize){
        juce::AudioFormatManager formats;formats.registerBasicFormats();
            auto node=std::make_unique<Node>();node->id=spec["id"].toString();node->spec=spec;
            if(node->id.isEmpty())throw std::runtime_error("Duplicate graph node");
            node->gain.store((float)(double)spec.getProperty("gain_db",0));node->pan.store((float)(double)spec.getProperty("pan",0));
            node->mute.store((bool)spec.getProperty("mute",false));node->solo.store((bool)spec.getProperty("solo",false));
            node->monitorGain=(float)(double)spec.getProperty("monitor_gain_db",0);node->soloGain=(float)(double)spec.getProperty("solo_gain_db",node->monitorGain);
            node->bakedGain=(float)(double)spec.getProperty("baked_gain_db",0);node->bakedPan=(float)(double)spec.getProperty("baked_pan",0);
            auto buffer=[&](Buffer& value){node->bufferBytes+=(size_t)blockSize*2*sizeof(float);value.setSize(2,blockSize);value.clear();};
            buffer(node->main);buffer(node->raw);buffer(node->output);buffer(node->scratch);
            if(spec.hasProperty("plugin_project_path")){
                const juce::File programFile(spec["plugin_project_path"].toString());
                if(!programFile.existsAsFile()||programFile.getSize()>8*1024*1024||programFile.getFileName()!=juce::SHA256(programFile).toHexString()+".json")throw std::runtime_error("Graph program identity mismatch");
                node->program=juce::JSON::parse(programFile.loadFileAsString());
                if((double)node->program["sample_rate"]!=rate||(int)node->program["block_size"]!=blockSize)throw std::runtime_error("Graph node clock mismatch");
                const auto* values=node->program["tracks"].getArray();if(!values||values->size()!=1)throw std::runtime_error("Graph node must have one track");
                const auto track=(*values)[0];const auto* plugins=track["plugins"].getArray();
                const auto clipCount=track["audio_clips"].isArray()?track["audio_clips"].getArray()->size():0;
                if(spec.hasProperty("audio_clip_count")&&(int)spec["audio_clip_count"]!=clipCount)throw std::runtime_error("Graph audio clip count differs from prepared metadata");
                // JUCE readers own decoder/format buffers in addition to our
                // planar PCM. Reserve a conservative MiB per open reader.
                node->bufferBytes+=(size_t)clipCount*1024*1024;
                node->isSource=(track["notes"].isArray()&&track["notes"].getArray()->size()>0)
                    ||(track["audio_clips"].isArray()&&track["audio_clips"].getArray()->size()>0);
                const int slots=plugins?plugins->size():0;
                if(spec.hasProperty("plugin_count")&&(int)spec["plugin_count"]!=slots)throw std::runtime_error("Graph plugin count differs from prepared metadata");
                for(int p=0;p<slots;++p){node->sides.emplace_back(2,blockSize);node->bufferBytes+=(size_t)blockSize*2*sizeof(float);}
                if(slots){node->worker=std::make_unique<NodeSource>(programFile,rate,blockSize,slots);node->bufferBytes+=node->worker->bytes();}
                else if(auto* clips=track["audio_clips"].getArray())for(const auto& clip:*clips){
                    auto input=std::make_unique<juce::FileInputStream>(juce::File(clip["path"].toString()));
                    auto reader=std::unique_ptr<juce::AudioFormatReader>(formats.createReaderFor(std::move(input)));
                    const auto offset=(int64_t)(juce::int64)clip.getProperty("source_offset_samples",(juce::int64)0),length=(int64_t)(juce::int64)clip["duration_samples"];
                    if(!reader||reader->sampleRate!=rate||reader->numChannels>2||offset<0||length<0||offset+length>reader->lengthInSamples)throw std::runtime_error("Graph clip source mismatch");
                    node->clips.push_back({std::move(reader),(int64_t)(juce::int64)clip["sample"],offset,length,(float)(double)clip.getProperty("gain",1.0)});
                }
            }
            node->curves=readCurves(spec);
        return node;
    }
    void soloPaths(){
        std::set<size_t> audible;bool any=false;
        for(size_t i=0;i<nodes.size();++i)if(nodes[i]->solo.load()){any=true;audible.insert(i);}
        if(any){
            auto expand=[&](bool upstream){size_t old;
                do{old=audible.size();for(const auto& edge:edges)
                    if(upstream?edge.target!=master&&audible.count(edge.target):audible.count(edge.source))
                        audible.insert(upstream?edge.source:edge.target);
                }while(old!=audible.size());};
            expand(true);expand(false);
        }
        for(size_t i=0;i<nodes.size();++i)nodes[i]->mutedBySolo=any&&!audible.count(i);
    }
    void plan(bool preserve=false){
        totalDelay=0;allocated=0;account(protection.bufferBytesBound());for(const auto& node:nodes)account(node->bufferBytes);
        for(const auto& level:levels)for(auto i:level){
            auto& node=*nodes[i];node.pluginDelays=node.worker?node.worker->pluginLatencies():std::vector<int>{};
            int input=0;
            for(const auto& edge:edges)if(edge.target==i){
                int preceding=0;if(edge.slot>=0)for(int p=0;p<edge.slot;++p)preceding+=node.pluginDelays[(size_t)p];
                input=std::max(input,nodes[edge.source]->arrival-preceding);
            }
            node.inputDelay=input;node.arrival=input+(node.worker?node.worker->latency():0);
            if(node.arrival<0||node.arrival>rate*30)throw std::runtime_error("Graph delay exceeds 30 seconds");
        }
        for(auto& edge:edges){
            auto& target=*nodes[edge.target];int preceding=0;
            if(edge.slot>=0)for(int p=0;p<edge.slot;++p)preceding+=target.pluginDelays[(size_t)p];
            const int delay=target.inputDelay+preceding-nodes[edge.source]->arrival;
            if(delay<0)throw std::runtime_error("Negative graph edge delay");
            account((size_t)delay*2*sizeof(float));
            if(!preserve||delay!=edge.delay){edge.history.setSize(2,delay);edge.history.clear();edge.cursor=0;}edge.delay=delay;
        }
        totalDelay=nodes[master]->arrival+(protectedOutput?protection.latency():0);discard=totalDelay;
    }
public:
    struct PreparedUpdate {
        var manifest,previous;
        std::vector<bool> force;
        std::vector<std::unique_ptr<Node>> replacements;
        std::atomic<bool> ready{false},applied{false},cancelled{false},failed{false};
        std::atomic<size_t> audioBytes{0};
        mutable std::mutex mutex;
        var diagnostics;
        juce::StringArray retained;
        String error;
        var states()const{std::lock_guard lock(mutex);return diagnostics;}
        String failure()const{std::lock_guard lock(mutex);return error;}
        juce::StringArray programs()const{std::lock_guard lock(mutex);return retained;}
    };
    static bool changesResources(const var& old,const var& next){
        const auto& a=*old["tracks"].getArray();const auto& b=*next["tracks"].getArray();
        for(int i=0;i<a.size();++i)if(a[i]["runtime_signature"]!=b[i]["runtime_signature"])return true;return false;
    }
    static void prepare(const std::shared_ptr<PreparedUpdate>& plan,size_t available){
        struct Reclaim{std::shared_ptr<PreparedUpdate> value;~Reclaim(){value->replacements.clear();value->audioBytes.store(0);std::lock_guard lock(value->mutex);value->retained.clear();}} reclaim{plan};
        try{
            const auto& old=*plan->previous["tracks"].getArray();const auto& tracks=*plan->manifest["tracks"].getArray();
            plan->replacements.resize((size_t)tracks.size());
            for(int i=0;i<tracks.size();++i){
                if(plan->cancelled.load())return;
                if(old[i]["runtime_signature"]==tracks[i]["runtime_signature"]&&(plan->force.empty()||!plan->force[(size_t)i]))continue;
                // Count the maximum mapping, planar inputs/outputs/scratch and
                // side inputs before allocating or starting a new child.
                const auto plugins=(uint32_t)(int)tracks[i].getProperty("plugin_count",0);
                if(plugins>nodeMaxPlugins)throw std::runtime_error("Invalid staged plugin count");
                const auto clips=(uint32_t)(int)tracks[i].getProperty("audio_clip_count",0);
                const size_t bytes=(size_t)(4+plugins)*512*2*sizeof(float)+(plugins?NodeSource::bytesBound(512,plugins):0)+(size_t)clips*1024*1024;
                if(bytes>available-plan->audioBytes.load())throw std::runtime_error("Graph staging exceeds the audio-buffer budget");
                plan->audioBytes.fetch_add(bytes);
                plan->replacements[(size_t)i]=makeNode(tracks[i],(double)plan->manifest["sample_rate"],512);
            }
            for(;;){
                if(plan->cancelled.load())return;
                bool ready=true;auto* values=new juce::DynamicObject();
                for(const auto& node:plan->replacements)if(node&&node->worker){
                    const auto state=node->worker->status();values->setProperty(node->id,state);
                    if(state["status"].toString()=="failed")throw std::runtime_error(state["error"].toString().toStdString());
                    if(state["status"].toString()!="ready")ready=false;
                }
                {std::lock_guard lock(plan->mutex);plan->diagnostics=var(values);}
                if(ready)break;juce::Thread::sleep(2);
            }
            plan->ready.store(true,std::memory_order_release);
            while(!plan->applied.load(std::memory_order_acquire)&&!plan->cancelled.load())juce::Thread::sleep(1);
            // The old node/job is reclaimed here, away from the producer and
            // device callback. Its PCM already queued in the graph is owned.
        }catch(const std::exception& e){std::lock_guard lock(plan->mutex);plan->error=e.what();plan->failed.store(true);}
    }
    std::vector<bool> forceReplacements(){
        std::vector<bool> values;for(const auto& node:nodes)values.push_back(node->worker&&(node->worker->isFailed()||(node->recovering&&node->worker->isPending())));return values;
    }
    bool requiresReplacement(const var& current,const var& next){
        if(changesResources(current,next))return true;for(bool failed:forceReplacements())if(failed)return true;return false;
    }
    void install(const std::shared_ptr<PreparedUpdate>& plan){
        if(!plan->ready.load(std::memory_order_acquire)||plan->cancelled.load())throw std::runtime_error("Graph replacement is not prepared");
        std::vector<int> sideSlots;
        for(const auto& edge:edges){
            int slot=edge.slot;
            if(edge.slot>=0&&plan->replacements[edge.target]){
                const auto& target=*plan->replacements[edge.target];slot=-1;
                if(auto* tracks=target.program["tracks"].getArray())if(auto* plugins=(*tracks)[0]["plugins"].getArray())
                    for(int p=0;p<plugins->size();++p)if((*plugins)[p]["id"].toString()==edge.pluginId)slot=p;
                if(slot<0)throw std::runtime_error("Replacement removed an active sidechain target");
            }sideSlots.push_back(slot);
        }
        size_t retiredBytes=0;juce::StringArray retiredPrograms;
        for(size_t i=0;i<nodes.size();++i)if(plan->replacements[i]){
            auto& old=*nodes[i];auto& next=*plan->replacements[i];
            next.gain.store(old.gain.load());next.pan.store(old.pan.load());next.mute.store(old.mute.load());next.solo.store(old.solo.load());
            next.left=old.left;next.right=old.right;next.targetLeft=old.targetLeft;next.targetRight=old.targetRight;next.ramp=old.ramp;
            next.inputDelay=old.inputDelay;next.arrival=old.arrival;next.recovering=true;
            const auto total=allocated-old.bufferBytes+next.bufferBytes;
            if(total>limit)throw std::runtime_error("Replacement graph exceeds audio-buffer budget");allocated=total;
            if(old.worker)retiredPrograms.add(old.worker->programPath());retiredBytes+=old.bufferBytes;
            nodes[i].swap(plan->replacements[i]);
        }
        for(size_t i=0;i<edges.size();++i)edges[i].slot=sideSlots[i];
        plan->audioBytes.store(retiredBytes);std::lock_guard lock(plan->mutex);plan->retained=retiredPrograms;
    }
    ~GraphKernel(){for(auto& node:nodes)if(node->worker)node->worker->requestClose();}
    GraphKernel(const var& manifest,int64_t start,bool loop,int64_t begin,int64_t end,bool offline=false,std::atomic<bool>* stopping=nullptr,size_t available=budget)
        :cancel(stopping),rate((double)manifest["sample_rate"]),origin(start),processingSample(start),loopStart(begin),loopEnd(end),looping(loop),strict(offline){
        if(!(rate>=8000&&rate<=192000)||start<0||(loop&&(begin<0||end<=begin)))throw std::runtime_error("Invalid graph source clock");
        limit=std::min(available,budget);
        const auto graph=manifest["routing_graph"];
        protectedOutput=(bool)manifest.getProperty("monitor_protection",false);
        if(protectedOutput)protection.prepare(rate,blockSize);
        soloGains=manifest["solo_gain_db"];
        const auto* tracks=manifest["tracks"].getArray();if(!tracks||tracks->isEmpty()||tracks->size()>128)throw std::runtime_error("Invalid graph nodes");
        account(protection.bufferBytesBound());
        for(const auto& spec:*tracks){
            if(cancel&&cancel->load())throw std::runtime_error("Graph preparation cancelled");
            int plugins=(int)spec.getProperty("plugin_count",0),clips=(int)spec.getProperty("audio_clip_count",0);
            if(spec.hasProperty("plugin_project_path")&&(!spec.hasProperty("plugin_count")||!spec.hasProperty("audio_clip_count"))){
                const auto program=juce::JSON::parse(juce::File(spec["plugin_project_path"].toString()).loadFileAsString());
                if(auto* nodes=program["tracks"].getArray())if(nodes->size()==1){
                    if(auto* values=(*nodes)[0]["plugins"].getArray())plugins=values->size();
                    if(auto* values=(*nodes)[0]["audio_clips"].getArray())clips=values->size();
                }
            }
            if(plugins<0||plugins>(int)nodeMaxPlugins||clips<0)throw std::runtime_error("Invalid graph buffer dimensions");
            const auto estimate=(size_t)(4+plugins)*blockSize*2*sizeof(float)+(plugins?NodeSource::bytesBound((uint32_t)blockSize,(uint32_t)plugins):0)+(size_t)clips*1024*1024;
            if(estimate>limit-allocated)throw std::runtime_error("Graph readers and workers exceed available audio-buffer budget");
            auto node=makeNode(spec,rate,blockSize);
            if(index.count(node->id))throw std::runtime_error("Duplicate graph node");account(node->bufferBytes);
            index[node->id]=nodes.size();nodes.push_back(std::move(node));
        }
        const auto masterId=graph["master"].toString();if(!index.count(masterId))throw std::runtime_error("Graph master missing");master=index.at(masterId);
        std::vector<int> indegree(nodes.size(),0);
        if(auto* routes=graph["edges"].getArray())for(const auto& spec:*routes){
            const auto source=spec["source"].toString(),target=spec["target"].toString(),kind=spec["kind"].toString();
            if(!index.count(source)||!index.count(target)||source==target||source==masterId||(kind!="main"&&kind!="send"&&kind!="sidechain"))throw std::runtime_error("Invalid graph edge");
            Edge edge;edge.source=index.at(source);edge.target=index.at(target);edge.kind=kind;const auto db=(double)spec.getProperty("gain_db",0.0);
            if(!std::isfinite(db)||db<-120||db>24)throw std::runtime_error("Invalid route gain");edge.gain=(float)std::pow(10.0,db/20.0);
            if(kind=="sidechain"){
                edge.pluginId=spec["plugin_id"].toString();
                if(!nodes[edge.target]->worker)throw std::runtime_error("Sidechain target has no plugin worker");
                const auto track=(*nodes[edge.target]->program["tracks"].getArray())[0];const auto* plugins=track["plugins"].getArray();
                if(plugins)for(int p=0;p<plugins->size();++p)if((*plugins)[p]["id"]==spec["plugin_id"])edge.slot=p;
                if(edge.slot<0)throw std::runtime_error("Sidechain plugin missing");
            }
            for(const auto& old:edges)if(old.source==edge.source&&old.target==edge.target&&old.slot==edge.slot&&old.kind==edge.kind)throw std::runtime_error("Duplicate graph edge");
            ++indegree[edge.target];edges.push_back(std::move(edge));
        }
        size_t visited=0;std::vector<size_t> frontier;
        for(size_t i=0;i<nodes.size();++i)if(!indegree[i])frontier.push_back(i);
        while(!frontier.empty()){
            levels.push_back(frontier);visited+=frontier.size();std::vector<size_t> next;
            for(auto i:frontier)for(const auto& edge:edges)if(edge.source==i&&--indegree[edge.target]==0)next.push_back(edge.target);
            frontier=std::move(next);
        }
        if(visited!=nodes.size())throw std::runtime_error("Graph contains a cycle");
        // Workers start concurrently above. Polling here belongs only to the
        // preparation/producer path, never to the audio device callback.
        for(auto& node:nodes)if(node->worker){
            try{node->worker->waitReady(120000,cancel);}catch(...){if(strict||(cancel&&cancel->load()))throw;}
        }
        plan();
    }
    int latency()const{return totalDelay;}
    size_t bytes()const{return allocated;}
    int quantum()const{return blockSize;}
    void setProcessingDeadline(double deadline){processingDeadline=deadline;}
    int64_t sample()const{return processingSample-totalDelay;}
    // One bounded source-rate quantum. The output is still in processing time;
    // the caller discards the initial latency before presenting source time.
    void process(Buffer& output,int count){
        if(count<=0||count>blockSize)throw std::runtime_error("Invalid graph quantum");
        soloPaths();int audible=0;String onlySource;std::set<size_t> heard{master};size_t previous;
        do{previous=heard.size();for(const auto& edge:edges)if(edge.slot<0&&heard.count(edge.target)
            &&!nodes[edge.target]->mute.load()&&!nodes[edge.target]->mutedBySolo)heard.insert(edge.source);
        }while(previous!=heard.size());
        for(size_t i=0;i<nodes.size();++i)if(i!=master&&heard.count(i)&&!nodes[i]->mute.load()&&!nodes[i]->mutedBySolo&&nodes[i]->isSource){++audible;onlySource=nodes[i]->id;}
        nodes[master]->soloGain=audible==1?(float)(double)soloGains.getProperty(onlySource,nodes[master]->monitorGain):nodes[master]->monitorGain;
        for(const auto& level:levels){
            for(auto i:level){
                auto& node=*nodes[i];node.submitted=false;node.main.clear();node.raw.clear();node.output.clear();for(auto& side:node.sides)side.clear();
                for(auto& edge:edges)if(edge.target==i)feed(edge,count);
                if(!node.worker){addLocal(node,processingSample-node.inputDelay,count);for(int ch=0;ch<2;++ch)node.raw.copyFrom(ch,0,node.main,ch,0,count);continue;}
                try{
                    if(node.invalidLatency.load()){if(strict)throw std::runtime_error("Graph latency changed");continue;}
                    if(node.worker->isPending()){
                        if(!node.worker->receive(node.raw,node.worker->pendingFrames(),0)){++node.late;node.recovering=true;continue;}
                        node.raw.clear();node.recovering=true;
                    }
                    if(node.worker->isFailed()){if(strict)throw std::runtime_error("Graph worker failed");continue;}
                    node.worker->submit(processingSample,node.inputDelay,origin,first||node.recovering,looping,loopStart,loopEnd,node.main,node.sides,count);
                    node.recovering=false;node.submitted=true;
                }catch(...){if(strict)throw;node.raw.clear();node.recovering=true;}
            }
            // First/chased quantum is preparation on the producer thread. A
            // cold plugin can initialise DSP here after reporting state ready;
            // it must not be replaced with silence by the live block deadline.
            const auto deadline=(strict||first)?juce::Time::getMillisecondCounterHiRes()+2000.0:
                processingDeadline>0?processingDeadline:juce::Time::getMillisecondCounterHiRes()+std::max(2.0,count/rate*1000.0*.8);
            for(auto i:level){
                auto& node=*nodes[i];
                if(node.worker&&node.submitted&&node.worker->isPending()){
                    bool done=false;
                    try{
                        do{if(cancel&&cancel->load())throw std::runtime_error("Graph cancelled");
                            done=node.worker->receive(node.raw,count,1);
                        }while(!done&&juce::Time::getMillisecondCounterHiRes()<deadline);
                        if(!done){++node.late;node.raw.clear();node.recovering=true;if(strict)throw std::runtime_error("Graph deadline exceeded");}
                        if(done&&node.worker->latency()!=node.arrival-node.inputDelay){
                            node.invalidLatency.store(true);replanWanted=true;
                            ++node.latencyChanges;
                            if(strict)throw std::runtime_error("Plugin changed latency during fixed export");
                            node.raw.clear();node.recovering=true;
                        }
                    }catch(...){node.raw.clear();node.recovering=true;if(strict)throw;}
                }
                mix(node,count,audible);
            }
        }
        for(int ch=0;ch<2;++ch)output.copyFrom(ch,0,nodes[master]->output,ch,0,count);
        if(protectedOutput)protection.process(output,0,count);
        first=false;processingSample+=count;
    }
    int consumeDiscard(int count){const auto n=(int)std::min<int64_t>(discard,count);discard-=n;return n;}
    bool needsReplan()const{return replanWanted;}
    uint64_t replanCount()const{return replans;}
    bool replan(int64_t nextSource){
        const auto old=totalDelay;plan(true);const bool moved=old!=totalDelay;
        for(auto& node:nodes)node->invalidLatency.store(false);
        if(moved)seek(nextSource,looping,loopStart,loopEnd);
        else discard=0;
        replanWanted=false;++replans;return moved;
    }
    void seek(int64_t start,bool loop,int64_t begin,int64_t end){
        if(start<0||(loop&&(begin<0||end<=begin)))throw std::runtime_error("Invalid graph seek");
        for(auto& node:nodes)if(node->worker&&node->worker->isPending()){
            // A hung path cannot hold a graph-wide seek barrier. It retains
            // input ownership and is discarded/recovered by the normal path.
            try{node->worker->receive(node->raw,node->worker->pendingFrames(),0);}catch(...){if(strict)throw;}
        }
        origin=processingSample=start;looping=loop;loopStart=begin;loopEnd=end;discard=totalDelay;first=true;
        for(auto& edge:edges){edge.history.clear();edge.cursor=0;}
        if(protectedOutput)protection.prepare(rate,blockSize);
        for(auto& node:nodes){node->recovering=node->worker&&node->worker->isPending();node->raw.clear();node->output.clear();}
    }
    juce::var monitorValues()const{
        auto* values=new juce::DynamicObject();for(size_t i=0;i<nodes.size();++i)if(i!=master){const auto& node=*nodes[i];auto* v=new juce::DynamicObject();
            v->setProperty("gain_db",node.gain.load());v->setProperty("pan",node.pan.load());v->setProperty("mute",node.mute.load());v->setProperty("solo",node.solo.load());values->setProperty(node.id,var(v));}
        return var(values);
    }
    void monitor(const var& values){
        if(!values.isObject())throw std::runtime_error("Invalid graph monitor map");
        for(const auto& field:values.getDynamicObject()->getProperties())if(!index.count(field.name.toString())||index.at(field.name.toString())==master)throw std::runtime_error("Unknown monitor track");
        for(const auto& field:values.getDynamicObject()->getProperties()){
            auto& node=*nodes[index.at(field.name.toString())];const auto& value=field.value;
            if(value.hasProperty("gain_db"))node.gain.store((float)(double)value["gain_db"]);
            if(value.hasProperty("pan"))node.pan.store((float)(double)value["pan"]);
            if(value.hasProperty("mute"))node.mute.store((bool)value["mute"]);
            if(value.hasProperty("solo"))node.solo.store((bool)value["solo"]);
        }
    }
    var meterValues()const{auto* values=new juce::DynamicObject();for(size_t i=0;i<nodes.size();++i)if(i!=master)values->setProperty(nodes[i]->id,nodes[i]->peak.load());return var(values);}
    var pluginValues(bool& ready){
        ready=true;auto* values=new juce::DynamicObject();for(const auto& node:nodes)if(node->worker){
            auto state=node->worker->status();state.getDynamicObject()->setProperty("missed_blocks",(juce::int64)node->late);
            if(node->invalidLatency.load())state.getDynamicObject()->setProperty("status","loading");
            state.getDynamicObject()->setProperty("latency_replans",(juce::int64)replans);
            state.getDynamicObject()->setProperty("latency_change_blocks",(juce::int64)node->latencyChanges);
            if(state["status"].toString()!="ready"||node->recovering)ready=false;values->setProperty(node->id,state);
        }return var(values);
    }
    juce::StringArray programs()const{juce::StringArray values;for(const auto& node:nodes)if(node->worker)values.add(node->worker->programPath());return values;}
    bool compatible(const var& manifest)const{
        const auto* tracks=manifest["tracks"].getArray();
        if(!tracks||tracks->size()!=(int)nodes.size())return false;
        for(int i=0;i<tracks->size();++i){const auto& next=(*tracks)[i];const auto& old=nodes[(size_t)i]->spec;
            if(next["id"]!=old["id"]||next["runtime_signature"]!=old["runtime_signature"]
               ||next.hasProperty("plugin_project_path")!=old.hasProperty("plugin_project_path"))return false;
        }return true;
    }
    // Producer thread at a completed quantum boundary. Verify every program
    // before publishing any new epoch. A failed DSP update is never reported
    // as applied merely because its musical revision was committed.
    void update(const var& manifest){
        if(!compatible(manifest))throw std::runtime_error("Graph structure requires a new plan");
        const auto& tracks=*manifest["tracks"].getArray();std::vector<juce::File> changed(nodes.size());
        std::vector<std::vector<Curve>> curves;for(const auto& spec:tracks)curves.push_back(readCurves(spec));
        for(size_t i=0;i<nodes.size();++i)if(nodes[i]->worker){
            const auto file=juce::File(tracks[(int)i]["plugin_project_path"].toString());
            if(!file.existsAsFile()||file.getSize()>8*1024*1024||file.getFileName()!=juce::SHA256(file).toHexString()+".json")throw std::runtime_error("Graph update identity mismatch");
            if(file.getParentDirectory()!=juce::File(nodes[i]->worker->programPath()).getParentDirectory())throw std::runtime_error("Graph update escaped its node workspace");
            changed[i]=file;
        }
        for(size_t i=0;i<nodes.size();++i)if(nodes[i]->worker&&changed[i].getFileNameWithoutExtension()!=nodes[i]->worker->hash()){
            if(nodes[i]->worker->isPending()){
                const auto until=juce::Time::getMillisecondCounterHiRes()+2100;
                while(!nodes[i]->worker->receive(nodes[i]->raw,nodes[i]->worker->pendingFrames(),1))
                    if(juce::Time::getMillisecondCounterHiRes()>until)throw std::runtime_error("Pending graph node did not release its input");
            }
            nodes[i]->worker->configure(changed[i]);
        }
        for(auto& node:nodes)if(node->worker)node->worker->waitReady(120000,cancel);
        for(size_t i=0;i<nodes.size();++i){
            if(nodes[i]->worker&&(nodes[i]->worker->latency()!=nodes[i]->arrival-nodes[i]->inputDelay||nodes[i]->worker->pluginLatencies()!=nodes[i]->pluginDelays)){
                nodes[i]->invalidLatency.store(true);replanWanted=true;
            }
            nodes[i]->spec=tracks[(int)i];nodes[i]->curves=std::move(curves[i]);
        }
    }
};
}
