"""
录像的基础常量 (MECHANICS 18.8): 文件后缀 / 加密头 / 分段与画质参数 / 清理期限.

被 recorder.py (录像线程), recorder_store.py (存档 / 加密 / 清理),
recorder_media.py (Media Foundation 封装), recorder_merge.py (合并) 共用.
由 recorder.py 重新导出, 别处照旧写 recorder.KINDS / recorder.STILL_EVERY.
"""

import hashlib

KINDS = ("camera", "screen")
PART = ".recording"
EXT = ".oitv"                     # 加密后的录像
MAGIC = b"OITV\x01"
REC_KEY = hashlib.sha256(b"obliphur-terminal/recording/v1").digest()   # 所有 OIT 共用 (简易加密: 只防自动上传)
SEGMENT = 30 * 60                 # 每个文件最长 30 分钟 (30 分钟 = 60 张)
FOCUS_EVERY = 20                  # 刻痕: 每这么多秒判定一次焦点设备 (MECHANICS 18.8)
STILL_EVERY = 30                  # 每这么多秒拍一张照片 / 截一张图
STILL_STEP = 2_000_000            # 照片接成视频后每张放这么久 (100ns): 0.2 秒
SCREEN_FPS = 5                    # 视频的帧率标记 (= 1 / STILL_STEP)
SCREEN_WIDTH = 960               # 画面缩到最宽这么多 (压一下画质 / 体积; 合并画布也按它封顶)
SCREEN_BITRATE = 2_000_000        # 每张 0.2 秒 → 约 50 KB 一张
CAMERA_FPS = 5
CAMERA_BITRATE = 1_000_000        # 每张 0.2 秒 → 约 25 KB 一张 (传给别的设备合并 / 同步时不会太大)
CAMERA_SIZE = (480, 360)
CAMERA_STALL = 12                 # 摄像头这么多秒没有新画面: 视为被关掉了

MAX_AGE_DAYS = 3                  # 录像超过这么多天自动清除 (MECHANICS 18.8)
PARTIAL_AGE_DAYS = 1              # 没写完的 (.recording.oitv, 合并时不用) 留这么久
SETTLE_HOURS = 24                 # 合并好之后, 用掉的片段再留这么久 (有片段迟到时重合并要用), 之后清掉
CONSUMED = ".consumed"            # merged/<kind>/<合约 id>.consumed: 合并用掉的片段的文件名 (JSON 列表)
PURGE_EXT = (EXT, ".mp4", ".tmp", PART)

_CHUNK = 1 << 20                  # 加密 / 解密一次读多少
