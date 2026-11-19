"""实时 MIDI 录制、窗口清理与评判首音对齐的回归检查。"""

import ctypes
import importlib.util
import json
from pathlib import Path
import sys
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import music_midi_input as midi
import tempstore
from pages.midi_input_dialog import MidiInputDialog


class Driver:
    """模拟 WinMM，在真实回调签名中传入带设备时间戳的短消息。"""

    def midiInOpen(self, handle, device, callback, instance, flags):
        ctypes.cast(handle, ctypes.POINTER(ctypes.c_void_p))[0] = 1
        self.callback = ctypes.WINFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint,
                                          ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t)(callback)
        self.closed = False
        return 0

    def midiInStart(self, handle):
        return 0

    def midiInStop(self, handle):
        return 0

    def midiInClose(self, handle):
        self.closed = True
        return 0

    def send(self, timestamp, status, key=0, value=0):
        self.callback(1, midi._MIM_DATA, 0, status | (key << 8) | (value << 16), timestamp)


class WinRtDriver:
    ports = []

    class DeviceInfo:
        def __init__(self, device_id, name):
            self.id, self.name = device_id, name

    class MidiInPort:
        @staticmethod
        def get_device_selector():
            return "midi-input-selector"

        @staticmethod
        async def from_id_async(device_id):
            port = WinRtDriver.Port(device_id)
            WinRtDriver.ports.append(port)
            return port

    class Port:
        def __init__(self, device_id):
            self.device_id = device_id
            self.callback = None
            self.closed = False
            self.removed = False

        def add_message_received(self, callback):
            self.callback = callback
            return "token"

        def remove_message_received(self, token):
            assert token == "token"
            self.removed = True

        def close(self):
            self.closed = True

        def send(self, data):
            reader = SimpleNamespace(
                read_bytes=lambda out: out.__setitem__(slice(None), data),
                close=lambda: None,
            )
            with patch.object(midi, "_winrt_modules", return_value=(None, None, SimpleNamespace(
                    from_buffer=lambda _: reader))):
                self.callback(None, SimpleNamespace(message=SimpleNamespace(
                    raw_data=SimpleNamespace(length=len(data)))))

    class DataReader:
        @staticmethod
        def find_all_async(*args):
            assert args == ("midi-input-selector", None)
            return WinRtDriver._find()

    @staticmethod
    async def _find():
        return [WinRtDriver.DeviceInfo("ble-id", "Bluetooth Piano")]


def main():
    work = Path(tempstore.autosession())
    root = tk.Tk()
    root.geometry("320x200+-3000+-3000")
    api = Driver()
    device = midi.MidiDevice("winmm", 0, "Test instrument")
    with patch.object(midi, "_winmm", return_value=api), patch.object(
            midi, "input_devices", return_value=[device]):
        dlg = MidiInputDialog(root)
        dlg._start()
        api.send(25, 0xF8)
        api.send(50, 0x90, 60, 0)
        api.send(100, 0xB0, 64, 127)
        assert dlg.recorder.status() == (False, "")
        api.send(1500, 0x90, 60, 97)
        api.send(1750, 0x90, 60, 0)
        api.send(1850, 0xB0, 64, 0)
        api.send(2000, 0x91, 64, 82)
        api.send(2300, 0x81, 64, 45)
        assert dlg.recorder.status() == (True, "")
        dlg._stop()
        data = dlg.result
        expected = (b"\x00\xb0\x40\x7f\x00\x90\x3c\x61"
                    b"\x81\x7a\x90\x3c\x00\x64\xb0\x40\x00"
                    b"\x81\x16\x91\x40\x52\x82\x2c\x81\x40\x2d"
                    b"\x00\xff\x2f\x00")
        assert data[22:] == expected, data
        assert api.closed and not midi._open_inputs
        dlg = MidiInputDialog(root)
        dlg._start()
        dlg._stop()
        assert dlg.winfo_exists() and dlg.recorder is None and dlg.result is None
        dlg._start()
        dlg.cancel()
        assert not midi._open_inputs
        dlg = MidiInputDialog(root)
        dlg._start()
        api.send(10, 0x90, 60, 90)
        with patch.object(config, "MUSIC_MIDI_INPUT_MAX_EVENTS", 1):
            api.send(20, 0x80, 60)
        dlg.after_cancel(dlg.poll)
        dlg._update_status()
        assert dlg.winfo_exists() and dlg.recorder is None and dlg.result is None
        dlg.cancel()
    root.destroy()

    take = midi._Take()
    take.receive(0xB0, 64, 127, 100)
    take.receive(0x90, 60, 90, 200)
    raw = take.to_midi(take.started_at + 0.5)
    assert b"\x83\x74\x80\x3c\x00\x00\xb0\x40\x00" in raw

    # 只隔离 Partitura 环境依赖，实际执行 worker 的对齐、匹配与输出逻辑。
    worker_path = Path(__file__).resolve().parents[1] / "music_compare_worker.py"
    spec = importlib.util.spec_from_file_location("compare_worker_check", worker_path)
    worker = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"partitura": SimpleNamespace(), "mido": SimpleNamespace()}):
        spec.loader.exec_module(worker)
    base = [(0.0, .25, 60, 90, 0), (.5, .3, 64, 80, 1)]
    shifted = [(n[0] + 6, *n[1:]) for n in base]
    output = work / "midi-comparison.json"
    for reference, performance, length in ((base, shifted, 6.8), (shifted, base, .8)):
        with patch.object(worker, "notes", side_effect=[reference, performance]), patch.object(
                worker.mido, "MidiFile", return_value=SimpleNamespace(length=length), create=True), patch.object(
                sys, "argv", ["worker", "--reference", "ref.mid", "--performance", "perf.mid",
                              "--output", str(output)]):
            worker.main()
        summary = json.loads(output.read_text(encoding="utf-8"))["summary"]
        assert summary["matched"] == 2 and summary["missing"] == summary["extra"] == 0
        assert summary["tempo_ratio_percent"] == 100 and summary["onset_error_ms_abs_mean"] == 0
        assert summary["performance_duration_ms"] == round(length * 1000)
    assert worker.from_first_note([]) == []

    # 蓝牙后端运行 WinRT 枚举、打开、消息回调和清理，不经过 WinMM 句柄。
    class NoDevices:
        def midiInGetNumDevs(self):
            return 0

    def winrt_modules():
        return (SimpleNamespace(find_all_async=WinRtDriver.DataReader.find_all_async),
                WinRtDriver.MidiInPort, WinRtDriver.DataReader)

    with patch.object(midi, "_winmm", return_value=NoDevices()), patch.object(
            midi, "_winrt_modules", side_effect=winrt_modules):
        devices = midi.input_devices()
        assert devices == [midi.MidiDevice("winrt", "ble-id", "Bluetooth Piano")]
        recorder = midi.MidiInput("ble-id", backend="winrt")
        port = WinRtDriver.ports[-1]
        port.send(bytes((0x90, 60, 90)))
        port.send(bytes((0x80, 60, 0)))
        raw = recorder.finish()
        assert port.closed and port.removed
        assert b"\x00\x90\x3c\x5a" in raw and b"\x00\x80\x3c\x00" in raw
    with patch.object(midi, "_winmm", return_value=NoDevices()), patch.object(
            midi, "_winrt_modules", side_effect=ImportError):
        assert midi.input_devices() == []
        assert "未安装" in midi.BLUETOOTH_MIDI_STATUS
    print("MIDI input check OK")


if __name__ == "__main__":
    main()
