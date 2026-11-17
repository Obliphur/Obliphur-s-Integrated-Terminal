# 本机音乐工具接口

Windows x64 的标准接口位于 `music_tools.py`。安装及转换均复用基本设置中选择的外部软件目录。设置 → 额外功能 → 音乐工具提供一键安装、单项安装及文件转换；已有权重会直接复用。

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
```

`convert(source, destination)` 按扩展名自动选择适配器。PDF、PNG、JPEG、TIFF、BMP 经 Audiveris 输出 `.mxl`，需要 `.mid` / `.midi` / `.mscz` 时继续经 MuseScore 导出；MusicXML、MXL、MSCZ、MSCX 经 MuseScore 导出 MIDI 或 MSCZ；WAV、MP3、FLAC、OGG 经钢琴模型输出 MIDI。音频转录只适用于钢琴录音，不提供演奏评分。识谱可能出错，结果应在 MuseScore 中校对；多个乐章产生多份 MXL 时明确报错，避免丢失乐章。

Partitura 是可单独安装的 MIDI 分析依赖，使用独立 Python 环境并包含 `mido`。安装探测会加载一份 MIDI 并验证音符事件。它目前只提供分析运行环境，不会自动比较两份 MIDI 或生成演奏反馈；也不需要额外安装 pretty_midi。

同步方法返回结果或抛出异常。页面使用 `start_install(tool=None)` / `start_convert(source, destination)`，立即返回并在后台执行；从同一缓存 manager 读取 `state`（idle/installing/running/ready/error）、`current`、`progress`、`error`、`result`。所有音乐任务串行，执行期间锁定外部目录并阻止 Process Lasso 部署及旧权重下载入口。界面重建不会中断任务。转换在 OIT 临时目录生成结果，经验证后复制至目标盘并原子替换；失败保留原输出。

Audiveris 5.11.0 与 MuseScore Studio 4.7.7 从官方 GitHub Release 获取 x64 MSI，统一下载器核对大小及 SHA-256，再用 MSI 行政解包部署至 `Audiveris/`、`MuseScore/`。Audiveris 自带 Java。调用命令行探测通过后才写 `oit-install.json`，该收据保存组件版本及目录内相对可执行路径。安装不改系统文件关联。两个项目均免费；其开源许可及组件许可保留在官方分发包中。

钢琴组件放在 `PianoTranscription/`，原有 164 MiB 权重沿用 Zenodo 校验。`runtime/` 中部署隔离 Python 3.11、CPU PyTorch 2.5.1 与锁定兼容的推理依赖，不使用系统 Python，不下载 CUDA。Python 来自 python.org，pip 和 setuptools 来自 PyPI，PyTorch CPU wheel 通过 OIT 分段下载器核验固定大小与 SHA-256，其余 wheel 来自 PyPI；下载和 pip 安装复用用户原有代理，避开 OIT 过滤代理。只有依赖导入自检完成后写入运行环境就绪标记。运行通过独立 `music_worker.py`，CPU 默认两个线程；识谱逐页处理。安装失败可重试，不把半成品标记为就绪。

Process Lasso 保护规则同时排除 Python、torchrun、Java、Audiveris、MuseScore 与 OIT；已有 OIT 管理的 Lasso 配置会在安装音乐组件时备份后刷新。目录、环境、收据、权重和转换结果均不随设备同步。Android 保留原有 AI 语音入口，本套原生音乐工具部署仅在 Windows 提供。
