# 乐器展示资源

每个型号关联一张展示图，普通乐器可复用同类图，具有明确特色的型号使用对应视觉形象。当前 203 张注册图为内置 image_gen 工具生成的展示插图，不是实际录音乐器的摄影或精确硬件结构图。透明背景用于深色界面。资源全部复制进当前工作树，运行时不依赖生成目录或外部网站。全量型号与图片映射在 gallery-models.json，首屏精选顺序在 gallery.json，完整性在 images.json。未采用的实拍候选和研究记录留在本机维护目录，不随产品分发。

## 配图规则

- 普通乐器：同类、同形制可以复用一张图，例如通用长笛、小提琴、古典吉他。不同形制仍分开，例如木吉他与电吉他、立式钢琴与三角钢琴。`media.image_scope` 为 `instrument`。
- 特别型号：具体品牌型号、定制版本、古董或具有独特构造的乐器，按真实差异设计形象，不能把同一张通用图仅换名称、背景或滤镜。`media.image_scope` 为 `model`。
- 差异优先来自乐器本身的轮廓、结构、材质、饰面、控制面板和年代特征；再用构图、视角、细节与光线表达气质。外形相近的钢琴也应表现各自的辨识点，同时保持整座乐器库的统一质感。找不到确切外观时，使用明确的展示插图，不虚构实物结构或历史摄影身份。
- `media.image_identity` 表示这张图所表现的乐器类别或特别型号。相同视觉身份可以跨插件来源复用；不同特别型号不共用同一张通用图。明确品牌型号后，应先判断是否需要从通用图升级为专属图。
- 同一型号的 Warm、Bright、Cinematic 等音色预设共用该型号图，不按预设重复生成图片。未来模型外观资源仍按这个视觉身份管理。

目前 LA Custom C7、Rhodes Mark I、Wurlitzer 200A、Hohner Clavinet C 标为型号专属；其余通用乐器图允许后续在对应类别复用。这是资源整理规则，不增加打开插件或切换预设时的检查。

## 中文显示

乐器卡片与型号页的中文名称从共享音源目录的 `instrument_localization.translated_names` 读取，预设选择使用目录的 `display_variant_name`（包含已审订的展示译名），不以配图清单的静态标题替代。英文原名作为辅助信息保留，Keyscape、Pianoteq、Kontakt 等产品名保持英文；个人预设保留用户命名。配图类别标签和界面标题使用中文。

## 第二版型号图

LA Custom C7 和 Wurlitzer 200A 使用 `*-illustration-v2.png`，通过内置 `image_gen` 编辑生成，完整输入提示词与参考图记录在 [prompts-v2.json](prompts-v2.json)。第一版文件保留用于版本追溯；产品映射以 `images.json` 为准。

- Wurlitzer 200A：改正原图过于接近 Rhodes 的外形，表现紧凑圆角塑料机身、内置扬声器格栅、旋钮和独立细腿。参考 [Rhodes 官方 Wurlitzer 页面](https://rhodesmusic.com/wurlitzer-by-rhodes-plugin/)。
- LA Custom C7：调整长尾轮廓、视角、木质与黑漆质感，修正上盖与琴体内部的结构关系。定制特点主要在内部整音，不以虚构外观装饰表达；参考 [Spectrasonics 官方型号介绍](https://www.spectrasonics.net/products/keyscape/models/la-custom-c7-grand-piano/)。

二者均为带 alpha 的 1536 × 1024 展示插图，运行时直接使用本地资源，不增加插件加载或预设切换检查。画廊图片 URL 带清单中的 SHA 版本，更新后不会继续命中旧图缓存。

## 提示词

共同提示词：

Use case: product-mockup. Asset type: one instrument image for an elegant virtual instrument library desktop application. Primary request: [以下型号主题]. Photorealistic premium catalog product photograph, physically believable construction and proportions, beautifully rendered fine material details. Entire instrument isolated with comfortable 12 percent empty margin on all sides, centered composition, consistent warm softbox key light from upper left and subtle cool rim light from right, realistic understated self-shadow. Transparent background, no floor, no room, no people, no accessories unrelated to the instrument, no text, no label, no watermark, no collage. Landscape 3:2 image. This is one standalone product-style instrument illustration, not an actual historical photo.

### la-custom-c7

a full black lacquer Yamaha C7 style concert grand piano, lid open on its prop, 88-key keyboard visible, three legs, brass pedals, three-quarter front-left view

### rhodes-mark-i

a complete vintage Rhodes Mark I Suitcase electric piano, black rounded textured top with silver control strip and black suitcase speaker cabinet underneath, realistic 73-key keyboard, three-quarter front-left view

### wurlitzer-200a

a complete vintage Wurlitzer 200A electric piano, black dark charcoal rounded plastic body, 64 cream and black keys, four thin chrome legs and sustain pedal, three-quarter front-left view

### clavinet-c

a complete vintage Hohner Clavinet C keyboard, long slim cream-white case with reddish walnut wood trim, 60 black and white keys and small dark control panel on the left, three-quarter front-left view

### classical-guitar

one elegant classical nylon-string guitar with warm cedar soundboard, dark rosewood sides, traditional rosette, six nylon strings, slotted headstock with side-facing tuning pegs; entire instrument visible diagonally in a tasteful product composition, no steel-string acoustic guitar

### celesta

a complete upright wooden celesta keyboard instrument, dark walnut rectangular small cabinet with open lower front showing metal resonator tubes, 4 octave piano-style keyboard, delicate feet and pedal, three-quarter front-left view

### flute

one complete silver concert transverse flute with realistic polished silver keywork, straight body, side embouchure plate, entire flute elegantly diagonal across composition, no stand

### violins

one fine orchestral violin of warm amber varnished wood, accurate four strings, f-holes, bridge, ebony fingerboard and chinrest, entire violin visible diagonally, elegant restrained product composition

### electric-bass

one vintage four-string Precision-style electric bass guitar, warm sunburst body, tortoiseshell pickguard, maple neck, four tuning pegs, entire instrument visible diagonally, no amp

### analog-machines

one premium vintage analog monosynth keyboard, walnut wooden side panels, dark sloped panel with three oscillator sections of silver-black knobs, 44 piano keys, handsome original generic analog synth design with no branding, three-quarter front-left view



## 全量扩展

本轮通过内置 image_gen 生成 193 张透明展示插图：83 张普通乐器类别图、110 张特殊型号图。加上前版十张，共 203 个图片身份；历史 C7、Wurlitzer 第一版仍保留，实际文件共 205 张。完整提示词见 [普通乐器](prompts-expansion-ordinary.json) 与 [特殊型号](prompts-expansion-models.json)。全部资源随本地应用分发，浏览与换预设不依赖联网生成或检查原始生成目录。

同型号的不同奏法、贝斯／铺底等预设仍共用型号图；例如 SK-1、VSS-30 保持电子键盘外形，不按某一个预设的音色角色改画另一件乐器。历史方钢琴、羽管键琴、手碟、鼓机等按各自形制组织。跨插件来源的同一型号可复用同一视觉身份。

这些是按型号特征制作的插图，不是厂商实拍，也不作为精确机械结构资料。全部 203 张已通过本地解码、SHA 与 alpha 格式核对，并完成九页全量缩略图目视检查。
