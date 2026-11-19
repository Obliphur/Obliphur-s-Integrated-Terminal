"""通过 Windows MIDI 输入设备录制本机评判用的 MIDI。"""

import ctypes
import asyncio
from ctypes import wintypes
from dataclasses import dataclass
import struct
import sys
from threading import Event, Lock, Thread
from time import monotonic

import config
from i18n import tr


class MidiInputError(RuntimeError):
    pass


@dataclass(frozen=True)
class MidiDevice:
    backend: str
    device_id: object
    name: str


BLUETOOTH_MIDI_STATUS = ""


def _winrt_modules():
    """只在枚举 / 打开 BLE MIDI 时加载可选的 PyWinRT。"""
    from winrt.windows.devices.enumeration import DeviceInformation
    from winrt.windows.devices.midi import MidiInPort
    from winrt.windows.storage.streams import DataReader
    return DeviceInformation, MidiInPort, DataReader


def _winrt_query(coroutine, timeout=10):
    """在临时 MTA 事件循环上完成 WinRT 异步操作，不阻塞 Tk 消息泵。"""
    result = []
    error = []
    done = Event()

    def run():
        try:
            result.append(asyncio.run(coroutine))
        except Exception as exc:
            error.append(exc)
        finally:
            done.set()

    Thread(target=run, daemon=True).start()
    if not done.wait(timeout):
        raise MidiInputError(tr("Windows MIDI 服务响应超时."))
    if error:
        raise MidiInputError(tr("无法读取 Windows MIDI 设备: {error}").format(error=error[0]))
    return result[0]


class _InputCaps(ctypes.Structure):
    _fields_ = [
        ("manufacturer", wintypes.WORD), ("product", wintypes.WORD),
        ("version", wintypes.DWORD), ("name", wintypes.WCHAR * 32),
        ("support", wintypes.DWORD),
    ]


def _winmm():
    if sys.platform != "win32":
        raise MidiInputError(tr("实时 MIDI 输入仅支持 Windows."))
    api = ctypes.WinDLL("winmm")
    signatures = {
        "midiInGetNumDevs": (),
        "midiInGetDevCapsW": (ctypes.c_size_t, ctypes.POINTER(_InputCaps), wintypes.UINT),
        "midiInOpen": (ctypes.POINTER(wintypes.HANDLE), wintypes.UINT,
                       ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD),
        "midiInStart": (wintypes.HANDLE,),
        "midiInStop": (wintypes.HANDLE,),
        "midiInClose": (wintypes.HANDLE,),
        "midiInGetErrorTextW": (wintypes.UINT, wintypes.LPWSTR, wintypes.UINT),
    }
    for name, types in signatures.items():
        fn = getattr(api, name)
        fn.argtypes = types
        fn.restype = wintypes.UINT
    return api


def _check(api, code):
    if code:
        message = ctypes.create_unicode_buffer(256)
        api.midiInGetErrorTextW(code, message, len(message))
        raise MidiInputError(tr("MIDI 输入失败: {error}").format(error=message.value or code))


