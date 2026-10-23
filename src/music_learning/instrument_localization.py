"""Chinese presentation metadata, separate from immutable factory identities."""

from __future__ import annotations

import re


def table(text):
    return dict(line.split("|", 1) for line in text.strip().splitlines())


MODELS = table("""
LA Custom C7 Grand Piano|洛杉矶定制 C7 三角钢琴
Celeste|钢片琴
Chimeatron|键盘钟琴
Clavichord|击弦古钢琴
Dolceola|多尔切奥拉键盘弦琴
Dulcitone|杜尔西通音叉键盘琴
Duo Combination|双乐器叠层组合
Electric Harpsichord|电拨弦古钢琴
Harmochord|哈莫科德簧风琴
Hohner Clavinet C|霍纳 C 型击弦电古钢琴
Hohner Clavinet-Pianet DUO|霍纳击弦电古钢琴与电钢琴双琴
Hohner Pianet M|霍纳 M 型电钢琴
Hohner Pianet N|霍纳 N 型电钢琴
Hohner Pianet T|霍纳 T 型电钢琴
JD-800 Crystal Rhodes|JD-800 水晶电钢琴
MK-80 Digital Rhodes|MK-80 数字电钢琴
MKS-20 Digital Piano|MKS-20 数字钢琴
Mini Student Butterfly Piano|迷你学生蝴蝶钢琴
Rhodes - Classic Mark I|罗兹经典一代电钢琴
Rhodes - LA Custom "E"|罗兹洛杉矶定制 E 型电钢琴
Rhodes 1946 Pre-Piano|罗兹 1946 早期电钢琴
Rhodes Piano Bass|罗兹键盘贝斯
Toy Piano - Classic|经典玩具钢琴
Toy Piano - Glock|钟音玩具钢琴
Toy Piano - Grand|三角玩具钢琴
Toy Piano - Saucer Bell|碟铃玩具钢琴
Vintage Vibe Electric Piano|复古律动电钢琴
Vintage Vibe Tine Bass|复古律动簧片键盘贝斯
Vintage Vibe Vibanet|复古律动击弦电古钢琴
Weltmeister Basset|威尔特迈斯特键盘贝斯
Weltmeister Claviset|威尔特迈斯特电钢琴
Wing Tack Piano|温氏图钉钢琴
Wing Upright Piano|温氏立式钢琴
Wurlitzer 140B|沃尔里策 140B 电钢琴
Wurlitzer 200A|沃尔里策 200A 电钢琴
Yamaha CP-70|雅马哈 CP-70 电三角钢琴
Grand C. Bechstein DG|贝希斯坦 DG 三角钢琴
Grand Blüthner|布吕特纳三角钢琴
Grand Bösendorfer 280VC|贝森朵夫 280VC 三角钢琴
Grand Grotrian|格罗特里安三角钢琴
Grand K2|K2 三角钢琴
Grand Shigeru Kawai SK-EX|卡瓦依 SK-EX 三角钢琴
Grand Ant. Petrof|安东宁·佩卓夫三角钢琴
Grand Petrof Mistral|佩卓夫米斯特拉尔三角钢琴
Grand Steingraeber|斯坦格列泊三角钢琴
Grand Steinway B|施坦威 B 三角钢琴
Grand Steinway D (Hamburg)|施坦威 D 三角钢琴（汉堡）
Grand Steinway D (New York)|施坦威 D 三角钢琴（纽约）
Grand YC5|YC5 三角钢琴
Upright U4|U4 立式钢琴
Vintage Tines MKI|一代簧片电钢琴
Vintage Tines MKII|二代簧片电钢琴
Vintage Reeds|复古簧舌电钢琴
Classical Guitar|古典吉他
Clavinet D6|D6 击弦电古钢琴
Celtic Harp|凯尔特竖琴
Concert Harp|音乐会竖琴
Triple Harp|三排弦竖琴
H. Ruckers II Harpsichord|鲁克斯二世拨弦古钢琴
G. Giusti Harpsichord (1680)|朱斯蒂拨弦古钢琴（1680）
J. Salodiensis Virginal (1600)|萨洛迪恩西斯维吉纳琴（1600）
J. Weimes Pianoforte (1808)|魏梅斯古钢琴（1808）
NY Steinway Square (1858)|纽约施坦威方形钢琴（1858）
Ph. Schmidt Square (1780)|施密特方形钢琴（1780）
I. Besendorfer (1829)|贝森朵夫古钢琴（1829）
J. Dohnal (1795)|多纳尔古钢琴（1795）
J.B. Streicher (1852)|施特赖歇尔古钢琴（1852）
S. Erard (1849)|埃拉尔古钢琴（1849）
C. Bechstein (1899)|贝希斯坦古钢琴（1899）
I. Pleyel (1835)|普莱耶尔古钢琴（1835）
J. Broadwood (1796)|布罗德伍德古钢琴（1796）
J. Frenzel (1841)|弗伦策尔古钢琴（1841）
Electra-Piano|电子钢琴
Pianet N|N 型电钢琴
Pianet T|T 型电钢琴
Celesta|钢片琴
Glockenspiel|钟琴
Kalimba|拇指琴
Toy Piano|玩具钢琴
Vibraphone V-B|V-B 颤音琴
Vibraphone V-M|V-M 颤音琴
Marimba|马林巴
Xylophone|木琴
Hand Pan|手碟
Spacedrum|太空鼓
Steel Drum|钢鼓
Tank Drum|舌鼓
Syngular|奇点物理建模合成器
Surge XT|涌浪 XT 合成器
""")

