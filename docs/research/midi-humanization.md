# 演奏 MIDI 的首版人性化依据

2026-11-18。目标是减少钢琴 MIDI 机械感，保持乐谱的音高、音数、速度、节拍、音色和踏板。这里只实现受控微差，没有训练演奏模型，也没有推断乐句、旋律声部或情绪。

## 数据与文献

[Goebl (2001), Melody lead in piano performance: Expressive device or artifact?](https://doi.org/10.1121/1.1376133) 对 22 位熟练钢琴家的肖邦演奏进行了分析。摘要报告旋律声部约提前 30 ms，但这一现象在手指接触琴键的层面几乎消失，支持击键力度和琴槌运动造成时间差的解释。因此，本实现不把最高音自动判作旋律，也不给它固定的 30 ms 提前。

[Peter et al. (2023), Automatic Note-Level Score-to-Performance Alignments in the ASAP Dataset](https://doi.org/10.5334/tismir.149) 提供了逐音对齐的古典钢琴演奏。考察使用 [(n)ASAP](https://github.com/CPJKU/asap-dataset/tree/4097b45757bed854818cf87e77b92323ebf90615)，固定提交 `4097b45757bed854818cf87e77b92323ebf90615`，许可 CC BY-NC-SA 4.0。仓库保留方法与统计摘要，不分发下载的演奏文件。

可复现命令：`python -X utf8 docs/research/midi_humanize_study.py`。脚本沿用 `updater.download()`，原始 CSV 只写入 OIT 临时会话，结束即删除。按照 metadata.csv 顺序，对 Bach、Beethoven、Chopin、Debussy、Schumann 各取最多两个不同曲名、`robust_note_alignment=1.0` 的演奏；Debussy 仅找到一个符合条件的不同曲名，共 9 段。这是便利样本，作曲家与曲目并不均衡。

按 align.csv 的匹配项连接 part.csv 与 ppart.csv，只统计谱面时值大于零、同一起拍至少两个已匹配音的组。起音跨度为组内最晚起音减最早起音；力度偏差是各音 MIDI velocity 与组内中位数差的绝对值。统计使用线性插值分位数。

| 演奏（相对数据集路径） | 组数 | 起音跨度中位数/ms | P90/ms |
| --- | ---: | ---: | ---: |
| Bach/Fugue/bwv_846/Shi05M.mid | 213 | 13.020 | 29.682 |
| Bach/Fugue/bwv_848/Denisova06M.mid | 521 | 7.812 | 22.132 |
| Beethoven/Piano_Sonatas/1-1/KimG01.mid | 420 | 10.680 | 22.843 |
| Beethoven/Piano_Sonatas/10-1/Hou02M.mid | 540 | 15.630 | 41.809 |
| Chopin/Ballades/1/Ali01.mid | 1,180 | 21.360 | 76.262 |
| Chopin/Ballades/3/Ko11M.mid | 969 | 19.270 | 57.904 |
| Debussy/Images_Book_1/1_Reflets_dans_lEau/Kleisen11M.mid | 300 | 26.046 | 73.041 |
| Schumann/Arabeske/Min09M.mid | 558 | 26.046 | 136.720 |
| Schumann/Kreisleriana/1/ParkJH04M.mid | 251 | 16.927 | 127.600 |

合计 4,952 组，跨度中位数 16.929 ms、P90 68.071 ms；其中 330 组超过 100 ms。仅观察不超过 100 ms 的 4,622 组时，中位数为 15.625 ms、P90 44.079 ms。全部组的音符力度绝对偏差中位数为 5.0。后者包含配器/声部强调，不等于随机力度噪声；长时间差也可能来自有意琶音、装饰音与对齐错误。不能将这些数据直接拟合为独立白噪声，100 ms 筛选也只是额外统计，不用于删除演奏事件。

## 实现选择

使用 `music_humanize.py`，纯标准库解析格式 0/1 的 PPQ MIDI，跨所有轨道建立 tempo 映射，再按毫秒处理。默认值在 `config.py`，均属于可调整的工程初值，未宣称从该样本估计出统计模型：

- 共同时间偏移的随机节点范围为 ±12 ms，每 8 个四分音符平滑插值；这只是缓慢变化，不模拟乐句边界，也不累计随机游走或改写 tempo。
- 同一谱面起音组共享的偏差标准差为 3 ms，各音的指间偏差标准差为 5 ms，均截断在 ±2σ。总偏移还受相邻起音组间隔的 20% 限制，保护快速音群及已有琶音。
- 原 MIDI velocity 上增加组内共用和逐音偏差，标准差分别为 1 和 2，均截断在 ±2σ，最终夹在 1–127。保留原始强弱，不固定强调最高音。
- 松键偏差标准差为 8 ms，截断在 ±16 ms，并限制在原时值的 15% 内。必要时进一步缩短，以避免新引入同通道同音高的重触重叠。MIDI tick 分辨率造成的量化误差仍然存在。

保留所有音高、鼓通道、控制器（含 CC64）、音色、tempo、节拍与 sysex；谱面零时刻的负偏移夹到零。MuseScore 会写出辅助通道的无起音复位松键事件，原样保留；零 tick 装饰音扩展到至少一个 tick，避免松键排在起音前。没有随机错音、漏音、增加音符或自动踏板。格式 2、SMPTE、截断文件、未结束的起音等明确报错；失败不替换原输出。

仅 `export_performance()`、`start_performance()`、曲库 AI 生成和「导出所选曲目为 MIDI」使用此过程，普通 `export()`/`convert()` 及演奏评判参考谱不加入扰动。`seed` 可用于可复现验证，默认每次新生成独立随机；缓存复用时演奏不再变化。MIDI 含算法版本与参数签名，旧缓存/参数变化后重新导出，不反复叠加扰动。安卓当前仍不生成 MIDI；最终 MP3 渲染尚待实现。

## 验证记录

`checks/_music_humanize_check.py` 覆盖多个 BPM/PPQ、跨轨变速、力度边界、快速同音重触、零 tick 装饰音、running status、复位松键、鼓通道、踏板、sysex、损坏输入和失败时保留旧输出；`_music_library_check.py` 覆盖旧缓存/参数变化后的重新生成。默认 20 首已分别由本机 MuseScore 4.7.7 实际导出并经过处理，再用 Partitura 独立环境中的 `mido` 解析，20 首均保持起音数量和轨道数量。

本机 Android 构建环境部署了微软 OpenJDK 17.0.21.0.2（官方 ZIP 校验 SHA-256 `49154292866386b2f4f09a3ecf3be07ab80a419149c84ec1d4419b9eec9e4b64`），配置用户级 `JAVA_HOME`/`PATH`。`:app:assembleDebug` 和 `ToolProtocolTest` 5 项通过；全量单元测试 191 项有 15 项 `CommTest` 失败，包含旧提示词、旧控制标记等断言，不能把全量检查记作通过。本任务不批量改写这些既有测试。
