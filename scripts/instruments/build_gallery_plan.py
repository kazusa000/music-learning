"""Build reviewed local presentation mappings from the read-only admitted catalogue.

This writes project resources only; it never publishes presets or edits the sound library.
"""
from pathlib import Path
import json
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from music_learning.instruments import catalog  # noqa: E402
from music_learning.instrument_localization import translated_names  # noqa: E402
from music_learning.instrument_taxonomy import INDEX  # noqa: E402
from music_learning.settings import Settings  # noqa: E402

# One physical form per category. Several sources can depict that same form.
FORMS = {
    "acoustic-piano": ("concert grand piano", "踏板延音与触键力度共同塑造和声的连贯性。"),
    "electric-piano": ("unbranded compact vintage reed electric piano", "轻奏与重奏的起音不同，可比较颤音、放大器和衰减。"),
    "harpsichord": ("wooden single manual wing-shaped harpsichord", "拨弦起音清楚，分解和弦与声部交错能保留颗粒。"),
    "clavinet": ("unbranded vintage clavinet electric clavichord keyboard with wood cheeks", "短音、止音和切分节奏比持续堆叠更能体现击弦感。"),
    "clavichord": ("small rectangular walnut acoustic clavichord with open horizontal lid", "纤细的击弦起音适合近距离、低密度的键盘织体。"),
    "pipe-organ": ("small pipe organ console and graduated metal pipes", "持续音由音栓组合塑形，注意低音与和弦的留白。"),
    "electric-organ": ("vintage dual manual tonewheel organ console with drawbars", "音栓、旋转扬声器与连奏共同决定风琴的推动感。"),
    "reed-organ": ("antique wooden harmonium with bellows and keyboard", "风箱感和持续和弦构成温暖底色，可用短句避免遮挡主旋律。"),
    "accordion": ("a complete black piano accordion with ivory keyboard and pleated bellows", "用乐句呼吸组织持续音，左右声部避免同时填满低中频。"),
    "toy-keyboard": ("small vintage electronic toy keyboard with miniature keys", "短音域和鲜明电子起音适合作为旋律点缀。"),
    "celesta": ("upright wooden celesta with keyboard and resonators", "清脆起音和金属余音适合高音区的旋律与点缀。"),
    "toy-piano": ("small wooden tabletop toy piano", "有限音域与带噪起音适合轻巧、稚拙的动机。"),
    "dulcitone": ("antique wooden dulcitone small upright keyboard with tuning fork mechanism", "音叉般的圆润短音适合细密分解和弦。"),
    "carillon-keyboard": ("vintage compact chime keyboard with wooden case", "钟声的余韵会累积，和声转换时注意止音。"),
    "dolceola": ("small early twentieth century wooden dolceola keyboard zither", "齐特琴式的击弦颗粒适合错落的和弦和单音装饰。"),
    "keyboard-bass": ("short vintage two-octave keyboard bass instrument", "用低音区的短句衔接和声，并与底鼓的起音错开。"),
    "acoustic-guitar": ("steel-string acoustic guitar with round sound hole", "分解与扫弦需要保留拨弦顺序和止音空隙。"),
    "electric-guitar": ("clean vintage six-string electric guitar with pickups and maple neck", "音长、同时发声音数与放大器共同决定清晰度。"),
    "electric-bass": ("vintage four-string electric bass guitar", "低音线的起止位置与底鼓共同组织律动。"),
    "upright-bass": ("full upright double bass with endpin and dark amber body", "长音适合支撑和声，短音适合与鼓组形成弹性。"),
    "violin": ("orchestral violin with amber varnish", "用乐句方向安排音长与力度，持续与短弓承担不同角色。"),
    "viola": ("orchestral viola with amber varnish", "中音区连接旋律与低音，避免与主唱持续重叠。"),
    "cello": ("full cello with endpin, four strings, amber wooden body", "中低音连奏可承接旋律，短弓能加强和声节奏。"),
    "string-ensemble": ("small ensemble of violin viola and cello instruments without people", "分配高、中、低声部的活动量，保持内声部顺畅。"),
    "erhu": ("Chinese two-string erhu with hexagonal snakeskin resonator and long neck", "单音旋律的停顿、连奏与装饰音决定表达。"),
    "fiddle": ("traditional fiddle violin with warm worn wooden finish", "舞曲短弓与重复动机适合清晰的拍点和句尾收束。"),
    "hurdy-gurdy": ("traditional wooden hurdy gurdy with crank wheel and tangent keybox", "持续低音与轮弦质感适合重复动机和调式旋律。"),
    "kemence": ("small pear-shaped bowed Turkish classical kemence with three strings", "窄音域的细腻旋律可与简单持续低音配合。"),
    "flute": ("silver concert transverse flute", "旋律需要呼吸空隙，高音与中音的力度应区别安排。"),
    "piccolo": ("small black wood piccolo with silver keywork", "短笛穿透力强，常用于高音加倍和短促呼应。"),
    "bansuri": ("Indian bamboo transverse bansuri flute with finger holes", "气息起音与平滑旋律适合疏朗伴奏。"),
    "kaval": ("traditional long wooden end-blown kaval flute with finger holes", "气声与音符之间的空隙能保留木管的质感。"),
    "ney": ("traditional reed ney end-blown flute with brass mouthpiece", "柔和气声适合长线条，换气位置比持续铺满更自然。"),
    "recorder": ("wooden alto recorder with block mouthpiece and finger holes", "清楚的吐音适合简洁对位和短乐句。"),
    "shakuhachi": ("Japanese bamboo shakuhachi with root-end bell and oblique blowing edge", "气息、留白和音头变化是尺八旋律的核心。"),
    "whistle": ("Irish tin whistle with six finger holes and dark fipple mouthpiece", "灵活的单音与短装饰适合轻快舞曲动机。"),
    "duduk": ("Armenian apricot wood duduk with broad double reed", "低密度伴奏能衬托簧片与气息的温暖质感。"),
    "shehnai": ("Indian wooden shehnai with flared metal bell and reed mouthpiece", "明亮簧片适合带装饰的前景旋律。"),
    "sho": ("Japanese sho mouth organ with vertical bamboo pipes and wooden wind chamber", "簇状持续和声适合静态、缓慢变化的织体。"),
    "zurna": ("wooden zurna shawm with strongly flared bell and small double reed", "强烈起音适合短句、呼应与明显的节奏边界。"),
    "bagpipes": ("traditional bagpipes with bag chanter and drone pipes", "持续低音形成调式背景，旋律应围绕稳定音展开。"),
    "saxophone": ("warm brass tenor saxophone with curved neck and bell", "力度、短句停顿与重音共同塑造管乐的呼吸。"),
    "clarinet": ("black wood Bb clarinet with silver keywork", "低中音与高音具有不同色彩，可用音区组织问答。"),
    "oboe": ("black wood oboe with silver keys and double reed", "清晰的簧片音适合抒情旋律与木管独奏。"),
    "bassoon": ("complete reddish wooden bassoon with silver bocal and keywork", "低中音既能连接低音，也适合轻巧的节奏短句。"),
    "horn": ("orchestral French horn with coiled brass tubing and broad bell", "圆号适合内声部与宽广旋律，注意和声层的渐变。"),
    "trumpet": ("brass orchestral trumpet with three piston valves", "清楚音头与句尾止音能让铜管齐奏更紧凑。"),
    "trombone": ("brass tenor slide trombone with long slide", "中低音和声与短促强调适合保留足够的句间空隙。"),
    "tuba": ("large orchestral brass tuba with upright bell and valves", "低音支撑应保持简洁，留意与低音提琴及底鼓的重叠。"),
    "brass-ensemble": ("arrangement of trumpet French horn and trombone without people", "按音区分配和弦，齐奏重音与释放应保持一致。"),
    "choir": ("one elegant studio vocal microphone with a small semicircle of music stands, no people", "人声层的元音与音域决定融合度，和弦不要挤在同一音区。"),
    "lute": ("Renaissance wooden lute with pear-shaped ribbed back and angled pegbox", "拨弦声部适合细致对位，较短音长能保持线条清晰。"),
    "guzheng": ("Chinese guzheng long wooden zither with individual movable bridges", "分解音型与旋律应留出装饰音和滑音的空间。"),
    "kanun": ("Turkish qanun trapezoidal zither with many string courses and small levers", "密集拨弦纹理适合围绕旋律做细小呼应。"),
    "kora": ("West African kora with large calabash resonator skin face and long neck", "交错拨弦动机可让低音与高音形成循环问答。"),
    "koto": ("Japanese koto long wooden zither with thirteen movable bridges", "稀疏动机与余音能突出每次拨弦的音色变化。"),
    "nyatiti": ("Kenyan nyatiti eight-string bowl lyre with two wooden arms and crossbar", "重复音型与低音重拍共同建立拨弦律动。"),
    "oud": ("Arabic oud with round wooden bowl body short fretless neck and bent pegbox", "单音旋律与装饰音适合配合简洁低音和打击乐。"),
    "pipa": ("Chinese pipa pear-shaped lute with four strings and fretted neck", "轮指、短拨和停顿承担不同层次，避免全部音符同样长度。"),
    "saz": ("Turkish long-necked baglama saz with small wooden pear body", "重复拨弦和单音动机适合明确的调式重心。"),
    "sitar": ("Indian sitar with long fretted neck gourd resonator and sympathetic strings", "旋律与共鸣的余韵需要留白，持续低音可保持稳定。"),
    "tanpura": ("Indian tanpura with gourd resonator long fretless neck and four strings", "持续空弦构成音高背景，不承担密集旋律。"),
    "tres": ("Cuban tres small guitar with three double courses of strings", "切分拨弦与短促和弦适合配合拉丁打击乐。"),
    "harp": ("complete orchestral concert pedal harp with curved neck column and strings", "分解和弦的音域与余韵决定空间，低音宜控制密度。"),
    "kalimba": ("wooden African thumb piano kalimba with metal tines", "短促音头和钟状余音适合重复小动机。"),
    "santur": ("Persian santur trapezoid hammered dulcimer with thin strings and two beaters", "双槌交替适合快速音型，和声变化时留意余音。"),
    "yangqin": ("Chinese yangqin butterfly-shaped hammered dulcimer on stand with bamboo beaters", "轮音与分解纹理适合连接旋律，避免低中音持续堆叠。"),
    "balafon": ("West African wooden balafon with gourd resonators", "木质起音适合交错节奏和清楚的重复乐句。"),
    "gong": ("large suspended bronze orchestral gong on restrained wooden frame", "长余音适合段落边界，避免频繁触发掩盖后续声部。"),
    "music-box": ("small open wooden mechanical music box with metal comb and pinned cylinder", "短旋律与有限音区可保留音乐盒的精巧感。"),
    "shakers": ("pair of wooden maracas and a small woven shaker", "轻重音与细分变化让持续律动更自然。"),
    "bongos": ("pair of Cuban wooden bongos joined together with natural skins", "高低鼓交替与短装饰适合回应主节奏。"),
    "congas": ("pair of tall wooden conga drums with natural skins", "开放音、闷音与重拍可建立层次清楚的手鼓律动。"),
    "dayereh": ("Persian round frame drum daf with skin and internal metal rings", "框鼓的边缘与中心音适合形成明暗交替。"),
    "darabuka": ("Middle Eastern goblet darbuka drum with ornate dark metal body", "低音与边缘高音的问答适合明确的节奏骨架。"),
    "djembe": ("West African goblet-shaped wooden djembe with rope-tuned goatskin", "低音、开音和拍击的对比决定律动，不宜全用同一力度。"),
    "ewe-percussion": ("small Ghanaian percussion ensemble with three barrel drums iron bell and shaker", "多个鼓声部各自承担不同细分，保持重音的错落。"),
    "kroboto-ensemble": ("Ghanaian kroboto boba and kidi barrel drums grouped together", "鼓组问答适合短循环，注意每个声部的音色分工。"),
    "riq": ("small Arabic riq frame tambourine with paired brass jingles", "铃片与皮膜音共同承担细分和重拍。"),
    "tabla": ("Indian tabla pair with wooden dayan and metal bayan, central black tuning spots", "高低鼓的组合适合有呼吸的节奏句，而非机械均匀连打。"),
    "taiko": ("large Japanese nagado taiko drum on wooden stand with two bachi", "强重音与句间留白能凸显大鼓的身体感。"),
    "timbales": ("pair of shallow metal timbales on stand with cowbell", "鼓边、钟声与开放音适合组织拉丁节奏的层次。"),
    "tonbak": ("Persian wooden tonbak goblet drum with natural skin", "指尖与掌击的交替适合细密、流动的节奏句。"),
    "tar": ("large simple wooden tar frame drum with natural skin", "中心低音与边缘短音形成清楚的节奏轮廓。"),
    "davul": ("large Turkish double-headed davul drum with wooden cylinder and beaters", "低重拍与高短击可支撑鲜明的行进感。"),
    "claves": ("two hardwood claves crossed in a simple product composition", "固定节奏骨架需要稳定重音，留出其它打击乐的空隙。"),
    "cymbals": ("pair of orchestral bronze crash cymbals with leather straps", "钹的余韵适合强化段落边界，释放时间要与后续和声配合。"),
    "triangle": ("orchestral steel triangle suspended with one slim beater", "明亮点缀不必每拍出现，稀疏落点更容易被听见。"),
    "woodblock": ("rectangular wooden percussion woodblock with a slit and two wooden beaters", "干净短音适合细分与呼应，力度变化可避免机械感。"),
    "bass-drum": ("large orchestral bass drum mounted sideways on a simple stand", "大鼓适合宽阔重拍，先控制余音再叠加低频声部。"),
    "snare-drum": ("concert snare drum with metal shell and two wooden sticks", "滚奏、单击与重音分别承担连接和强调。"),
    "drum-kit": ("complete acoustic drum kit with bass drum snare toms hi-hat and cymbals", "底鼓与军鼓先建立骨架，踩镲和过门再补充细分。"),
    "timpani": ("pair of orchestral copper timpani with pedal bases and soft mallets", "定音鼓应跟随和声根音与结构重拍，注意音高变化。"),
    "glockenspiel": ("orchestral glockenspiel with two rows of silver metal bars and two beaters", "高音余韵适合短动机、加倍与点缀。"),
    "xylophone": ("orchestral xylophone with two rows of dark rosewood bars and resonators", "清楚木质起音适合快速音型与节奏旋律。"),
    "marimba": ("full wooden marimba with graduated rosewood bars and golden resonators", "低音圆润、高音清脆，可用音区区别旋律与和声。"),
    "vibraphone": ("concert vibraphone with silver bars resonators damper pedal and mallets", "踏板与颤音影响和声清晰度，转和弦时注意余音。"),
    "steelpan": ("polished steel pan drum on stand with distinct hammered note areas", "金属共鸣与轻巧旋律适合疏朗的和声背景。"),
    "beats-engine": ("original unbranded compact groovebox with pads and step sequencer", "循环层可分配不同重音和密度，避免所有层同时填满。"),
    "synths": ("original unbranded analog synthesizer keyboard with wooden cheeks and tactile controls", "振荡器、滤波器和包络共同塑造动态，先确定声部角色再调音色。"),
    "multi-instrument": ("two complementary vintage keyboards arranged in a tasteful layered instrument portrait", "层叠音色要按音区和起音分工，避免每层都占满。"),
    "unclassified": ("original elegant electronic keyboard instrument with tactile controls", "先试弹音区与力度，再决定它在编曲中的位置。"),
}