PHRASES = table("""
Natural|自然
Original|原始
Basic|基础
Amped|音箱效果
Binaural|双耳
a la piano|钢琴式演奏
Classical Guitar 12 Strings|十二弦古典吉他
Classical Guitar|古典吉他
NY Steinway D|纽约施坦威 D
C. Bechstein DG|贝希斯坦 DG
Bass & Piano split|贝斯与钢琴分区
H. Ruckers II Harpsichord|鲁克斯二世拨弦古钢琴
Clavinet D6|D6 击弦电古钢琴
Vibraphone V-B|V-B 颤音琴
Celtic Harp|凯尔特竖琴
Hand Pan|手碟
dampers, hand played|带制音器·手奏
Syngular|奇点
Flash of Wisdom|智慧闪光
Deserted Caravan|荒野商队
Ancient Tree|古树
Helmholtz Drops|亥姆霍兹水滴
MKI|一代
W1|W1
Clavinet-Pianet DUO|击弦电古钢琴与电钢琴双琴
Mini Student Butterfly Piano|迷你学生蝴蝶钢琴
LA Custom C7 Grand Piano|洛杉矶定制 C7 三角钢琴
Rhodes 1946 Pre-Piano|罗兹 1946 早期电钢琴
Vintage Vibe Electric Piano|复古律动电钢琴
Vintage Vibe Tine Bass|复古律动簧片键盘贝斯
Weltmeister Claviset E.Piano|威尔特迈斯特电钢琴
Weltmeister Basset|威尔特迈斯特键盘贝斯
Rhodes Piano Bass|罗兹键盘贝斯
Clavinet C|C 型击弦电古钢琴
Pianet M|M 型电钢琴
Pianet N|N 型电钢琴
Pianet T|T 型电钢琴
Wing Tack Piano|温氏图钉钢琴
Wing Upright|温氏立式钢琴
Wurlitzer|沃尔里策
E.Piano|电钢琴
E.Grand|电三角钢琴
MK-80 Rhodes|MK-80 电钢琴
LA Rhodes|洛杉矶电钢琴
LA Piano|洛杉矶钢琴
MKS EP|MKS 电钢琴
MKS Piano|MKS 钢琴
LA Custom|洛杉矶定制
MIDI|数字控制
LoFi|低保真
Auto-Wah|自动哇音
Fuzz Face|绒面失真
Cry Baby|哭泣哇音
Fulltone|富尔通
AC-30|AC-30
MXR Phase 90|MXR 90 相位效果
Phase 90|90 相位效果
Univibe|复古旋转相位
Suitcase|箱体式
Grand|三角琴
Upright|立式钢琴
Celeste|钢片琴
Glock|钟琴
Chimeatron|键盘钟琴
Clavichord|击弦古钢琴
Dolceola|键盘弦琴
Dulcitone|音叉键盘琴
Harmochord|簧风琴
Electric Harpsichord|电拨弦古钢琴
Electric Acoustic Grand|电声混合三角钢琴
Electric Grand|电三角钢琴
Electric Piano|电钢琴
Harpsichord|拨弦古钢琴
Piano|钢琴
Rhodes|罗兹电钢琴
Vibanet|击弦电古钢琴
Pre-Piano|早期电钢琴
Tack|图钉
Bass|贝斯
Brass|铜管
Keys|键盘
Lead|主音
Pad|铺底
Perc|打击
Core|核心
Saw|锯齿波
Seq|序列
City|都市
Bright|明亮
Brite|明亮
Dark|暗色
Warm|温暖
Softest|极柔和
Soft|柔和
Mellow|柔润
Dry|干声
Wet|湿声
Classic|经典
Classical|古典
Stereo|立体声
Mono|单声道
Full|饱满
Wide|宽广
Thin|纤细
Deep|深沉
Extra|超强
Hard|硬朗
Muted|闷音
Mute|闷音
Sustain|延音
Staccato|断奏
Octaves|八度
Octave|八度
Octo|八度
Sub|超低音
Amp|音箱
Crunchy|轻度失真
Crunch|轻度失真
Distortion|失真
Overdriven|过载
Drive|驱动
Driver|驱动器
Clean|清音
Phaser|移相
Phase|相位
Chorus|合唱
Tremolo|颤音
Trem|颤音
Vibrato|揉音
Vibra|颤动
Vibe|律动
Vibes|颤音琴
Wah|哇音
Room|房间
Studio|录音室
Stage|舞台
Ballad|抒情
Blues|布鲁斯
Bluesy|布鲁斯风格
Gospel|福音
Pop|流行
Rock|摇滚
Rocker|摇滚演奏
Funk|放克
Funky|放克
Rhythm|节奏
Indie|独立音乐
Retro|复古
Vintage|复古
Eighties|八十年代
Seventies|七十年代
Sixties|六十年代
Early|早期
Late|晚期
Old|旧式
Olden Days|旧日时光
Aged|陈旧
Antique|古董
Dream|梦境
Dreams|梦境
Dreamland|梦境
Spacey|太空感
Space|空间
Liquid|流动
Lush|丰润
Luscious|丰润
Juicy|饱满
Smooth|顺滑
Rich|丰富
Clear|清澈
Solid|扎实
Simple|简洁
Accent|重音
Ambient|氛围
Reeds|簧舌
Angelic|天使般
Plectrum|拨片
Blue|忧郁
Brian's Pet Sounds|布赖恩的宠物之声
Cathedral|大教堂
Ceremonial|仪式
Charmer|魅力
Chimes|钟铃
Chimey|钟铃感
Belltone|钟音
Belltower|钟楼
Big Bells|大钟
Tolling Bells|鸣钟
Bells|铃音
Shimmer|微光
Christmas|圣诞
Blend|混合
Cinematic|电影感
Cinema|电影
Club|俱乐部
Combo|组合
Vox|沃克斯
Cry|哭泣
Crystal|水晶
Desolate|荒凉
Deserted|荒芜
Distant|远景
Dronescape|持续音景
Epic|史诗
Fast|快速
Slow|缓慢
Pump|抽吸
Folk|民谣
Forsaken|遗落
Gold Star|金星
Haunted|幽灵
Hollywood|好莱坞
Honky Tonk|酒馆钢琴
Hybrid|混合
Digital|数字
Lullaby|摇篮曲
Mood|情绪
Jersey|泽西
Env Filter|包络滤波
Filter|滤波
Comp|压缩
Percussive|敲击感
Sampler|采样器
Lonely Basement|孤独地下室
Music Box|八音盒
Musicbox|八音盒
Noise|噪声
Mini|迷你
Morricone|莫里康内
Musette|缪塞特
Toy|玩具
Odd Couple|奇妙搭档
Orchestral|管弦乐
Organic|自然质感
Pianet|电钢琴
Pluck|拨奏
Plucky|拨奏
Reckoning|清算
Scoring|配乐
Score|配乐
Speaker|扬声器
Spin|旋转
Spinner|旋转
Stops|音栓
Soaked|浸润
Church|教堂
Sola|独奏
Octavia|八度失真
Shifted|移调
Shiny|闪亮
Spring|弹簧
Super|超级
Wash|弥漫
Infinite|无限
Fuzz|绒面失真
Fuzzy|绒面失真
Fairydust|精灵尘埃
Sparkle Bell|闪耀铃音
Vinyl|黑胶
Warped|扭曲
Guitarish|吉他质感
Jangle|叮当
Octabell|八度钟音
Octapluck|八度拨奏
Octathump|八度重击
Octavo|八度
Retroctave|复古八度
Retropluck|复古拨奏
Retrospace|复古空间
Saucer Bell|碟铃
Shine|光泽
Underwater|水下
Pianoceleste|钢琴与钢片琴
Thump|重击
Thumper|重击
Tiny Saloon|小酒馆
Trashy|粗砺
Trippy|迷幻
Triumphant Victory|凯旋
Uber|超级
Ukrainian|乌克兰
Ultra|超强
Unreal Smash|超现实重击
Harpsivibe|拨弦颤音
Innocence|纯真
Vibrabell|颤动钟音
Vibraharp|颤音琴
Sines|正弦波
Shakes|摇动
Damping|制音
Watery|水波
Twin|双音箱
Fuzz Face|绒面失真
Yuletide|圣诞时节
Celechimes|钢片琴钟铃
Choruspace|合唱空间
Dreamphase|梦境相位
Tremopad|颤音铺底
Dulcelesta|音叉与钢片琴
Dulcitonia|音叉幻境
Echoshift|移调回声
Echospace|回声空间
Echowarp|扭曲回声
Octobass|八度贝斯
Phasedrive|相位驱动
Pianola|自动钢琴
Vibramute|闷音颤动
Vibraspace|颤动空间
""")

