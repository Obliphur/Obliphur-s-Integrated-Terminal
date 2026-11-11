"""
合约的录像 (MECHANICS 18.8 目光 / 刻痕): 摄像头和整个屏幕各存成 mp4 (H.264, 没有声音), 只用标准库.

不录连续的视频 (视频有时长限制, 占地方): 每 STILL_EVERY 秒拍一张照片 (目光) / 截一张图 (刻痕),
照片按顺序每张 STILL_STEP 秒接成视频 (片段里和合并后都是), 放的时候就是 0.2 秒一张.
编码用 Windows 自带的 Media Foundation (mfplat / mfreadwrite, 经 ctypes 调用 COM 接口):
- 屏幕: GDI 截取整个虚拟屏幕 (所有显示器, 带鼠标指针), 缩到最宽 SCREEN_WIDTH;
- 摄像头: 第一个视频采集设备, 挑最接近 640×480 的格式 (摄像头一直开着, 只留每 STILL_EVERY 秒的那一张).
录像在后台线程里写. 每 SEGMENT 秒换一个文件 (程序被强行关掉时最多丢这一段).

加密 (防止被网盘 / 云相册自动上传后看到): 正在写的 mp4 放在 OIT 的临时文件夹里 (work_dir → %TEMP%\\OIT\\rec, 不在数据目录),
写完 (Finalize) 后用 AES-256-CTR 加密成 .oitv 存进数据目录, 再删掉 mp4. 只能在 OIT 里播放 (play: 解密到临时文件夹再打开).
文件格式: MAGIC + 8 字节 nonce + 密文 (计数器 = nonce + 第几个 16 字节块, 同 Android 的 RecCrypto).

存放: 数据目录/recordings/camera 和 数据目录/recordings/screen, 分开存放.

实现分在 recorder_parts/ 里: recorder_common (常量) / recorder_store (存档, 加密, 清理) /
recorder_media (Media Foundation, 屏幕, 摄像头) / recorder_merge (合并). 它们从不导入本模块;
这里只留录像线程和自检, 并把旧的 recorder.XXX 名字重新导出, 其他文件的写法不用改.
"""

import ctypes
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime

from recorder_parts.recorder_common import (
    CAMERA_BITRATE, CAMERA_FPS, CAMERA_SIZE, CAMERA_STALL, CONSUMED, EXT, FOCUS_EVERY, KINDS,
    MAGIC, MAX_AGE_DAYS, PART, PARTIAL_AGE_DAYS, PURGE_EXT, REC_KEY, SCREEN_BITRATE, SCREEN_FPS,
    SCREEN_WIDTH, SEGMENT, SETTLE_HOURS, STILL_EVERY, STILL_STEP, _CHUNK,
)
from recorder_parts.recorder_media import (
    GUID, HRESULT, IID_IMFMediaSource, MFError, MFMediaType_Audio, MFMediaType_Video, MFVideoFormat_H264,
    MFVideoFormat_RGB32, MFVideoInterlace_Progressive, MF_DEVSOURCE_ATTRIBUTE_FRIENDLY_NAME,
    MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE, MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_GUID,
    MF_DEVSOURCE_ATTRIBUTE_SOURCE_TYPE_VIDCAP_SYMBOLIC_LINK, MF_E_NO_MORE_TYPES, MF_MT_ALL_SAMPLES_INDEPENDENT,
    MF_MT_AVG_BITRATE, MF_MT_DEFAULT_STRIDE, MF_MT_FRAME_RATE, MF_MT_FRAME_SIZE, MF_MT_INTERLACE_MODE,
    MF_MT_MAJOR_TYPE, MF_MT_PIXEL_ASPECT_RATIO, MF_MT_SUBTYPE, MF_MT_VIDEO_ROTATION,
    MF_SINK_WRITER_DISABLE_THROTTLING, MF_SOURCE_READERF_ENDOFSTREAM, MF_SOURCE_READERF_ERROR,
    MF_SOURCE_READER_ALL_STREAMS, MF_SOURCE_READER_ENABLE_VIDEO_PROCESSING,
    MF_SOURCE_READER_FIRST_VIDEO_STREAM, _attributes, _camera_activates, _camera_index, _check, _com,
    _dims, _find_streams, _free_activates, _get_guid, _get_string, _get_u32, _get_u64, _media_type,
    _pick_camera_type, _reader_type, _release, _sample_bytes, _Screen, _set_guid, _set_u32, _set_u64,
    _shutdown, _startup, _streams, _video_rotation, _Writer, camera_consent, camera_count, camera_devices,
)
from recorder_parts.recorder_merge import _canvas, _fit, _reencode_concat, _rotate, _rotate90, _topdown, merge
from recorder_parts.recorder_store import (
    _Aes, _consumed_path, _remove, _safe_cid, cid_of, clean_play, decrypt_file, delete_merged, encrypt_file,
    files_of, folder, incoming_dir, list_files, list_merged, merged_dir, merged_file, play, play_dir, purge_old,
    read_consumed, rec_xor, seal_file, seal_leftovers, settle_fragments, total_size, work_dir, write_consumed,
)


