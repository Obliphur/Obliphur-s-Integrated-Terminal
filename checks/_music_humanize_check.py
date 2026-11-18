"""MIDI 人性化：音符完整性、跨轨速度、微差上限、重触、截断与原子落盘。"""

from collections import Counter, defaultdict, deque
from pathlib import Path
import struct
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
import music_humanize as H
import music_tools as M
import tempstore


tempstore.session("music-humanize-check")
root = Path(tempstore.mkdtemp("music-humanize-check"))


def fixture(ppq=960, bpm=120, format_one=True):
    conductor = [H.Event(0, b"\xff\x03\x04test"),
                 H.Event(0, b"\xff\x51\x03" + int(60000000 / bpm).to_bytes(3, "big")),
                 H.Event(ppq * 8, b"\xff\x51\x03\x0f\x42\x40"),
                 H.Event(0, b"\xff\x58\x04\x04\x02\x18\x08")]
    tracks = []
    for channel in (0, 1):
        track = [H.Event(0, bytes([0xc0 + channel, 0])), H.Event(0, b"\xf0\x02\x01\xf7")]
        for beat in range(24):
            for pitch in (48, 55, 60):
                track.append(H.Event(beat * ppq, bytes([0x90 + channel, pitch + 12 * channel, 70])))
                # note_on velocity=0 和 note_off 两种结束写法都要保留。
                off = 0x90 if beat % 2 else 0x80
                track.append(H.Event((beat + 1) * ppq, bytes([off + channel, pitch + 12 * channel, 0])))
        track.extend([H.Event(ppq, bytes([0xb0 + channel, 64, 127])),
                      H.Event(ppq * 20, bytes([0xb0 + channel, 64, 0]))])
        track.append(H.Event(ppq * 10, b"\x92\x37\x00"))  # MuseScore 辅助通道复位松键。
        track.sort(key=lambda e: e.tick)
        tracks.append(track)
    # 鼓通道不加指间、力度与松键扰动。
    drums = [H.Event(0, b"\x99\x24\x40"), H.Event(ppq, b"\x89\x24\x00")]
    tracks.insert(0, conductor)
    tracks.append(drums)
    if not format_one:
        tracks = [sorted([e for t in tracks for e in t], key=lambda e: e.tick)]
    for track in tracks:
        track.sort(key=lambda e: e.tick)
        track.append(H.Event(ppq * 25, b"\xff\x2f\x00"))
    return H.write_midi(1 if format_one else 0, ppq, tracks)


def pairs(data):
    fmt, ppq, tracks = H.read_midi(data)
    tempo = H.TempoMap(ppq, tracks)
    notes = []
    for ti, track in enumerate(tracks):
        active = defaultdict(deque)
        assert all(a.tick <= b.tick for a, b in zip(track, track[1:]))
        assert track[-1].data == b"\xff\x2f\x00"
        for event in track:
            kind = event.data[0] & 0xf0
            if kind not in (0x80, 0x90):
                continue
            key = (ti, event.data[0] & 15, event.data[1])
            if kind == 0x90 and event.data[2]:
                active[key].append(event)
            else:
                if not active[key]:
                    continue
                on = active[key].popleft()
                assert on.tick < event.tick, "时值必须为正"
                notes.append((key, on.tick, event.tick, on.data[2], tempo.seconds_at(on.tick), tempo.seconds_at(event.tick)))
        assert not any(active.values()), "存在未松开的音"
    return sorted(notes, key=lambda n: (n[0], n[1]))