def slug(value):
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub("[^a-z0-9]+", "-", text).strip("-")


SHAPES = {
    "Rhodes Piano Bass": "short 32-key Rhodes Piano Bass, orange-red flat lid, black textured lower case, silver fascia, no full-size piano or speaker cabinet",
    "Chimeatron": "vintage Chimeatron electro-acoustic chime keyboard, compact walnut cabinet and pale small keyboard, quiet bronze details",
    "Electric Harpsichord": "vintage Baldwin-style electric harpsichord, long narrow rectangular dark red and walnut case with electric controls, not a wing-shaped acoustic harpsichord",
    "JD-800 Crystal Rhodes": "Roland JD-800 style black digital synthesizer, wide panel densely populated with many sliders, red accents, full keyboard, no wood electric-piano shell",
    "Wing Tack Piano": "antique Wing upright tack piano, tall worn amber wood cabinet, upright rectangular construction, slightly open front panel and keyboard, no grand tail",
    "Wing Upright Piano": "antique Wing upright piano in dark walnut, tall flat-backed cabinet and upright front above keyboard, distinct from a concert grand",
    "Hohner Clavinet-Pianet DUO": "Hohner Clavinet Pianet Duo electric keyboard, angular long low dark case, ivory keys, selectors on left, four slender chrome legs",
    "Vintage Vibe Vibanet": "Vintage Vibe Vibanet electric clavinet, glossy red and white angular low case, full keyboard and chrome legs",
    "Yamaha CP-70": "Yamaha CP-70 electric grand piano, recognizable angular black shallow grand body, folding-style chrome legs, silver control area and full keyboard",
    "Hohner Pianet M": "Hohner Pianet M vintage electric keyboard, compact warm walnut case, cream keys, simple dark upper panel, four slender legs",
    "Hohner Pianet N": "Hohner Pianet N electric piano, brown wood rectangular cabinet, black lid and silver front fascia, chrome support legs",
    "Hohner Pianet T": "portable Hohner Pianet T electric keyboard, low dark brown flat rectangular briefcase-style case, compact keyboard, no tall speaker cabinet",
    "Rhodes - LA Custom \"E\"": "custom-voiced Rhodes Mark I style electric piano, black textured case, silver control fascia, keyboard and chrome stage legs, close three-quarter keyboard-side view; customization is internal, no invented ornament",
    "Vintage Vibe Electric Piano": "modern Vintage Vibe tine electric piano, compact red sparkle molded shell with white trim and chrome legs",
    "Weltmeister Claviset": "Weltmeister Claviset vintage electric keyboard in a low brown wood rectangular cabinet, pale keys, small left control section",
    "Wurlitzer 140B": "Wurlitzer 140B electric piano, tan-grey painted metal angular rounded compact body, cream keys, thin chrome legs; clearly older angular form than the black molded 200A",
    "Vintage Vibe Tine Bass": "compact Vintage Vibe tine bass keyboard, short red sparkle keyboard cabinet with chrome supports, small bass-range keyboard",
    "Weltmeister Basset": "Weltmeister Basset vintage bass keyboard, very short cream keyboard in slim warm brown angular cabinet, two-octave proportions",
    "Mini Student Butterfly Piano": "small antique student butterfly grand piano, compact symmetrical split butterfly lid opened to both sides, warm brown wooden body, short keyboard and slender wooden legs",
    "Rhodes 1946 Pre-Piano": "Rhodes 1946 Pre-Piano, small early electric piano with honey colored wood cabinet, short keyboard and simple period legs, not a black 1970s Rhodes",
    "Toy Piano - Classic": "small antique upright toy piano with miniature cream keys, aged dark wood rectangular cabinet",
    "Toy Piano - Glock": "small glockenspiel toy piano, bright pale wooden box with miniature keyboard and glimpse of metal bar mechanism",
    "Toy Piano - Grand": "miniature wooden toy grand piano, tiny grand outline with raised short lid and three small legs",
    "Toy Piano - Saucer Bell": "unusual antique saucer-bell toy piano, small wooden keyboard cabinet with a row of shallow metal saucer resonators above the keys",
    "MK-80 Digital Rhodes": "Roland Rhodes MK-80 style digital stage piano, long black electronic keyboard, sparse buttons and small display above the keys, not a mechanical reed piano",
    "MKS-20 Digital Piano": "Roland MKS-20 style black rackmount digital piano sound module, narrow 19-inch rack face with rotary control, small green display and buttons; NO attached piano keyboard",
    "Vintage Tines MKII": "Rhodes Mark II style tine electric piano, flat rectangular black lid, silver front control strip, keyboard on chrome stage legs",
    "Vintage Reeds": "compact vintage reed electric piano, low charcoal rounded case with speaker grilles and pale keys, silver slender legs",
    "Clavinet D6": "Hohner Clavinet D6, long black flat electric clavichord case with walnut wood side cheeks and rocker switches on the left",
    "Electra-Piano": "Hohner Electra-Piano vintage electric piano, substantial dark wood rectangular cabinet on four legs, black upper lid and pale full keyboard",
    "H. Ruckers II Harpsichord": "Flemish Hans Ruckers II style harpsichord, long narrow wing outline, elegantly painted pale case and decorated inner soundboard, turned wooden legs, no modern metal piano frame",
    "G. Giusti Harpsichord (1680)": "Giovanni Giusti 1680 Italian harpsichord, slender angular wing-shaped warm cypress wood body, short period keyboard, restrained wooden stand, no modern piano pedals",
    "J. Salodiensis Virginal (1600)": "Joseph Salodiensis 1600 virginal, small rectangular horizontal wooden box with a short recessed keyboard, decorative inner lid and slender stand; no grand-piano tail",
    "J. Weimes Pianoforte (1808)": "Jacob Weimes 1808 Viennese pianoforte, slim warm walnut grand-like wooden frame, approximately five and a half octave period keyboard, thin tapered legs and early pedals",
    "NY Steinway Square (1858)": "New York Steinway 1858 square piano, wide rectangular rosewood table-like body, carved period legs and lyre, open rectangular lid, no triangular modern grand shape",
    "Ph. Schmidt Square (1780)": "Philippus Schmidt 1780 square piano, modest narrow rectangular walnut coffer with short keyboard offset along its front and four thin straight legs, no concert-grand tail",
    "J. Dohnal (1795)": "Joseph Dohnal 1795 Viennese fortepiano, light honey wood frame and narrow grand-like body, short five-octave period keyboard, delicate straight legs, early knee levers, no modern black concert grand",
    "I. Besendorfer (1829)": "Ignaz Besendorfer 1829 early Romantic grand piano, long warm figured walnut body and plain raised wooden lid, slender period legs and early pedal lyre, no modern black lacquer shell",
    "J.B. Streicher (1852)": "Johann Baptist Streicher 1852 grand piano, elegant long brown rosewood body, period keyboard and narrow carved legs, restrained nineteenth-century pedal lyre",
    "S. Erard (1849)": "Sebastien Erard 1849 grand piano, rich rosewood long curved body with raised wooden lid, carved nineteenth-century legs and period lyre, restrained brass detail",
    "C. Bechstein (1899)": "Carl Bechstein 1899 grand piano, deep mahogany wooden curved body with raised lid, late nineteenth-century legs and pedal lyre, approaching modern proportions but visibly aged wood",
    "I. Pleyel (1835)": "Ignace Pleyel 1835 French grand piano, slim warm mahogany body with gently curved rim and raised wooden lid, elegant narrow period legs and delicate lyre",
    "J. Broadwood (1796)": "John Broadwood 1796 English pianoforte, dark reddish wood long angular grand-like body, short five-and-half octave keyboard, modest straight legs and early pedal mechanism",
    "J. Frenzel (1841)": "Johann Frenzel 1841 Austrian grand piano, amber figured walnut body with period curved rim and raised lid, slender nineteenth-century legs and pedal lyre",
    "Celtic Harp": "small Celtic lever harp, warm wood triangular frame with curved neck, visible tuning levers, no pedal base",
    "Concert Harp": "large orchestral concert pedal harp, tall curved neck, carved golden wood pillar, many strings and pedal base",
    "Triple Harp": "Welsh triple harp, elegantly curved dark walnut frame with THREE parallel rows of strings, no modern concert pedal base",
    "Hand Pan": "bronze handpan, closed convex UFO-shaped upper shell with central dome and circular hammered tone fields, no stand or sticks",
    "Spacedrum": "stainless steel spacedrum handpan, smooth silver convex UFO shell with central dome and arranged tone fields, no open concave steelpan bowl",
    "Tank Drum": "steel tongue tank drum, dark blue metal rounded closed bowl with precisely cut radial tongues, not an open steelpan or handpan dimples",
    "Vibraphone V-B": "concert vibraphone with warm gold metal bars, cream and black rolling frame, graduated gold resonators and damper pedal",
    "Vibraphone V-M": "concert vibraphone with silver metal bars, dark angular metal frame, silver resonators, wheels and damper pedal",
    "Upright U4": "traditional U4 style upright piano, tall restrained dark walnut cabinet with three pedals and full keyboard, no grand tail",
    "Mini": "Minimoog Model D style compact analog synthesizer, warm walnut case, hinged sloping black control panel with three oscillator knob banks and 44 keys",
    "Mini 700": "Korg MiniKorg 700 style compact early analog synth, two-octave keyboard, wood cheeks, grey case and unusual green and orange controls along left and lower panel",
    "Orchestrator": "vintage orchestral string synthesizer keyboard, broad warm wood cabinet with dark front panel and rows of compact multicolored register switches",
    "String Melody II": "Logan String Melody II style string ensemble keyboard, dark brown wood side panels, black control band and register switches, full single manual; NOT an accordion",
    "Juno 106": "Roland Juno-106 style black 61-key analog synth, recognizable red and blue panel stripes, long row of sliders, no wood cheeks",
    "Korg XD": "vintage Korg-style analog keyboard source labelled XD, compact dark grey case, distinctive band of controls and full keyboard, model-inspired illustration without invented logos",
    "Odyssey": "ARP Odyssey style compact black and white analog synthesizer, densely arranged vertical sliders, short keyboard and contrasting cream panel sections",
    "Prophet 6": "Sequential Prophet-6 style polyphonic synthesizer, black panel with many round knobs, walnut wood end cheeks and 49-key keyboard",
    "YC 30": "Yamaha YC-30 style vintage combo organ, substantial warm wood case, one keyboard and bright register switches above, metal support legs",
    "Electone Organ": "vintage Yamaha Electone home organ, substantial dark wood console, two keyboards, colorful register tabs, pedalboard beneath",
    "CP11 Electronic Keyboard": "vintage CP11-style electronic stage keyboard, wide dark case, full piano keyboard and sparse analog switches, no harpsichord wing or strings",
    "EP10 Electronic Keyboard": "vintage EP10 electronic keyboard, compact dark brown rectangular case, pale piano keys and small control switches, no acoustic harpsichord shape",
    "EP20 Electronic Keyboard": "vintage EP20 electronic stage piano, low black rectangular body, full pale keyboard, orange rocker controls and simple stand",
    "RMI Electronic Keyboard": "RMI Electra-piano style electronic keyboard, bright orange-brown rectangular case, black control strip, simple register tabs and full keyboard",
    "Roady Electronic Keyboard": "vintage Roady electronic piano keyboard in a black portable flat case with small left control panel and stage legs",
    "BeeGees": "small vintage BeeGees-style electronic toy keyboard with pale plastic body, miniature keys and colorful rhythm controls, NOT an acoustic drum kit",
    "Concertmate": "vintage Concertmate portable electronic toy keyboard, slim grey plastic case, miniature keys and small orange function buttons, no drum kit",
    "CS-01 II": "Yamaha CS-01 II style compact monophonic synthesizer, small black and grey body, mini keys and green accented knobs/sliders at the left",
    "Droopy": "small quirky vintage toy synthesizer keyboard, warm brown low plastic case, miniature keys and tactile colored controls, no orchestral instrument",
    "HandySound": "small Yamaha HandySound style portable toy keyboard, blue-green plastic rectangular body, miniature white keys and compact round buttons",
    "Peo 800": "vintage Peo 800 toy electronic keyboard, cream and grey compact plastic body with small keys and distinctive colored function buttons",
    "Rapman": "Casio Rapman style grey and yellow toy electronic keyboard, bright yellow rhythm controls, small keys and portable plastic body",
    "SK-1": "Casio SK-1 style tiny sampling keyboard, dark grey plastic case, 32 miniature keys, colorful small sampling buttons and built-in microphone grille",
    "Tronichord": "early electronic Tronichord-style chord instrument, compact cream curved body with chord buttons and long touch strum strip, no piano keyboard",
    "VL-1": "Casio VL-1 style pocket calculator synthesizer, long thin white plastic body, tiny keyboard, small LCD and calculator-like buttons",
    "VSS-30": "Yamaha VSS-30 style compact sampling keyboard, grey and black plastic body, miniature keys, colored sampling buttons and microphone grille, NOT a guitar",
    "F 100": "vintage F100-style portable electronic organ keyboard, restrained dark brown and cream rectangular case, small keys and register controls",
    "Mellotron": "Mellotron M400 style cream-white wooden tape keyboard cabinet, distinctive blocky angular body and 35-key keyboard, small upper left controls",
    "Memory": "Memorymoog-inspired vintage polyphonic synthesizer, large black knob-filled panel, dark walnut cheeks and wide keyboard",
}