# ---------------------------------------------------------------- 录像线程

class Recorder(threading.Thread):
    """一路录像 (kind = camera / screen). state: starting → running → stopped;
    出错时 failed (没能开始) / lost (录着录着摄像头没了). error 是说明."""

    def __init__(self, data_dir: str, kind: str, on_file=None, tag="", camera=""):
        super().__init__(daemon=True, name=f"rec-{kind}")
        self.data_dir = data_dir
        self.kind = kind
        self._tag_lock = threading.Lock()
        self.tag = self._clean_tag(tag)         # 合约 id: 文件名里带上, 结束后按它合并 (18.8)
        self.camera = str(camera or "")         # 本机的哪一台摄像头 (camera_devices 的 id; "" = 第一台)
        self.on_file = on_file                 # 写完一个文件 (加密好) 时调用 (路径)
        self.writing = ""                      # 正在写的文件 (临时文件夹里)
        self.state = "starting"
        self.error = ""
        self.started_at = None                 # 第一帧写进去的时刻 (time.time())
        self.frames = 0
        self._stop = threading.Event()

    def stop(self, wait: float = 5.0):
        self._stop.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(wait)

    @property
    def alive(self) -> bool:
        return self.state in ("starting", "running")

    @staticmethod
    def _clean_tag(tag) -> str:
        return re.sub(r"[^0-9A-Za-z]", "", str(tag))[:24]

    def set_tag(self, tag: str):
        """合约 id 要等合约真正开始才有 (目光: 先打开摄像头, 打开了才开始合约).
        之后写 / 已经写完的这一段都按新的 tag 命名, 否则结束后按合约 id 找不到录像 (18.8)."""
        with self._tag_lock:
            self.tag = self._clean_tag(tag)

    def _tag_text(self) -> str:
        with self._tag_lock:
            return self.tag

    def _new_path(self):
        d = work_dir(self.data_dir, self.kind)
        os.makedirs(d, exist_ok=True)
        out = folder(self.data_dir, self.kind)
        stem = datetime.now().strftime("%Y%m%d-%H%M%S")
        tag = self._tag_text()
        suffix = f"__{tag}" if tag else ""
        n = 0
        while True:
            name = stem + (f"-{n}" if n else "") + suffix
            final = os.path.join(d, name + ".mp4")
            part = os.path.join(d, name + PART + ".mp4")
            if not any(os.path.exists(x) for x in (final, part, os.path.join(out, name + EXT))):
                self.writing = part
                return part, final
            n += 1

    def _final_path(self, part: str) -> str:
        """这一段写完后的正式名字: 用现在的 tag (可能从 "" 改成了合约 id), 并避开已有的片段 / 录像."""
        base = os.path.basename(part)
        if base.lower().endswith(PART + ".mp4"):
            base = base[:-len(PART) - 4]
        elif base.lower().endswith(".mp4"):
            base = base[:-4]
        stem, _sep, _old = base.partition("__")          # 去掉开始写时的 tag
        out = folder(self.data_dir, self.kind)
        tag = self._tag_text()
        n = 0
        while True:
            name = stem + (f"-{n}" if n else "") + (f"__{tag}" if tag else "")
            final = os.path.join(os.path.dirname(part), name + ".mp4")
            if os.path.abspath(final).lower() == os.path.abspath(part).lower():
                return final
            if not os.path.exists(final) and not os.path.exists(os.path.join(out, name + EXT)):
                return final
            n += 1

    def _done(self, part, final):
        """一段写完了: 改成正式的名字, 在另一个线程里加密进数据目录 (不耽误下一段)."""
        self.writing = ""
        final = self._final_path(part)
        try:
            os.replace(part, final)
        except OSError:
            final = part

        def seal():
            try:
                path = seal_file(self.data_dir, self.kind, final)
            except OSError as e:
                self.error = self.error or f"encrypt: {e}"
                return
            if self.on_file:
                try:
                    self.on_file(path)
                except Exception:
                    pass
        t = threading.Thread(target=seal, daemon=True, name=f"rec-seal-{self.kind}")
        t.start()
        self._sealing = t

    def wait_sealed(self, wait: float = 10.0):
        """等最后一段加密完 (关闭程序时)."""
        t = getattr(self, "_sealing", None)
        if t is not None and t.is_alive():
            t.join(wait)

    def run(self):
        try:
            lib, own = _startup()
        except Exception as e:
            self.state, self.error = "failed", f"Media Foundation: {e}"
            return
        try:
            (self._run_camera if self.kind == "camera" else self._run_screen)(lib)
        except Exception as e:
            if self.state == "starting":
                self.state = "failed"
            elif self.state == "running" and not self._stop.is_set():
                self.state = "lost"
            self.error = self.error or f"{type(e).__name__}: {e}"
        finally:
            if self.state in ("starting", "running"):
                self.state = "stopped"
            _shutdown(lib, own)

    # ---- 屏幕 ----

    def _run_screen(self, lib):
        scr = _Screen()
        try:
            while not self._stop.is_set():
                part, final = self._new_path()
                w = _Writer(lib, part, scr.w, scr.h, SCREEN_FPS, SCREEN_BITRATE)
                seg0 = time.monotonic()
                k = 0
                try:
                    while not self._stop.is_set() and time.monotonic() - seg0 < SEGMENT:
                        bits = scr.grab()                           # 截一张图, 在视频里放 STILL_STEP (0.2 秒)
                        w.write_bytes(bits, scr.size, k * STILL_STEP, STILL_STEP)
                        k += 1
                        self.frames += 1
                        if self.state == "starting":
                            self.state, self.started_at = "running", time.time()
                        while not self._stop.is_set():
                            left = seg0 + k * STILL_EVERY - time.monotonic()
                            if left <= 0:
                                break
                            self._stop.wait(min(left, 0.5))
                finally:
                    w.close()
                    self._done(part, final) if k else _remove(part)
        finally:
            scr.close()

    # ---- 摄像头 ----

    def _run_camera(self, lib):
        arr, n = _camera_activates(lib)
        if not n:
            _free_activates(lib, arr, n)
            self.error = "no camera"
            raise MFError("没有找到摄像头")
        source = ctypes.c_void_p()
        try:
            idx = _camera_index(arr, n, self.camera)
            _com(arr[idx], 33, ctypes.byref(IID_IMFMediaSource), ctypes.byref(source),
                 argtypes=(ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)), what="ActivateObject")
        finally:
            _free_activates(lib, arr, n)
        reader = ctypes.c_void_p()
        attrs = _attributes(lib, 1)
        try:
            _set_u32(attrs, MF_SOURCE_READER_ENABLE_VIDEO_PROCESSING, 1)
            _check(lib.rw.MFCreateSourceReaderFromMediaSource(source, attrs, ctypes.byref(reader)),
                   "MFCreateSourceReaderFromMediaSource")
        finally:
            _release(attrs)
        cur = ctypes.c_void_p()
        try:
            native = _pick_camera_type(reader)
            if native:
                try:
                    _com(reader, 7, MF_SOURCE_READER_FIRST_VIDEO_STREAM, None, native,
                         argtypes=(ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p), what="SetCurrentMediaType(native)")
                except MFError:
                    pass
                finally:
                    _release(native)
            want = _media_type(lib)
            try:
                _set_guid(want, MF_MT_MAJOR_TYPE, MFMediaType_Video)
                _set_guid(want, MF_MT_SUBTYPE, MFVideoFormat_RGB32)
                _com(reader, 7, MF_SOURCE_READER_FIRST_VIDEO_STREAM, None, want,
                     argtypes=(ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p), what="SetCurrentMediaType(RGB32)")
            finally:
                _release(want)
            _com(reader, 6, MF_SOURCE_READER_FIRST_VIDEO_STREAM, ctypes.byref(cur),
                 argtypes=(ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p)), what="GetCurrentMediaType")
            fs = _get_u64(cur, MF_MT_FRAME_SIZE)
            width, height = fs >> 32, fs & 0xFFFFFFFF
            self._camera_loop(lib, reader, cur, width, height)
        finally:
            _release(cur)
            _release(reader)
            try:
                _com(source, 12, what="Shutdown")
            except Exception:
                pass
            _release(source)

    def _camera_loop(self, lib, reader, cur, width, height):
        read = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
                                  ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
                                  ctypes.POINTER(ctypes.c_longlong), ctypes.POINTER(ctypes.c_void_p))(
            ctypes.cast(reader, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0][9])
        self._last_frame = time.monotonic()
        while not self._stop.is_set():
            part, final = self._new_path()
            w = _Writer(lib, part, width, height, CAMERA_FPS, CAMERA_BITRATE, input_type=cur)
            seg0 = time.monotonic()
            last = -1e9
            k = 0
            try:
                while not self._stop.is_set() and time.monotonic() - seg0 < SEGMENT:
                    idx, flags, ts = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_longlong()
                    sample = ctypes.c_void_p()
                    hr = read(reader, MF_SOURCE_READER_FIRST_VIDEO_STREAM, 0, ctypes.byref(idx),
                              ctypes.byref(flags), ctypes.byref(ts), ctypes.byref(sample))
                    try:
                        if hr < 0:
                            self.error = f"ReadSample 0x{hr & 0xFFFFFFFF:08X}"
                            raise MFError(self.error)
                        if flags.value & (MF_SOURCE_READERF_ERROR | MF_SOURCE_READERF_ENDOFSTREAM):
                            self.error = f"camera stream ended (flags 0x{flags.value:X})"
                            raise MFError(self.error)
                        now = time.monotonic()
                        if not sample:
                            if now - self._last_frame > CAMERA_STALL:
                                self.error = "camera stalled"
                                raise MFError(self.error)
                            continue
                        self._last_frame = now
                        if now - last < STILL_EVERY:          # 摄像头一直开着 (要看着它有没有被关掉), 只留每 30 秒的那一张
                            continue
                        last = now
                        w.write_sample(sample, k * STILL_STEP, STILL_STEP)
                        k += 1
                        self.frames += 1
                        if self.state == "starting":
                            self.state, self.started_at = "running", time.time()
                    finally:
                        _release(sample)
            finally:
                w.close()
                self._done(part, final) if k else _remove(part)

    def stalled(self) -> bool:
        """摄像头太久没有新画面 (ReadSample 卡住时线程自己发现不了, 由外面看)."""
        if self.kind != "camera" or self.state != "running":
            return False
        return time.monotonic() - getattr(self, "_last_frame", time.monotonic()) > CAMERA_STALL


