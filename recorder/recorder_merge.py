"""
录像的合并 (MECHANICS 18.8 刻痕): 把各设备 / 各段录屏接成一个视频.

画面变换 (转正 / 缩放 / 画布) 是纯 bytes 运算; 解码 / 编码用 recorder_media 的
Media Foundation 封装; 片段和最终录像的存放用 recorder_store. 由 recorder.py 重新导出.
"""

import array
import ctypes
import os
import shutil
import tempfile
from ctypes import byref
from datetime import datetime

from recorder.recorder_common import (
    CAMERA_BITRATE, EXT, SCREEN_BITRATE, SCREEN_FPS, SCREEN_WIDTH, STILL_STEP,
)
from recorder.recorder_media import (
    HRESULT, MFError, MFMediaType_Video, MFVideoFormat_RGB32, MF_MT_DEFAULT_STRIDE, MF_MT_FRAME_SIZE,
    MF_SOURCE_READER_ALL_STREAMS, MF_SOURCE_READER_ENABLE_VIDEO_PROCESSING, MF_SOURCE_READERF_ENDOFSTREAM,
    MF_SOURCE_READER_FIRST_VIDEO_STREAM, _attributes, _check, _com, _dims, _find_streams, _get_u32, _get_u64,
    _reader_type, _release, _sample_bytes, _set_u32, _shutdown, _startup, _video_rotation, _Writer,
)
from recorder.recorder_store import (
    _remove, _safe_cid, cid_of, decrypt_file, encrypt_file, files_of, incoming_dir, merged_dir,
    read_consumed, work_dir, write_consumed,
)


# ---------------------------------------------------------------- 画面变换 (RGB32)

def _topdown(src: bytes, sw, sh, stride) -> bytes:
    """RGB32 (sw×sh, 行距 stride 可正负, 可能带行尾填充) → 紧凑、从上往下的 bytes."""
    astride, row = abs(stride), sw * 4
    if stride == row:
        return bytes(src[:row * sh])
    rows = [src[y * astride: y * astride + row] for y in range(sh)]
    if stride < 0:
        rows.reverse()
    return b"".join(rows)


def _rotate(src: bytes, sw, sh, stride, deg: int) -> tuple:
    """RGB32 源 (sw×sh, 行距 stride 可正负) 顺时针转 deg (90 / 180 / 270) 度.
    返回 (新 bytes, 新宽, 新高, 新行距, 从上往下). 按 4 字节一个像素整列切片, 不逐像素循环 (几小时的录像也够快)."""
    a = array.array("I")
    a.frombytes(_topdown(src, sw, sh, stride))
    deg %= 360
    if deg == 180:
        a.reverse()
        return a.tobytes(), sw, sh, sw * 4
    out = array.array("I")
    if deg == 90:                       # 新的第 ny 行 = 原来的第 ny 列, 从下往上
        for x in range(sw):
            out.extend(a[(sh - 1) * sw + x::-sw])
    elif deg == 270:                    # 新的第 ny 行 = 原来的倒数第 ny 列, 从上往下
        for x in range(sw - 1, -1, -1):
            out.extend(a[x::sw])
    else:
        return a.tobytes(), sw, sh, sw * 4
    return out.tobytes(), sh, sw, sh * 4


def _rotate90(src: bytes, sw, sh, stride) -> tuple:
    """RGB32 源 (sw×sh, 行距 stride 可正负) 顺时针转 90°: 返回 (新 bytes, 新宽=sh, 新高=sw, 新行距=sh*4, 从上往下).
    只在"参与的全是竖屏设备、却冒出一帧横屏画面"时用 (MECHANICS 18.8), 把横屏画面转正."""
    return _rotate(src, sw, sh, stride, 90)