# Native parameter labels use the same dictionary; IDs and numeric units remain
# the host's original values. Per-library explicit labels can extend this view.
PHRASES.update(
    table("""
Volume|音量
Gain|增益
Pan|声像
Panning|声像
Balance|平衡
Reverb|混响
Delay|延迟
Mix|混合
Dry/Wet|干湿比
Output|输出
Input|输入
Attack|起音
Decay|衰减
Release|释音
Dynamics|动态
Velocity|力度
Tone|音色
Tune|调音
Tuning|调律
Pitch|音高
Transpose|移调
Detune|失谐
Cutoff|截止频率
Resonance|共振
Frequency|频率
Feedback|反馈
Depth|深度
Rate|速率
Speed|速度
Width|宽度
On|开启
Off|关闭
Bypass|旁通
Enable|启用
Enabled|已启用
Threshold|阈值
Ratio|压缩比
Knee|拐点
Amount|程度
Mode|模式
Type|类型
Size|大小
Time|时间
Level|电平
Pedal|踏板
Damper|制音器
Hammer|琴槌
String|琴弦
Soundboard|音板
Microphone|麦克风
Position|位置
Distance|距离
Lid|琴盖
Brightness|明亮度
Hardness|硬度
Impedance|阻抗
Unison|同度弦
Stretch|伸展
Condition|状态
Age|年份
Noise|噪声
Sympathetic|共鸣
Harmonic|泛音
Harmonics|泛音
Saturation|饱和
Compression|压缩
EQ|均衡
Low|低频
High|高频
Mid|中频
Bass|低音
Treble|高音
Eff|效果
DelPol|延迟极性
DelTime|延迟时间
DelFeed|延迟反馈
Freq|频率
Q|共振宽度
LFO|低频振荡
Osc|振荡器
Filter|滤波器
""")
)
PHRASES.update(
    table("""
Tape|磁带
Echo|回声
Broken|破碎
Octa|八度
Chimer|钟铃
Chime|钟铃
Enhanced|增强
Tacked|图钉
Bark|咆哮
Dimension|空间维度
Aftertouch|触后
Controller|控制器
Freewheeling oscillator quality|离线振荡器质量
Freewheeling sample quality|离线采样质量
Oscillator quality|振荡器质量
Oversampling|过采样
Pitch bend|弯音
Polyphony|复音数
Preload size|预加载大小
Sample quality|采样质量
Scala root key|音阶基准音
Spec|音源参数
Stretched tuning|伸展调律
Sustain cancels release|延音取消释音
12 Strings|十二弦
4'|四英尺音栓
""")
)
from .instrument_localization_pianoteq import VOCABULARY  # noqa: E402