# ---------------------------------------------------------------- 自检

def _selftest(data_dir=None, seconds=3):
    """录几秒屏幕 (30 秒一张: 开始时截一张), 看加密的文件写出来了没有, 解密后能读出画面 (要有桌面)."""
    d = data_dir or tempfile.mkdtemp()
    # 加密: 任意位置开始解密都对得上
    data = os.urandom(5000)
    nonce = b"12345678"
    enc = rec_xor(nonce, 0, data)
    assert enc != data and rec_xor(nonce, 0, enc) == data and rec_xor(nonce, 37, enc[37:999]) == data[37:999]
    # 已知答案 (NIST SP 800-38A F.5.5 的密钥换成 REC_KEY 时没有现成的答案, 这里只核对 CTR 的计数器拼法)
    ks = rec_xor(nonce, 0, bytes(32))
    assert ks[16:] == _Aes.ecb(nonce + (1).to_bytes(8, "big"))
    r = Recorder(d, "screen")
    r.start()
    t0 = time.time()
    while r.state == "starting" and time.time() - t0 < 10:
        time.sleep(0.1)
    assert r.state == "running", (r.state, r.error)
    time.sleep(seconds)
    note = r.error
    assert r.frames == 1, r.frames          # 3 秒里只截了开始时的一张
    r.stop()
    r.wait_sealed()
    assert r.state == "stopped", (r.state, r.error)
    files = list_files(d, "screen")
    assert len(files) == 1 and not files[0]["partial"] and files[0]["name"].endswith(EXT) \
        and files[0]["size"] > 1000, files
    assert not os.listdir(work_dir(d, "screen")), "临时文件夹里的 mp4 加密后删掉了"
    with open(files[0]["path"], "rb") as f:
        head = f.read(64)
    assert head.startswith(MAGIC) and b"ftyp" not in head, "存下来的不是明文 mp4"
    out = os.path.join(d, "check.mp4")
    decrypt_file(files[0]["path"], out)
    with open(out, "rb") as f:
        assert f.read(12)[4:8] == b"ftyp", "解密后是 mp4"
    n = _streams(out)
    assert n == 1, (n, note)
    # 合并 (刻痕): 给这一段打上合约 id 再录一段, 接成一个 (总览只有画面)
    merged = ""
    r2 = Recorder(d, "screen", tag="x1")
    r2.start()
    t0 = time.time()
    while r2.state == "starting" and time.time() - t0 < 10:
        time.sleep(0.1)
    time.sleep(2)
    r2.stop()
    r2.wait_sealed()
    assert len(files_of(d, "screen", "x1")) == 1 and cid_of(files_of(d, "screen", "x1")[0]["name"]) == "x1"
    mp = merge(d, "x1")
    mo = os.path.join(d, "merged.mp4")
    decrypt_file(mp, mo)
    assert _streams(mo) == 1 and os.path.getsize(mp) > 1000, (mp, _streams(mo))
    merged = f", merged {_dims(mo)}"
    # 目光和刻痕在合约结束时同时合并 (两个线程): 不能共用临时文件夹 (1.0.367 以前互相删对方的文件, 两路都失败)
    seg = files_of(d, "screen", "x1")[0]
    os.makedirs(folder(d, "camera"), exist_ok=True)
    shutil.copyfile(seg["path"], os.path.join(folder(d, "camera"), seg["name"]))
    errs, outs = [], {}

    def both(kind):
        try:
            outs[kind] = merge(d, "x1", kind)
        except Exception as e:      # noqa: BLE001
            errs.append((kind, repr(e)))
    ts = [threading.Thread(target=both, args=(k,)) for k in ("screen", "camera")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errs and all(outs.get(k) for k in ("screen", "camera")), (errs, outs)
    assert not os.listdir(os.path.join(work_dir(d, "merged"))), "合并的临时文件夹用完要删"
    # 目光: 摄像头要在合约开始前先打开 (那时还不知道合约 id), 合约开始后再补上 tag ——
    # 这一段 / 这一整场录像结束时都要能按合约 id 找到 (否则就是"摄像头录像存不下来").
    late = Recorder(d, "screen")
    late.set_tag("late1")
    part = os.path.join(work_dir(d, "screen"), "20260101-000000.recording.mp4")
    assert os.path.basename(late._final_path(part)) == "20260101-000000__late1.mp4", late._final_path(part)
    return (f"recorder selftest OK ({r.frames} frames, {files[0]['size']} bytes, streams {n}, "
            f"cameras: {camera_count()}{merged}{', note: ' + note if note else ''})"), d


def _selftest_cleanup():
    """录像的清理 (不录像, 只用临时文件夹): 3 天自动清除, 没写完的 1 天, 合并后用掉的片段 24 小时后清掉, 空文件夹删掉."""
    d = tempfile.mkdtemp()
    now = time.time()
    try:
        def make(path, age_h):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"x")
            os.utime(path, (now - age_h * 3600, now - age_h * 3600))
            return path
        cam, scr = folder(d, "camera"), folder(d, "screen")
        old = make(os.path.join(cam, "20260101-000000__c1" + EXT), 80)          # 超过 3 天
        new = make(os.path.join(cam, "20260101-000001__c2" + EXT), 10)
        part_old = make(os.path.join(scr, "20260101-000002__c2" + PART + EXT), 30)    # 没写完, 超过 1 天
        part_new = make(os.path.join(scr, "20260101-000003__c2" + PART + EXT), 5)
        inc_old = make(os.path.join(incoming_dir(d, "c1", "camera"), "20260101-000004__c1" + EXT), 90)
        keep_txt = make(os.path.join(d, "recordings", "notes.txt"), 500)         # 不是录像: 不动
        merged_old = make(os.path.join(merged_dir(d, "screen"), "20260101-000005__c3" + EXT), 100)
        orphan = make(_consumed_path(d, "gone", "screen"), 1)                    # 最终录像已经没了的记录
        n = purge_old(d, now=now, busy=[new])
        assert not os.path.exists(old) and not os.path.exists(part_old) and not os.path.exists(inc_old) and not os.path.exists(merged_old)
        assert os.path.exists(new) and os.path.exists(part_new) and os.path.exists(keep_txt) and not os.path.exists(orphan)
        assert n == 4, n
        assert not os.path.isdir(os.path.join(d, "recordings", "incoming", "c1")), "空了的 incoming 文件夹要删"
        # 合并后用掉的片段: 24 小时之后清掉 (本机的 + 收到的), 之前留着
        seg_local = make(os.path.join(cam, "20260102-000000__c9" + EXT), 30)
        seg_in = make(os.path.join(incoming_dir(d, "c9", "camera"), "20260102-000100__c9" + EXT), 30)
        seg_other = make(os.path.join(cam, "20260102-000200__c8" + EXT), 30)
        final = make(os.path.join(merged_dir(d, "camera"), "20260102-000000__c9" + EXT), 30)
        write_consumed(d, "c9", "camera", [os.path.basename(seg_local), os.path.basename(seg_in)])
        os.utime(_consumed_path(d, "c9", "camera"), (now - 3600, now - 3600))      # 1 小时前合并的
        assert settle_fragments(d, now=now) == 0 and os.path.exists(seg_local)
        os.utime(_consumed_path(d, "c9", "camera"), (now - 25 * 3600, now - 25 * 3600))
        assert settle_fragments(d, now=now) == 2
        assert not os.path.exists(seg_local) and not os.path.exists(seg_in) and os.path.exists(seg_other) and os.path.exists(final)
        assert read_consumed(d, "c9", "camera") == {os.path.basename(seg_local), os.path.basename(seg_in)}
        # 旧版本合并好的 (没有记录): 补上记录, 从最终录像的时间起算
        old_frag = make(os.path.join(scr, "20260103-000000__c7" + EXT), 30)
        old_final = make(os.path.join(merged_dir(d, "screen"), "20260103-000000__c7" + EXT), 30)
        assert settle_fragments(d, now=now) == 1 and not os.path.exists(old_frag) and os.path.exists(old_final)
        # 正在写的 (busy) 不删, 就算很旧
        busy = make(os.path.join(cam, "20260101-000009__cb" + EXT), 500)
        purge_old(d, now=now, busy=[busy])
        assert os.path.exists(busy)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return "recorder cleanup selftest OK"


if __name__ == "__main__":
    import tempstore
    tempstore.autosession()      # 自检的临时文件进 %TEMP%\OIT 的会话目录, 退出即删 (tempstore.py)
    if "--cleanup-test" in sys.argv:
        print(_selftest_cleanup())
    else:
        print(_selftest()[0])