def _fit(src: bytes, sw, sh, stride, dw, dh) -> bytes:
    """一帧 RGB32 (源 sw×sh, 行距 stride 字节, 可正可负) 等比缩放后放进 dw×dh 的画布 (从上往下):
    保持宽高比, 居中, 周围补黑 (横屏画布放竖屏内容时左右留黑, 反之上下留黑). 用最近邻缩放 (录屏 1 fps, 够用)."""
    astride = abs(stride)
    row = dw * 4
    out = bytearray(row * dh)                       # 全黑画布
    if sw <= 0 or sh <= 0:
        return bytes(out)
    scale = min(dw / sw, dh / sh)                   # 等比缩放到刚好放进画布 (contain)
    iw = max(2, min(dw, int(sw * scale)) & ~1)      # 内容区大小 (取偶)
    ih = max(2, min(dh, int(sh * scale)) & ~1)
    ox, oy = (dw - iw) // 2, (dh - ih) // 2         # 居中
    same_w = (iw == sw)
    xmap = None if same_w else [min(sw - 1, x * sw // iw) * 4 for x in range(iw)]
    for dyi in range(ih):
        sy = min(sh - 1, dyi * sh // ih)
        soff = (sy if stride > 0 else (sh - 1 - sy)) * astride
        scaled = src[soff: soff + iw * 4] if same_w else b"".join(src[soff + xb: soff + xb + 4] for xb in xmap)
        dy = oy + dyi
        out[dy * row + ox * 4: dy * row + ox * 4 + len(scaled)] = scaled
    return bytes(out)


def _canvas(info, weights=None):
    """合并画布的大小和朝向 (MECHANICS 18.8). info: [(宽, 高, 是不是本机录的)].
    weights: 每段的分量 (目光按录了多久, 用文件大小代替); 给了就按横 / 竖各录了多久定朝向, 不按段数.
    - 有横屏设备参与就按横屏: 本机 (电脑, 横屏设备) 录过, 或片段里横屏占多数; 否则 (全是竖屏手机) 按竖屏.
    - 画布取同朝向里面积最大的那段 (保持分辨率不变), 最长边再压到不超过 SCREEN_WIDTH (压一下体积).
    - 不同朝向的内容: 横屏画布里的竖屏内容左右留黑; 竖屏画布里的横屏画面直接转正 (见 _reencode_concat / _rotate90)."""
    if weights is None:
        weights = [1] * len(info)
    pairs = [(d, wt) for d, wt in zip(info, weights) if d[0] > 0 and d[1] > 0]
    info = [d for d, _ in pairs]
    if not info:
        return None
    n_land = sum(wt for (w, h, _), wt in pairs if w > h)
    n_port = sum(wt for (w, h, _), wt in pairs if w <= h)
    has_local = any(loc for _, _, loc in info)
    land = has_local or (n_land > 0 and n_land >= n_port)
    same = [(w, h) for w, h, _ in info if (w > h) == land] or [(w, h) for w, h, _ in info]
    w, h = max(same, key=lambda d: d[0] * d[1])
    m = max(w, h)
    if m > SCREEN_WIDTH:
        w, h = round(w * SCREEN_WIDTH / m), round(h * SCREEN_WIDTH / m)
    return max(2, w // 2 * 2), max(2, h // 2 * 2)


# ---------------------------------------------------------------- 重编码并接起来

def _reencode_concat(out_path, inputs, fps=SCREEN_FPS, size=None, bitrate=SCREEN_BITRATE, fit_turn=True):
    """把几个 mp4 (已解密) 按顺序解码 → 重新编码接成一个 (画面统一到 size 给的画布, 没给时用第一段的大小;
    每一段等比缩放居中留黑, 不同朝向也兼容). 只接画面 (旧版本录的声音丢掉).
    照片的时间戳不管原来是什么 (手机的片段里是真实的间隔 30 秒), 按顺序每张放 STILL_STEP (0.2 秒). 返回输出的 (宽, 高)."""
    lib, own = _startup()
    writer = None
    dw, dh = (size or (0, 0))
    try:
        n_frames = 0                                # 已经写了几张
        for path in inputs:
            reader = ctypes.c_void_p()
            attrs = _attributes(lib, 1)
            try:
                _set_u32(attrs, MF_SOURCE_READER_ENABLE_VIDEO_PROCESSING, 1)
                _check(lib.rw.MFCreateSourceReaderFromURL(ctypes.c_wchar_p(path), attrs, byref(reader)), "open")
            finally:
                _release(attrs)
            vcur = None
            try:
                vidx, _aidx = _find_streams(reader)
                if vidx < 0:
                    continue
                vcur = _reader_type(lib, reader, MF_SOURCE_READER_FIRST_VIDEO_STREAM, MFMediaType_Video,
                                    MFVideoFormat_RGB32)
                fs = _get_u64(vcur, MF_MT_FRAME_SIZE)
                sw, sh = fs >> 32, fs & 0xFFFFFFFF
                try:
                    stride = ctypes.c_int(_get_u32(vcur, MF_MT_DEFAULT_STRIDE)).value
                except MFError:
                    stride = sw * 4
                if not stride:
                    stride = sw * 4
                turn = _video_rotation(reader)              # 手机按陀螺仪写的旋转标记: 先把画面转正
                ew, eh = (sh, sw) if turn in (90, 270) else (sw, sh)
                if writer is None:
                    if not (dw and dh):                     # 没给画布: 用第一段 (转正后) 的大小 (同以前)
                        dw, dh = min(ew, SCREEN_WIDTH) // 2 * 2, eh // 2 * 2
                    writer = _Writer(lib, out_path, dw, dh, fps, bitrate)
                # 竖屏画布里冒出横屏画面 (全是竖屏设备, 中途有横屏) → 把这一段的每帧转正 (MECHANICS 18.8)
                # (目光不这样做: 每段已经按手机的朝向转正了, 横着拿手机录的就是横的, 放进竖画布时上下留黑; fit_turn=False)
                rotate = fit_turn and dw < dh and ew > eh
                rd = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
                                        ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
                                        ctypes.POINTER(ctypes.c_longlong), ctypes.POINTER(ctypes.c_void_p))(
                    ctypes.cast(reader, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0][9])
                while True:
                    idx, flags, ts = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_longlong()
                    sample = ctypes.c_void_p()
                    hr = rd(reader, MF_SOURCE_READER_ALL_STREAMS, 0, byref(idx), byref(flags), byref(ts),
                            byref(sample))
                    if hr < 0:
                        break
                    try:
                        if flags.value & MF_SOURCE_READERF_ENDOFSTREAM and not sample:
                            break
                        if not sample:
                            continue
                        if idx.value != vidx:
                            continue
                        ptr, size, buf = _sample_bytes(sample)
                        try:
                            src = ctypes.string_at(ptr, size)
                            fw, fh, fstride = sw, sh, stride
                            if turn:
                                src, fw, fh, fstride = _rotate(src, fw, fh, fstride, turn)
                            if rotate:
                                src, fw, fh, fstride = _rotate90(src, fw, fh, fstride)
                            frame = _fit(src, fw, fh, fstride, dw, dh)
                            data = ctypes.create_string_buffer(frame, len(frame))
                            writer.write_bytes(data, len(frame), n_frames * STILL_STEP, STILL_STEP)
                            n_frames += 1
                        finally:
                            _com(buf, 4, what="Unlock")
                            _release(buf)
                    finally:
                        _release(sample)
            finally:
                _release(vcur)
                _release(reader)
        return dw, dh
    finally:
        if writer is not None:
            writer.close()
        _shutdown(lib, own)


# ---------------------------------------------------------------- 合并

def merge(data_dir: str, cid: str, kind: str = "screen", extra=()) -> str:
    """MECHANICS 18.8: 把这个合约某一路 (camera / screen) 的录像片段 (本机的 + 从别的设备收到的 + extra)
    按时间接成一个视频, 加密存进 merged/<kind>. 返回合并后的路径 (没有片段时 "")."""
    inc = incoming_dir(data_dir, cid, kind)
    got = []
    try:
        for n in os.listdir(inc):
            if n.lower().endswith(EXT):
                got.append(os.path.join(inc, n))
    except OSError:
        pass
    segs = [dict(s, local=True) for s in files_of(data_dir, kind, cid)] + [
        {"path": p, "name": os.path.basename(p), "start": None, "local": False} for p in list(extra) + got]
    segs = [s for s in segs if not s.get("partial")]
    if not segs:
        return ""
    used = {s["name"] for s in segs}
    consumed = read_consumed(data_dir, cid, kind)
    if consumed - used:
        # 以前合进去的片段已经清掉了 (SETTLE_HOURS 之后): 重合并时它们取自现有的最终录像, 不然迟到的片段会把整段录像盖成残缺的
        for n in os.listdir(merged_dir(data_dir, kind)):
            if cid_of(n) == _safe_cid(cid) and n.lower().endswith(EXT):
                segs.append({"path": os.path.join(merged_dir(data_dir, kind), n), "name": n, "start": None, "local": False})

    def when(s):
        return s["start"] or datetime.strptime(os.path.basename(s["name"])[:15], "%Y%m%d-%H%M%S")
    try:
        segs.sort(key=when)
    except ValueError:
        segs.sort(key=lambda s: s["name"])
    # 每次合并用自己的临时文件夹: 目光和刻痕同时合并 (两个线程) 时, 共用一个文件夹会互相覆盖 / 删掉对方的 0000.mp4 和 merged.mp4,
    # 结果是 MFCreateSinkWriterFromURL 0x80070020 / open 0x80070003 / Permission denied, 两路都合并失败
    root = work_dir(data_dir, "merged")
    os.makedirs(root, exist_ok=True)
    work = tempfile.mkdtemp(prefix=f"{_safe_cid(cid)}-{kind}-", dir=root)
    plain = []          # [(解密后的 mp4 路径, 是不是本机录的)]
    try:
        for i, s in enumerate(segs):
            p = s["path"]
            if p.lower().endswith(EXT):
                out = os.path.join(work, f"{i:04d}.mp4")
                decrypt_file(p, out)
                plain.append((out, s.get("local", False)))
            else:
                plain.append((p, s.get("local", False)))
        raw = os.path.join(work, "merged.mp4")
        # 画布的朝向 / 大小 (MECHANICS 18.8): 有横屏设备 (本机电脑录过, 或横屏片段占多数) 就按横屏, 否则按竖屏;
        # 横屏画布里的竖屏内容左右留黑, 竖屏画布里冒出的横屏画面转正
        info, weights, ok = [], [], []
        for p, loc in plain:
            try:
                w, h = _dims(p)
            except Exception:       # noqa: BLE001
                continue            # 打不开的片段 (例如手机上 OIT 被强杀时没写完的) 跳过, 不让整个合并失败
            info.append((w, h, loc))
            ok.append((p, loc))
            # 目光: 按横 / 竖各录了多久定画布的朝向 (手机横过来录了大半场, 就是横的), 码率固定, 用文件大小代替时长
            weights.append(max(1, os.path.getsize(p)) if kind == "camera" else 1)
        plain = ok
        if not plain:
            return ""
        # 目光 (摄像头) 的合并结果按目光的码率压 (要同步给所有设备, 不能太大); 照片按顺序每张 0.2 秒
        cam = kind == "camera"
        _reencode_concat(raw, [p for p, _ in plain], size=_canvas(info, weights),
                         bitrate=CAMERA_BITRATE if cam else SCREEN_BITRATE, fit_turn=not cam)
        d = merged_dir(data_dir, kind)
        os.makedirs(d, exist_ok=True)
        for n in os.listdir(d):                   # 这个合约这一路的旧结果先删 (本次重新合并盖掉)
            if cid_of(n) == _safe_cid(cid):
                _remove(os.path.join(d, n))
        stem = when(segs[0]).strftime("%Y%m%d-%H%M%S")
        dst = os.path.join(d, f"{stem}__{_safe_cid(cid)}{EXT}")
        encrypt_file(raw, dst)
        write_consumed(data_dir, cid, kind, consumed | used)
        return dst
    finally:
        shutil.rmtree(work, ignore_errors=True)