PHRASES.update(table(VOCABULARY))
_choices = {**PHRASES, **MODELS}
_pattern = re.compile(
    r"(?<![A-Za-z])(?:"
    + "|".join(re.escape(k) for k in sorted(_choices, key=len, reverse=True))
    + r")(?![A-Za-z])",
    re.I,
)
_folded = {k.casefold(): v for k, v in _choices.items()}
_codes = re.compile(
    r"\b(?:[0-9]+[A-Z]+|[A-Z]{1,4}-?\d+[A-Z]*|[A-Z]\d?|[WM]KI{0,2}|AC-30|CP70|MXR|EP|NR|MKS|[0-9]+s|dB|Hz|kHz|ms|sec|V-B|V-M|DG|XT|SK-EX|VC|EAP|RMI)\b"
)


def translate(text):
    """Return a translation and unresolved words; never change a factory name."""
    value = _pattern.sub(lambda m: _folded[m[0].casefold()], text)
    rest = _codes.sub("", value)
    missing = sorted(set(re.findall(r"[A-Za-zÀ-ÿ]+(?:'[A-Za-z]+)?", rest)))
    return re.sub(r"\s+", " ", value).strip(), missing


def label(text, explicit=None, fallback="音色"):
    if explicit:
        unchanged_code = (isinstance(explicit, str) and explicit == text
                          and translate(text) == (text, []))
        if not isinstance(explicit, str) or (not re.search(r"[\u4e00-\u9fff]", explicit) and not unchanged_code):
            raise ValueError("Chinese instrument labels must contain Chinese text")
        return explicit
    translated, missing = translate(text)
    return translated if not missing else fallback


