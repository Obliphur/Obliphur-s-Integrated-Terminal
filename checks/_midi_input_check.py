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


def main():
    work = Path(tempstore.autosession())
    root = tk.Tk()
    root.geometry("320x200+-3000+-3000")
    api = Driver()
    with patch.object(midi, "_winmm", return_value=api), patch.object(
            midi, "input_devices", return_value=[(0, "Test instrument")]):
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
    print("MIDI input check OK")


if __name__ == "__main__":
    main()