for ppq, bpm, one in ((960, 120, True), (96, 60, False), (480, 180, True)):
    original = fixture(ppq, bpm, one)
    for seed in range(20):
        rendered = H.humanize(original, seed=seed)
        assert rendered == H.humanize(original, seed=seed)
        assert rendered != H.humanize(original, seed=seed + 1)
        before, after = pairs(original), pairs(rendered)
        assert len(before) == len(after)
        assert Counter(n[0] for n in before) == Counter(n[0] for n in after)
        _, _, raw_tracks = H.read_midi(rendered)
        assert sum(e.data == b"\x92\x37\x00" for t in raw_tracks for e in t) == 2
        for a, b in zip(before, after):
            if a[0][1] == 9:
                assert a == b
            else:
                assert 1 <= b[3] <= 127 and abs(a[3] - b[3]) <= 6
                # 配置中的最大微差 + tick 四舍五入误差。
                assert abs(a[4] - b[4]) <= .028 + 1 / ppq
                # 松键噪声不超过 16 ms；为避免重触重叠，还可能缩短两次起音偏移之差。
                assert abs((a[5] - a[4]) - (b[5] - b[4])) <= .072 + 2 / ppq
        by_pitch = defaultdict(list)
        for n in after:
            by_pitch[n[0]].append(n)
        assert all(a[2] <= b[1] for seq in by_pitch.values() for a, b in zip(seq, seq[1:])), "新引入同音重叠"
        _, _, old_tracks = H.read_midi(original)
        _, _, new_tracks = H.read_midi(rendered)
        # tempo、节拍、控制器、音色、sysex 的内容与绝对位置均不变。
        keep = lambda ts: Counter((i, e.tick, e.data) for i, t in enumerate(ts) for e in t
                                  if e.data[0] & 0xf0 not in (0x80, 0x90) and not e.data.startswith(b"\xff\x7f"))
        assert keep(old_tracks) == keep(new_tracks)

# 独立的已知速度实例：先 120 BPM 两拍，再 60 BPM，跨越变速点的秒数必须准确。
tempo = H.TempoMap(480, [[H.Event(0, b"\xff\x51\x03\x07\xa1\x20"), H.Event(960, b"\xff\x51\x03\x0f\x42\x40")]])
assert tempo.seconds_at(480) == .5 and tempo.seconds_at(1440) == 2.0
assert tempo.tick_at(1.5) == 1200 and tempo.tick_at(.5) == 480

# 粗分辨率的连续一个 tick 短音不能报错，也不能丢失松键。
short = []
for tick in range(20):
    short.extend([H.Event(tick, b"\x90\x3c\x50"), H.Event(tick + 1, b"\x80\x3c\x00")])
short.sort(key=lambda e: e.tick)
short.append(H.Event(20, b"\xff\x2f\x00"))
assert len(pairs(H.humanize(H.write_midi(0, 96, [short]), seed=1))) == 20

# 原始 running status、不同 PPQ、短倚音、力度边界也应得到有效文件。
running = b"\x00\x90\x3c\x01\x00\x40\x7f\x01\x3c\x00\x01\x40\x00\x00\xff\x2f\x00"
raw = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 96) + b"MTrk" + struct.pack(">I", len(running)) + running
assert len(pairs(H.humanize(raw, seed=8))) == 2
zero = H.write_midi(0, 480, [[H.Event(0, b"\x90\x3c\x40"), H.Event(0, b"\x90\x3c\x00"), H.Event(0, b"\xff\x2f\x00")]])
assert len(pairs(H.humanize(zero, seed=8))) == 1

bad_inputs = [b"MThd\0\0\0\x06", fixture()[:-1], b"not midi", raw.replace(b"\x00\x60", b"\xe7\x28", 1),
              raw.replace(b"\x3c\x00", b"\x3d\x00"), H.humanize(fixture(), seed=1)]
for bad in bad_inputs:
    try:
        H.humanize(bad)
    except H.MidiError:
        pass
    else:
        raise AssertionError("无效或重复人性化的 MIDI 被接受")

# 真实转换入口：通用导出保持精确，演奏入口处理，失败不覆盖既有结果。
class External:
    location_busy = False
    model_busy = False
    root = str(root)
    from threading import Lock
    _lock = Lock()


music = M.MusicTools(str(root), External())
source, destination = root / "score.mscx", root / "演奏.mid"
source.write_text("<museScore/>", encoding="utf-8")
original = fixture()
def export(_, target):
    target.write_bytes(original)

with patch.object(music, "_require", return_value="MuseScore.exe"), patch.object(music, "_export", side_effect=export):
    music.export(source, destination)
    assert destination.read_bytes() == original
    music.export_performance(source, destination, seed=42)
    assert H.is_current(destination) and len(pairs(destination.read_bytes())) == len(pairs(original))
    existing = destination.read_bytes()
    with patch.object(H, "humanize_file", side_effect=H.MidiError("bad file")):
        try:
            music.export_performance(source, destination)
        except M.MusicError:
            pass
        else:
            raise AssertionError("处理失败未报错")
    assert destination.read_bytes() == existing and not music.busy and not music.external.model_busy
    assert not list(root.glob("*.part"))

print("music humanize check OK")