def bilingual(chinese, original):
    """Present translated names alongside their unchanged factory spelling."""
    return chinese if chinese == original else f"{chinese}（{original}）"


def translated_names(entry):
    names = entry.get("labels_zh", {})
    model = entry["model"]
    variant = entry["variant_name"]
    if entry.get("library_id") == "spectrasonics-keyscape" and " - " in variant:
        variant = variant.split(" - ", 1)[1]
    elif entry.get("expected_pianoteq"):
        native_model = entry["expected_pianoteq"]["instrument"]
        aliases = {
            "Vintage Tines MKI": "MKI",
            "Vintage Tines MKII": "MKII",
            "Vintage Reeds": "W1",
            "Grand Steinway D (New York)": "NY Steinway D",
            "Grand C. Bechstein DG": "C. Bechstein DG",
        }
        prefix = aliases.get(native_model, native_model)
        if variant == prefix:
            variant = "Original"
        elif variant.startswith(prefix + " "):
            variant = variant[len(prefix) :].strip(" -")
    translated_model = label(model["name"], names.get("model"), "乐器型号")
    translated_variant = label(variant, names.get("variant"))
    return translated_model, translated_variant


def localized(entry):
    model = entry["model"]
    translated_model, translated_variant = translated_names(entry)
    result = {
        **entry,
        "display_name": bilingual(translated_model + " · " + translated_variant, entry["name"]),
        "display_variant_name": bilingual(translated_variant, entry["variant_name"]),
        "model": {**model, "display_name": bilingual(translated_model, model["name"])},
        "source": {
            **entry["source"],
            # Product/library names are proper names, including for future imports.
            # Ignore legacy translated source labels without rewriting the catalog.
            "display_name": entry["source"]["library_name"],
        },
    }
    if "presentation_title" in entry:
        # Validate against the unmodified identity fields, then project only
        # display strings. The overlay must never change taxonomy/model state.
        from .instrument_presentation_amendments import validate_payload

        reviewed = validate_payload(entry, entry["presentation_title"])
        display_variant = bilingual(reviewed["zh"], reviewed["title"])
        result["display_variant_name"] = display_variant
        result["display_name"] = result["model"]["display_name"] + " · " + display_variant
    return result


def prepare_import(entry, overrides=None):
    """All import adapters can persist explicit translations before admission."""
    if overrides and entry["id"] in overrides:
        entry = {**entry, "labels_zh": overrides[entry["id"]]}
    result = localized(entry)
    missing = []
    for field, original in [
        ("model", entry["model"]["name"]),
        ("variant", entry["variant_name"].split(" - ", 1)[-1]),
    ]:
        if not entry.get("labels_zh", {}).get(field):
            missing.extend(translate(original)[1])
    if missing:
        raise ValueError(
            "Provide labels_zh for untranslated instrument names: " + ", ".join(sorted(set(missing)))
        )
    # Persist only the translation, not the formatted bilingual display value:
    # imports can be classified again on every read without nesting parentheses.
    model, variant = translated_names(entry)
    return {
        **result,
        "labels_zh": {**entry.get("labels_zh", {}), "model": model, "variant": variant},
    }


def prepare_entries(entries, bank):
    """Shared translation preflight, before expensive native validation."""
    import json
    from pathlib import Path
    from .instrument_taxonomy import classify

    path = Path(bank) / "labels.zh-CN.json"
    overrides = json.loads(path.read_bytes()) if path.is_file() else {}
    if not isinstance(overrides, dict):
        raise ValueError("Invalid Chinese instrument labels profile")
    return [prepare_import(classify(entry), overrides) for entry in entries]