def physical_leaf(name, library, leaf):
    if name.endswith("Drum Machine"):
        return "drum-kit"
    if name in {"Mellotron", "Memory", "Mini", "Mini 700", "Orchestrator", "String Melody II", "Juno 106", "Korg XD", "Odyssey", "Prophet 6", "CS-01 II", "JD-800 Crystal Rhodes"}:
        return "synths"
    if name in {"BeeGees", "Concertmate", "Droopy", "HandySound", "Peo 800", "Rapman", "SK-1", "Tronichord", "VL-1", "VSS-30"}:
        return "toy-keyboard"
    if name in {"YC 30", "Electone Organ", "F 100"}:
        return "electric-organ"
    if name in {"CP11 Electronic Keyboard", "EP10 Electronic Keyboard", "EP20 Electronic Keyboard", "RMI Electronic Keyboard", "Roady Electronic Keyboard", "MK-80 Digital Rhodes", "MKS-20 Digital Piano"}:
        return "electric-piano"
    if name == "Duo Combination":
        return "multi-instrument"
    if library == "Pianoteq 9" and (name.startswith("Grand ") or re.search(r"\(\d{4}\)", name)):
        return "harpsichord" if "Harpsichord" in name or "Virginal" in name else "acoustic-piano"
    return leaf


def main():
    origin = ROOT.parents[2] if ROOT.parent.name == "worktrees" else ROOT
    settings = Settings.load(origin / "runtime/app.local.json")
    if settings.instrument_library is None:
        settings.instrument_library = Path(json.loads((origin / "runtime/instrument-library.local.json").read_bytes())["root"])
    entries = catalog(settings)
    curated = json.loads((ROOT / "resources/instruments/gallery.json").read_bytes())["instruments"]
    existing = {e["model_id"]: e for e in curated}
    old_images = {e["id"] for e in json.loads((ROOT / "resources/instruments/images.json").read_bytes())["images"]}
    generic = {"celesta": "celesta", "violin": "violins", "viola": "violins", "flute": "flute", "electric-bass": "electric-bass", "synths": "analog-machines"}
    aliases = {"Vintage Tines MKI": "Rhodes - Classic Mark I", "Mark I Electric Piano": "Rhodes - Classic Mark I", "Pianet N": "Hohner Pianet N", "Pianet T": "Hohner Pianet T"}
    vintage = {"Mini", "Mini 700", "Orchestrator", "String Melody II", "Juno 106", "Korg XD", "Odyssey", "Prophet 6", "YC 30", "Electone Organ", "CP11 Electronic Keyboard", "EP10 Electronic Keyboard", "EP20 Electronic Keyboard", "RMI Electronic Keyboard", "Roady Electronic Keyboard", "BeeGees", "Concertmate", "CS-01 II", "Droopy", "HandySound", "Peo 800", "Rapman", "SK-1", "Tronichord", "VL-1", "VSS-30", "F 100", "Mellotron", "Memory"}
    special_piano = {"Vintage Tines MKII", "Vintage Reeds", "Clavinet D6", "Electra-Piano", "H. Ruckers II Harpsichord", "Upright U4", "Vibraphone V-B", "Vibraphone V-M", "Celtic Harp", "Concert Harp", "Triple Harp", "Hand Pan", "Spacedrum", "Tank Drum"}
    models = {}
    assets = {}
    for entry in entries:
        if not entry.get("audio_verified"):
            continue
        model = entry["model"]
        name = model["name"]
        lib = entry["source"]["library_name"]
        if model["id"] in models:
            continue
        leaf = entry.get("instrument_ids", ["unclassified"])[0]
        if leaf.startswith("synth-") or leaf == "synths":
            leaf = "synths"
        leaf = physical_leaf(name, lib, leaf)
        node = INDEX.get(leaf, INDEX["unclassified"])
        family = node
        while family["parent_id"]:
            family = INDEX[family["parent_id"]]
        subject, hint = FORMS.get(leaf, FORMS["unclassified"])
        special = (lib == "Keyscape" and name not in {"Celeste", "Duo Combination"}) or (lib == "Pianoteq 9" and (name.startswith("Grand ") or re.search(r"\(\d{4}\)", name) or name in special_piano)) or name in vintage or name.endswith("Drum Machine")
        special = bool(special or name in aliases)
        identity = aliases.get(name, name)
        if identity == "Rhodes - Classic Mark I":
            asset_id = "rhodes-mark-i"
            special = True
        elif model["id"] in existing:
            asset_id = existing[model["id"]]["image_id"]
        elif special:
            asset_id = "model-" + slug(identity)
        elif name == "Classical Guitar":
            asset_id = "classical-guitar"
        elif lib == "Pianoteq 9" and name == "Steel Drum":
            asset_id = "category-steelpan"
        else:
            asset_id = generic.get(leaf, "category-" + leaf)
        if asset_id not in assets:
            if name.endswith("Drum Machine"):
                number = name.removesuffix(" Drum Machine")
                subject = f"a vintage {number} electronic drum machine, desktop step sequencer with period-correct pads knobs and controls, no attached piano keyboard and no acoustic drum shells"
            elif name.startswith("Grand "):
                subject = f"a complete {name.removeprefix('Grand ')} model-inspired grand piano, distinctive curved rim and proportions, raised lid with plain wood underside, strings and plate horizontally inside body, three legs and coherent pedal lyre, no invented external customization"
            else:
                subject = SHAPES.get(identity, subject)
            assets[asset_id] = dict(id=asset_id, scope="model" if special else "instrument", identity=asset_id, subject=subject, original_model=identity if special else None, family=family["name"], hint=hint, existing=asset_id in old_images)
        title = translated_names(entry)[0]
        models[model["id"]] = dict(model_id=model["id"], image_id=asset_id, category=family["name"], instrument_id=leaf, eyebrow=node["name"], tagline=hint, description=hint, visual_identity=asset_id, image_scope=assets[asset_id]["scope"], original_name=name, title_zh=title, source=lib)
    # Keep the established featured model art/wording, but all other models are
    # grouped from catalogue identity; no preset-name splitting or GM guessing.
    for model_id, item in models.items():
        if model_id in existing:
            e = existing[model_id]
            item.update({k: e[k] for k in ["image_id", "tagline", "description"]})
    output = ROOT / "resources/instruments/gallery-models.json"
    output.write_text(json.dumps(dict(schema_version=1, models=models, assets=assets), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(models=len(models), assets=len(assets), new_images=sum(not a["existing"] for a in assets.values())), ensure_ascii=False))


if __name__ == "__main__":
    main()
