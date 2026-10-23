# Ample 加载就绪诊断

## LP 4.0.0 对象映射修正（2026-10-23 后续）

LP原构造器`0x1804e7290`的保留汇编明确区分四个同名虚表，不能只按符号字符串选择地址：

| processor相对偏移 | 实际虚表 | LEA / MOV指令地址 |
| --- | --- | --- |
| +0x0 | 0x1851f90d0 | 0x1804e7507 / 0x1804e750e |
| +0x210 | 0x1851f8a90 | 0x1804e7512 / 0x1804e7519 |
| +0x370 | 0x1851f9140 | 0x1804e7521 / 0x1804e7528 |
| +0x378 | 0x1851f8770 | 0x1804e7530 / 0x1804e7537 |

此前拿最后一个表当作+0x210的AudioProcessor表，才产生了错误的210/378冲突。正确的被动路径为：核验精确LP模块和IComponent wrapper表 → 读取wrapper+0x38的AudioProcessor指针 → 核验实际次表为模块基址+0x51f8a90 → 减0x210 → 核验实际主表为模块基址+0x51f90d0。不能直接从wrapper减0x210。所有读取仍须在自有实例和可读内存预算内；不匹配时不读loader字段。

纠正记录在`aglp-loading-readiness-rea-01/component-primary-mapping-correction-01.json`，SHA `ce12d5f658bb52c1238d6a8625e4907b6daf65dadad107b088a80e5e6a9e669d`，绑定原snapshot和constructor证据`ev_f25fa79957bdaa609e26d9687db119201dd572cd99dc1b908c068f73ce5cdd2d`。这只修正静态映射结论，实际loader值、就绪和发声仍需新独立运行确认。旧unsupported诊断与原分析文件保留。

## AGM 3.1.0 原始静态观测

2026-10-23 的分析只针对已安装的 AGM 3.1.0：`C:\Program Files\Common Files\VST3\AGM.vst3`，SHA-256 `9f8d18d346f00532015dac386227f8a8b8dca6e8312876f352c152b7b148962b`。本次使用 REA/Ghidra 静态分析，没有执行插件、加载样本或读写 H；原模块前后 SHA 相同，分析会话已关闭。它提供后续隔离诊断的观测点，不代表静音问题已修好，也不能套用 LP/SC 的不同版本内存布局。

证据、分析快照和精简结论分别保存在 `data/maintenance/instrument-intake/20261023-ample-shared-fix/agm-loading-readiness-rea-01/{evidence-bundle.json,analysis-snapshot.json,findings.json}`；下列地址都是该固定模块的静态分析地址，运行时须按实测加载基址换算，且先验证实际接口和对象。

| 观测 | 位置 | REA Evidence |
|---|---|---|
| `Agm2AudioProcessor` 构造名为 Loading Thread 的 JUCE Thread 子对象，偏移 processor +0x110 | `0x1802b2660` | `ev_67047216199cdb8f6d98be386fa0abcdd8e074497317fb70b33a8c0d04854534` |
| 该线程虚表 slot +8 指向加载函数 | `0x181493e30` → `0x1802b0970` | `ev_8b4037a3eadb42e3e342555a5653a3e8ae3b1aa93869e00985d96feeef8aac41` |
| 加载函数读取 InstDir 和 `_DBConfig.cf`；累积错误为 0 时才写入 Success | `0x1802b0970` | `ev_adae3874357f77c411832867aa5c48f2c1af7e538f240fe2984aba7915f27bc6` |
| 错误总数写在 thread this +0xb048（processor +0xb158）；状态字符串在 thread this +0x110（processor +0x220） | 同上 | 同上 |
| UI 在 processor +0x120 为 0 时比较 processor +0x220 的状态字符串，非 Success 则展示采样加载错误 | `0x1802d71d0` | `ev_8c007c4457d75dedc66b3da1b72af4c450679129af976a939f5a7b72d7ffd6f2` |
| JUCE Thread 构造器将 this +0x10 和 +0x18 置 0；具体运行句柄语义仍需实际转态佐证 | `0x180186360` | `ev_c48f3fce6ed01617f468f00bdb3e27fdc7012193fa3175f8680795b25b3f5f21` |

后续只在独立、已固定 SHA 的自有宿主中补充只读观测：核验实际 VST3 component 接口到 processor 的映射，记录加载前、触发前和处理后的加载线程/状态变化。不同二进制版本必须独立确认布局，不从 AGM 推测 LP/SC 的地址。字段不得修改；Success 或线程结束都不能代替首开、空机架恢复、完整身份/状态、非静音和无削波检查。若加载失败，应保留原始错误码并继续排查资源路径；若加载成功但触发前后存在时序差异，再用相同原始乐句做有界控制，不用任意睡眠或静音放行掩盖原因。
