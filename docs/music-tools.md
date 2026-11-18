# 本机音乐工具接口

Windows x64 的标准接口位于 `music_tools.py`。安装及转换均复用基本设置中选择的外部软件目录。设置 → 额外功能 → 音乐工具提供一键安装、单项安装及文件转换；已有权重会直接复用。

曲库接口 `MusicLibrary.catalog(persona)`、`add(source, persona)`、`get(id, persona)`、`select(id, persona)`、`selection(persona)`、`generate_midi(id, persona)` 和 `prepare_performance(id, persona)` 按人设隔离；`persona` 是当前人设文件名。内置曲目仅 Claudia 可用，其余人设初始为空，用户上传只增加到当前人设。曲库目录只提供给 AI，UI 不展示曲目列表或提供编辑入口；旧共享上传和选曲归 Claudia。全部曲库数据重置后，仅恢复 Claudia 内置曲库。

```python
import config
import external_software
import music_tools

data = config.app_data_dir()
external = external_software.manager(data)
# 也可以由设置页面选择位置；必须是已有的本机文件夹。
external.select_root(r"D:\OIT-tools")
music = music_tools.manager(data, external)

music.capabilities()              # id / name / inputs / outputs
music.status()                    # 各组件 ready、版本、路径，钢琴额外返回 weights_ready
music.status("piano")
music.install()                   # 顺序安装缺失组件，返回所有组件状态
music.install("audiveris")        # 或 musescore / piano
result = music.convert(r"D:\score.pdf", r"D:\score.mid")
print(result.to_dict())           # operation / tool / source / files
# 也可按能力调用：music.recognize(...)、music.export(...)、music.transcribe(...)
music.export_performance(r"D:\score.mscz", r"D:\performance.mid", seed=42)
music.render_performance(r"D:\performance.mid", r"D:\performance.mp3")
# seed 可省略；演奏用 MIDI 加入轻微起音、力度与松键偏差，普通 export 不加。
```

`convert(source, destination)` 按扩展名自动选择适配器。PDF、PNG、JPEG、TIFF、BMP 经 Audiveris 输出 `.mxl`，需要 `.mid` / `.midi` / `.mscz` 时继续经 MuseScore 导出；MusicXML、MXL、MSCZ、MSCX 经 MuseScore 导出 MIDI 或 MSCZ；WAV、MP3、FLAC、OGG 经钢琴模型输出 MIDI。音频转录只适用于钢琴录音，不提供演奏评分。识谱可能出错，结果应在 MuseScore 中校对；多个乐章产生多份 MXL 时明确报错，避免丢失乐章。

Partitura 是可单独安装的 MIDI 分析依赖，使用独立 Python 环境并包含 `mido`。安装探测会加载一份 MIDI 并验证音符事件；沟通页调用该环境对比两份 MIDI，也不需要额外安装 pretty_midi。

沟通页的「演奏评判」复用这些工具：左侧参考 MIDI / 乐谱图片或 PDF 转成 MIDI，右侧演奏 MIDI / 钢琴录音转成 MIDI，再由独立 worker 输出紧凑差异 JSON（音符命中、漏音 / 多音、平均起音与时值误差、最长误差和演奏时长）。评判模型只收到此摘要，不收到原始音频；评判文字再交给聊天模型。回复最早在发送后 60 秒加上演奏 MIDI 时长时显示。当前对齐按同音高最近起音匹配，结果用于练习提示，不替代专业乐谱校验。

同步方法返回结果或抛出异常。页面使用 `start_install(tool=None)` / `start_convert(source, destination)`，立即返回并在后台执行；从同一缓存 manager 读取 `state`（idle/installing/running/ready/error）、`current`、`progress`、`error`、`result`。所有音乐任务串行，执行期间锁定外部目录并阻止 Process Lasso 部署及旧权重下载入口。界面重建不会中断任务。转换在 OIT 临时目录生成结果，经验证后复制至目标盘并原子替换；失败保留原输出。

演奏导出使用 `export_performance(source, destination, seed=None)` 或后台 `start_performance(source, destination)`，输入为 MuseScore / MusicXML 乐谱，输出限 MIDI。处理器不增加依赖，保留原谱的音高、速度、踏板与鼓通道；只在组内起音、力度和松键上加入保守微差。普通格式转换、音频转录及演奏评判参考谱保持精确。曲库 AI 缓存包含算法/参数签名，旧机械版本或参数不符时重新生成；同一缓存不反复随机。数据考察、参数和局限见 [MIDI 人性化依据](research/midi-humanization.md)。

`render_performance(midi, mp3)` 使用已安装 MuseScore 渲染 MP3，沿用同一音乐任务锁和失败原子保护；结果至少验证 ID3 标签后的 MPEG 音频帧头，不接受空文件或错误文本。`MusicLibrary.prepare_performance(id, persona=None)` 先取得有效演奏 MIDI，再根据 MIDI 的 SHA-256、`MUSIC_RENDER_VERSION` 和音频校验复用 MP3；签名保存在旁边的 `.mp3.json`，避免文件时刻精度导致旧缓存误用。MuseScore 导入 MIDI 时会重新解释部分时值，音频不能保证逐事件保留全部起音与松键微差。沟通工具 `oit_music_perform` 只准备音频，正式回复显示时主线程才向 `MusicPlayback` 提交播放；`oit_music_stop` 可停止并取消待播。Windows MCI 在线程中播放并检查真实空闲/休息状态，不启动外部播放器。音频缓存和待播动作均不随聊天同步；安卓明确返回不支持。

Audiveris 5.11.0 与 MuseScore Studio 4.7.7 从官方 GitHub Release 获取 x64 MSI，统一下载器核对大小及 SHA-256，再用 MSI 行政解包部署至 `Audiveris/`、`MuseScore/`。Audiveris 自带 Java。调用命令行探测通过后才写 `oit-install.json`，该收据保存组件版本及目录内相对可执行路径。安装不改系统文件关联。两个项目均免费；其开源许可及组件许可保留在官方分发包中。

钢琴组件放在 `PianoTranscription/`，原有 164 MiB 权重沿用 Zenodo 校验。`runtime/` 中部署隔离 Python 3.11、CPU PyTorch 2.5.1 与锁定兼容的推理依赖，不使用系统 Python，不下载 CUDA。Python 来自 python.org，pip 和 setuptools 来自 PyPI，PyTorch CPU wheel 通过 OIT 分段下载器核验固定大小与 SHA-256，其余 wheel 来自 PyPI；下载和 pip 安装复用用户原有代理，避开 OIT 过滤代理。音乐工具启动隔离 Python 子进程时，OIT 仅在该进程存活期间豁免其可执行文件目录，退出即撤销。只有依赖导入自检完成后写入运行环境就绪标记。运行通过独立 `music_worker.py`，CPU 默认两个线程；识谱逐页处理。安装失败可重试，不把半成品标记为就绪。2026-11-18 大陆直连 1 字节 Range 探测中，GitHub API、python.org、PyPI、清华 PyPI 镜像、PyTorch CDN，以及 Zenodo 模型文件均可达；统一下载器直连失败会改用用户原有上游代理。

Process Lasso 保护规则同时排除 Python、torchrun、Java、Audiveris、MuseScore 与 OIT；已有 OIT 管理的 Lasso 配置会在安装音乐组件时备份后刷新。目录、环境、收据、权重和转换结果均不随设备同步。Android 保留原有 AI 语音入口，本套原生音乐工具部署仅在 Windows 提供。