def input_devices():
    """合并 WinMM 设备与 Windows WinRT MIDI 设备，并去掉同名端口。"""
    global BLUETOOTH_MIDI_STATUS
    api = _winmm()
    devices = []
    for device_id in range(api.midiInGetNumDevs()):
        caps = _InputCaps()
        if api.midiInGetDevCapsW(device_id, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            name = caps.name.strip()
            devices.append(MidiDevice("winmm", device_id, name))
    BLUETOOTH_MIDI_STATUS = ""
    try:
        DeviceInformation, MidiInPort, _ = _winrt_modules()

        async def query():
            return await DeviceInformation.find_all_async(MidiInPort.get_device_selector(), None)

        for info in _winrt_query(query()):
            name = info.name.strip()
            if name:
                devices.append(MidiDevice("winrt", info.id, name))
    except ImportError:
        BLUETOOTH_MIDI_STATUS = tr("蓝牙 MIDI 支持未安装，请通过 OIT 安装程序启用.")
    except MidiInputError as error:
        BLUETOOTH_MIDI_STATUS = str(error)
    return devices


_MAX_DELTA = 0x0FFFFFFF  # 标准 MIDI 文件的单个 delta 最大值
_CALLBACK_FUNCTION = 0x00030000
_MIM_DATA = 0x3C3
_MIM_ERROR = 0x3C5
_open_inputs = set()  # WinMM 持有的是函数指针，端口成功关闭前需留住回调对象。


def _delta(value):
    parts = [value & 127]
    while value >= 128:
        value >>= 7
        parts.append((value & 127) | 128)
    return bytes(reversed(parts))


class _Take:
    """保留键盘和延音踏板数据，以首个有效 Note On 为零点。"""

    def __init__(self):
        self.origin = None
        self.started_at = None
        self.events = []
        self.active = set()
        self.pedal = set()
        self.error = ""

    def receive(self, status, key, value, timestamp):
        kind = status & 0xF0
        channel = status & 15
        note_on = kind == 0x90 and value > 0
        note_off = kind == 0x80 or (kind == 0x90 and value == 0)
        pedal = kind == 0xB0 and key == 64
        if self.error or not (note_on or note_off or pedal):
            return
        if self.origin is None:
            if not note_on:
                if pedal:
                    self._pedal(channel, value)
                return
            self.origin = timestamp
            self.started_at = monotonic()
            self.events.extend((0, 0xB0 | ch, 64, 127) for ch in sorted(self.pedal))
        elapsed = max(self.events[-1][0] if self.events else 0, timestamp - self.origin)
        if elapsed > _MAX_DELTA or len(self.events) >= config.MUSIC_MIDI_INPUT_MAX_EVENTS:
            self.error = "MIDI 录制超出长度上限，请分段录制."
            return
        if pedal:
            self._pedal(channel, value)
        elif note_on:
            if (channel, key) in self.active:
                self.events.append((elapsed, 0x80 | channel, key, 0))
            self.active.add((channel, key))
        else:
            if (channel, key) not in self.active:
                return
            self.active.remove((channel, key))
        self.events.append((elapsed, status, key, value))

    def _pedal(self, channel, value):
        if value >= 64:
            self.pedal.add(channel)
        else:
            self.pedal.discard(channel)

    def to_midi(self, stopped_at):
        if self.error:
            raise MidiInputError(tr(self.error))
        if self.origin is None:
            raise MidiInputError(tr("没有录到 MIDI 音符."))
        end = max(self.events[-1][0], min(_MAX_DELTA, round((stopped_at - self.started_at) * 1000)))
        events = self.events + [(end, 0x80 | ch, key, 0) for ch, key in sorted(self.active)]
        events.extend((end, 0xB0 | ch, 64, 0) for ch in sorted(self.pedal))
        track = bytearray()
        previous = 0
        for elapsed, status, key, value in events:
            track.extend(_delta(elapsed - previous))
            track.extend((status, key, value))
            previous = elapsed
        track.extend(b"\x00\xff\x2f\x00")
        # 格式 0，默认速度 120 BPM，每四分音符 500 tick，正好 1 ms/tick。
        return b"MThd" + struct.pack(">IHHH", 6, 0, 1, 500) + b"MTrk" + struct.pack(">I", len(track)) + track


class MidiInput:
    def __init__(self, device_id, backend="winmm"):
        self._bluetooth = _BluetoothMidiInput(device_id) if backend == "winrt" else None
        if self._bluetooth:
            return
        self._api = _winmm()
        self._lock = Lock()
        self._take = _Take()
        self._receiving = True
        self._handle = wintypes.HANDLE()
        callback_type = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.UINT,
                                          ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t)
        self._callback = callback_type(self._receive)
        pointer = ctypes.cast(self._callback, ctypes.c_void_p).value
        _check(self._api, self._api.midiInOpen(ctypes.byref(self._handle), device_id,
                                              pointer, 0, _CALLBACK_FUNCTION))
        _open_inputs.add(self)
        try:
            _check(self._api, self._api.midiInStart(self._handle))
        except MidiInputError:
            self.close()
            raise

    def _receive(self, _handle, message, _instance, packed, timestamp):
        # 回调线程只保存数据，不调用 WinMM 或 tkinter。
        if message in (_MIM_DATA, _MIM_ERROR):
            with self._lock:
                if not self._receiving:
                    return
                if message == _MIM_ERROR:
                    self._take.error = "MIDI 输入收到无效数据，请重新录制."
                    return
                self._take.receive(packed & 255, (packed >> 8) & 127, (packed >> 16) & 127, timestamp)

    def status(self):
        if self._bluetooth:
            return self._bluetooth.status()
        with self._lock:
            return self._take.origin is not None, self._take.error

    def close(self):
        if self._bluetooth:
            return self._bluetooth.close()
        if not self._handle:
            return
        with self._lock:
            self._receiving = False
        self._api.midiInStop(self._handle)
        code = self._api.midiInClose(self._handle)
        if code in (0, 5):  # MMSYSERR_INVALHANDLE：设备已移除，句柄不再有效。
            self._handle = None
            _open_inputs.discard(self)
        return code

    def finish(self):
        if self._bluetooth:
            return self._bluetooth.finish()
        stopped_at = monotonic()
        _check(self._api, self.close() or 0)
        return self._take.to_midi(stopped_at)


