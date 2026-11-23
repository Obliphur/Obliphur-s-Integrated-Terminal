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
music.status()                    # 各组件 ready、版本和路径
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

`convert(source, destination)` 按扩展名自动选择适配器。PDF、PNG、JPEG、TIFF、BMP 经 Audiveris 输出 `.mxl`，需要 `.mid` / `.midi` / `.mscz` 时继续经 MuseScore 导出；MusicXML、MXL、MSCZ、MSCX 经 MuseScore 导出 MIDI 或 MSCZ；WAV、MP3、FLAC、OGG 经 Spotify Basic Pitch 输出 MIDI。Basic Pitch 是 Apache 2.0 开源模型，音频转录适用于钢琴录音及以单一乐器为主的音频；不提供演奏评分。转录可能出错，结果应在 MuseScore 中校对；多个乐章产生多份 MXL 时明确报错，避免丢失乐章。

Partitura 是可单独安装的 MIDI 分析依赖，使用独立 Python 环境并包含 `mido`。安装探测会加载一份 MIDI 并验证音符事件；沟通页调用该环境对比两份 MIDI，也不需要额外安装 pretty_midi。

沟通页的「演奏评判」复用这些工具。Windows 左侧选参考 MIDI、乐谱图片或 PDF，右侧选演奏 MIDI、钢琴录音或「录制 MIDI」；通过系统配对蓝牙 MIDI 乐器后，OIT 使用 Windows MIDI (WinRT) 枚举并打开 BLE MIDI 端口，WinMM 驱动端口仍可照常使用。安卓支持参考 MIDI、MusicXML / MXL 或当前选曲，演奏 MIDI、音频、USB MIDI 与蓝牙 MIDI；Android 6.0 及以上可扫描并连接 BLE MIDI。安卓不做图片/PDF 识谱，上传的 MuseScore 文件须先在电脑转换，AI 可自行搜索乐谱。首次使用安卓演奏功能前，从设置 → 额外功能下载按 ABI 区分的可选资源包，包含 FluidSynth 原生库、Verovio、Basic Pitch 模型、钢琴音色与内置曲库 MIDI；ONNX Runtime 随 APK 提供。录制从第一个有力度的按键开始计时，停止后把录制结果用作本次演奏。Windows 评判需要 Partitura，安卓使用 Kotlin MIDI 分析。

独立 worker 将参考与演奏各自的首音作为零点，再按同音高最近起音匹配，输出音符命中、漏音、多音、起音与时值误差以及演奏时长。首音对齐保留了曲中的停顿，适用于从相同位置开始的演奏。评判模型只接收差异摘要，聊天模型接收摘要和评判文字。模型只在误差模式连贯且能与偶发失误区分时提及可能的演奏意图。回复最早在发送后 60 秒加上演奏时长时显示。

同步方法返回结果或抛出异常。页面使用 `start_install(tool=None)` / `start_convert(source, destination)`，立即返回并在后台执行；从同一缓存 manager 读取 `state`（idle/installing/running/ready/error）、`current`、`progress`、`error`、`result`。所有音乐任务串行，执行期间锁定外部目录并阻止 Process Lasso 部署及旧权重下载入口。界面重建不会中断任务。转换在 OIT 临时目录生成结果，经验证后复制至目标盘并原子替换；失败保留原输出。

演奏导出使用 `export_performance(source, destination, seed=None)` 或后台 `start_performance(source, destination)`，输入为 MuseScore / MusicXML 乐谱，输出限 MIDI。处理器不增加依赖，保留原谱的音高、速度、踏板与鼓通道；只在组内起音、力度和松键上加入保守微差。普通格式转换、音频转录及演奏评判参考谱保持精确。曲库 AI 缓存包含算法/参数签名，旧机械版本或参数不符时重新生成；同一缓存不反复随机。数据考察、参数和局限见 [MIDI 人性化依据](research/midi-humanization.md)。

`render_performance(midi, mp3)` 使用已安装 MuseScore 渲染 MP3，沿用同一音乐任务锁和失败原子保护；结果至少验证 ID3 标签后的 MPEG 音频帧头，不接受空文件或错误文本。`MusicLibrary.prepare_performance(id, persona=None)` 先取得有效演奏 MIDI，再根据 MIDI 的 SHA-256、`MUSIC_RENDER_VERSION` 和音频校验复用 MP3；签名保存在旁边的 `.mp3.json`，避免文件时刻精度导致旧缓存误用。MuseScore 导入 MIDI 时会重新解释部分时值，音频不能保证逐事件保留全部起音与松键微差。沟通工具 `oit_music_perform` 只准备音频，正式回复显示时主线程才向 `MusicPlayback` 提交播放；`oit_music_stop` 可停止并取消待播。Windows MCI 在线程中播放并检查真实空闲/休息状态，不启动外部播放器；Android 使用 MediaPlayer 播放本机 FluidSynth 渲染的音频。安卓资源按 ABI 从 GitHub Release 的可选音乐资源附件下载，通过统一 Android Updater 校验附件和包内文件 SHA-256 后原子安装；APK 本体不包含 FluidSynth、Verovio、Basic Pitch 模型、钢琴音色或曲库 MIDI，ONNX Runtime 的 Android JNI 库仍随 APK 提供。音频缓存和待播动作均不随聊天同步。

Audiveris 5.11.0 与 MuseScore Studio 4.7.7 从官方 GitHub Release 获取 x64 MSI，统一下载器核对大小及 SHA-256，再用 MSI 行政解包部署至 `Audiveris/`、`MuseScore/`。Audiveris 自带 Java。调用命令行探测通过后才写 `oit-install.json`，该收据保存组件版本及目录内相对可执行路径。安装不改系统文件关联。两个项目均免费；其开源许可及组件许可保留在官方分发包中。

钢琴转录组件安装到 `BasicPitch/`，部署独立 Python 3.11 环境和指定版本的 Spotify Basic Pitch、ONNX Runtime CPU 及运行依赖；其中固定安装 setuptools，为 resampy 提供 `pkg_resources`。使用 Basic Pitch 随包提供的模型，不另行下载权重，不安装 PyTorch 或 CUDA，也不使用系统 Python。Python 来自 python.org，pip 与组件来自 PyPI；下载和 pip 安装复用用户原有代理，避开 OIT 过滤代理。Windows 使用 ONNX 推理，因此安装时跳过 Basic Pitch 在 Python 3.11 上声明的可选 TensorFlow 依赖。音乐工具启动隔离 Python 子进程时，OIT 仅在该进程存活期间豁免其可执行文件目录，退出即撤销。只有依赖导入（包括 `pkg_resources`）、模型加载和 CPU provider 检查通过后才写入就绪标记。运行通过独立 `music_worker.py`；安装失败可重试，不把半成品标记为就绪。运行依赖修订变化会使旧环境失效并在下一次安装时更新；旧版 `PianoTranscription/` 文件不会被新版本读取或自动删除。

Process Lasso 保护规则同时排除 Python、torchrun、Java、Audiveris、MuseScore 与 OIT；已有 OIT 管理的 Lasso 配置会在安装音乐组件时备份后刷新。目录、环境、收据、权重和转换结果均不随设备同步。Android 保留原有 AI 语音入口，本套原生音乐工具部署仅在 Windows 提供。