class _BluetoothMidiInput:
    """在独立 WinRT apartment 中接收系统配对的 BLE MIDI 消息。"""

    def __init__(self, device_id):
        self.device_id = device_id
        self._lock = Lock()
        self._take = _Take()
        self._receiving = True
        self._ready = Event()
        self._close_requested = Event()
        self._port = None
        self._token = None
        self._loop = None
        self._startup_error = None
        self._thread = Thread(target=self._run, name="OIT Bluetooth MIDI", daemon=True)
        self._thread.start()
        if not self._ready.wait(15):
            self.close()
            raise MidiInputError(tr("打开蓝牙 MIDI 设备超时."))
        if self._startup_error:
            error = self._startup_error
            self.close()
            raise MidiInputError(tr("无法打开蓝牙 MIDI 设备: {error}").format(error=error))

    async def _open(self):
        _, MidiInPort, _ = _winrt_modules()
        self._port = await MidiInPort.from_id_async(self.device_id)
        if self._port is None:
            raise MidiInputError(tr("Windows 无法打开此 MIDI 输入设备."))
        self._token = self._port.add_message_received(self._receive)

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._open())
            self._ready.set()
            if not self._close_requested.is_set():
                self._loop.run_forever()
        except Exception as error:
            self._startup_error = error
            self._ready.set()
        finally:
            if self._port is not None:
                try:
                    if self._token is not None:
                        self._port.remove_message_received(self._token)
                    self._port.close()
                except Exception:
                    pass
            self._loop.close()

    def _receive(self, _sender, args):
        reader = None
        try:
            _, _, DataReader = _winrt_modules()
            message = args.message
            reader = DataReader.from_buffer(message.raw_data)
            raw = bytearray(message.raw_data.length)
            reader.read_bytes(raw)
            if len(raw) < 3:
                return
            with self._lock:
                if self._receiving:
                    self._take.receive(raw[0], raw[1] & 127, raw[2] & 127,
                                       round(monotonic() * 1000))
        except Exception:
            with self._lock:
                self._take.error = "蓝牙 MIDI 输入收到无效数据，请重新录制."
        finally:
            if reader is not None:
                try:
                    reader.close()
                except Exception:
                    pass

    def status(self):
        with self._lock:
            return self._take.origin is not None, self._take.error

    def close(self):
        self._close_requested.set()
        loop = self._loop
        if self._thread.is_alive() and loop is not None and loop.is_running():
            with self._lock:
                self._receiving = False
            loop.call_soon_threadsafe(loop.stop)
            self._thread.join(timeout=5)
        return 0 if not self._thread.is_alive() else 1

    def finish(self):
        stopped_at = monotonic()
        if self.close():
            raise MidiInputError(tr("关闭蓝牙 MIDI 设备超时."))
        return self._take.to_midi(stopped_at)
