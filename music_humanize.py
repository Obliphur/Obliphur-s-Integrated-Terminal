"""演奏 MIDI 的保守人性化：标准库 SMF 读写、跨轨速度映射与受限随机微差。"""

from bisect import bisect_left, bisect_right
from collections import defaultdict, deque
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import random
import struct

import config


class MidiError(ValueError):
    """不支持或不完整的 MIDI；由音乐工具层翻译后展示。"""


@dataclass
class Event:
    tick: int
    data: bytes


def _vlq(data, pos):
    value = 0
    for _ in range(4):
        if pos >= len(data):
            raise MidiError("truncated VLQ")
        byte = data[pos]
        value, pos = (value << 7) | (byte & 127), pos + 1
        if byte < 128:
            return value, pos
    raise MidiError("invalid VLQ")


def _encode(value):
    if not 0 <= value <= 0x0fffffff:
        raise MidiError("delta out of range")
    out = [value & 127]
    while value > 127:
        value >>= 7
        out.append(128 | (value & 127))
    return bytes(reversed(out))


def read_midi(data):
    """仅处理 MuseScore 常用的格式 0/1、PPQ；明确拒绝格式 2 与 SMPTE。"""
    if len(data) > config.MUSIC_HUMANIZE_MAX_BYTES or len(data) < 14 or data[:4] != b"MThd":
        raise MidiError("invalid header or size")
    length = int.from_bytes(data[4:8], "big")
    fmt, count, ppq = struct.unpack(">HHH", data[8:14])
    if length < 6 or fmt not in (0, 1) or not count or (fmt == 0 and count != 1) or not 0 < ppq < 0x8000:
        raise MidiError("unsupported format or time division")
    pos, tracks, total = 8 + length, [], 0
    for _ in range(count):
        if data[pos:pos + 4] != b"MTrk" or pos + 8 > len(data):
            raise MidiError("missing track")
        size = int.from_bytes(data[pos + 4:pos + 8], "big")
        body, pos = data[pos + 8:pos + 8 + size], pos + 8 + size
        if len(body) != size:
            raise MidiError("truncated track")
        cursor, tick, running, events = 0, 0, None, []
        while cursor < len(body):
            delta, cursor = _vlq(body, cursor)
            tick += delta
            if cursor >= len(body):
                raise MidiError("missing event")
            status = body[cursor]
            if status >= 128:
                cursor += 1
            elif running is not None:
                status = running
            else:
                raise MidiError("missing running status")
            if 0x80 <= status < 0xf0:
                running = status
                size = 1 if status & 0xf0 in (0xc0, 0xd0) else 2
                payload = body[cursor:cursor + size]
                if len(payload) != size or any(x >= 128 for x in payload):
                    raise MidiError("invalid channel event")
                message, cursor = bytes([status]) + payload, cursor + size
            elif status in (0xff, 0xf0, 0xf7):
                running = None
                start = cursor
                if status == 0xff:
                    if cursor >= len(body) or body[cursor] >= 128:
                        raise MidiError("invalid meta type")
                    cursor += 1
                size, payload_pos = _vlq(body, cursor)
                cursor = payload_pos + size
                if cursor > len(body):
                    raise MidiError("truncated meta or sysex")
                message = bytes([status]) + body[start:cursor]
                if message[:2] == b"\xff\x51" and (size != 3 or int.from_bytes(body[payload_pos:cursor], "big") == 0):
                    raise MidiError("invalid tempo")
                if message[:2] == b"\xff\x2f" and (size != 0 or cursor != len(body)):
                    raise MidiError("invalid end of track")
            else:
                raise MidiError("unsupported event")
            total += 1
            if total > config.MUSIC_HUMANIZE_MAX_EVENTS:
                raise MidiError("too many events")
            events.append(Event(tick, message))
        if not events or events[-1].data != b"\xff\x2f\x00":
            raise MidiError("missing end of track")
        tracks.append(events)
    if pos != len(data):
        raise MidiError("trailing chunks")
    return fmt, ppq, tracks


def write_midi(fmt, ppq, tracks):
    chunks = [b"MThd" + struct.pack(">IHHH", 6, fmt, len(tracks), ppq)]
    for events in tracks:
        # 同一 tick 保持原顺序；重触音在处理阶段留出一个 tick 的松键间隔。
        body, previous = bytearray(), 0
        for event in sorted(events, key=lambda e: e.tick):
            body.extend(_encode(event.tick - previous) + event.data)
            previous = event.tick
        chunks.append(b"MTrk" + struct.pack(">I", len(body)) + body)
    return b"".join(chunks)


class TempoMap:
    """把跨轨 tempo 事件组成全局映射，毫秒偏差不随 BPM 或 PPQ 改变。"""

    def __init__(self, ppq, tracks):
        tempos = {0: 500000}
        for track in tracks:
            for event in track:
                if event.data[:2] == b"\xff\x51":
                    tempos[event.tick] = int.from_bytes(event.data[-3:], "big")
        self.ticks, self.seconds, self.rates = [], [], []
        seconds, previous, rate = 0.0, 0, 0.5 / ppq
        for tick, tempo in sorted(tempos.items()):
            seconds += (tick - previous) * rate
            self.ticks.append(tick)
            self.seconds.append(seconds)
            rate, previous = tempo / 1000000 / ppq, tick
            self.rates.append(rate)

    def seconds_at(self, tick):
        index = bisect_right(self.ticks, tick) - 1
        return self.seconds[index] + (tick - self.ticks[index]) * self.rates[index]

    def tick_at(self, seconds):
        seconds = max(0, seconds)
        index = bisect_right(self.seconds, seconds) - 1
        return max(0, round(self.ticks[index] + (seconds - self.seconds[index]) / self.rates[index]))


def profile():
    return {key: getattr(config, key) for key in (
        "MUSIC_HUMANIZE_VERSION", "MUSIC_HUMANIZE_DRIFT_MS", "MUSIC_HUMANIZE_DRIFT_BEATS",
        "MUSIC_HUMANIZE_GROUP_MS", "MUSIC_HUMANIZE_FINGER_MS", "MUSIC_HUMANIZE_RELEASE_MS",
        "MUSIC_HUMANIZE_VELOCITY", "MUSIC_HUMANIZE_SIGMA_LIMIT", "MUSIC_HUMANIZE_IOI_FRACTION",
        "MUSIC_HUMANIZE_RELEASE_FRACTION")}


def _marker():
    digest = hashlib.sha256(json.dumps(profile(), sort_keys=True).encode()).hexdigest()[:16]
    payload = ("OIT-humanize:" + digest).encode("ascii")
    return b"\xff\x7f" + _encode(len(payload)) + payload


def is_current(path):
    """缓存须含当前参数签名，旧版机械 MIDI 或损坏缓存重新生成。"""
    try:
        if Path(path).stat().st_size > config.MUSIC_HUMANIZE_MAX_BYTES:
            return False
        _, _, tracks = read_midi(Path(path).read_bytes())
        return any(e.data == _marker() for t in tracks for e in t)
    except (OSError, MidiError):
        return False


def humanize(data, *, seed=None):
    """返回演奏副本；种子相同时结果一致，默认每次新生成取独立随机种子。"""
    settings = profile()
    if (any(not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in settings.values())
            or config.MUSIC_HUMANIZE_DRIFT_BEATS <= 0 or config.MUSIC_HUMANIZE_IOI_FRACTION >= .5
            or config.MUSIC_HUMANIZE_RELEASE_FRACTION >= 1):
        raise MidiError("invalid humanization profile")
    fmt, ppq, tracks = read_midi(data)
    if any(e.data.startswith(b"\xff\x7f") and b"OIT-humanize:" in e.data for t in tracks for e in t):
        raise MidiError("MIDI already humanized; export again from score")
    tempo, notes = TempoMap(ppq, tracks), []
    # 按轨道和通道配对；允许同音重复，用 FIFO 处理原谱已有的重叠。
    for track in tracks:
        active = defaultdict(deque)
        for event in track:
            kind = event.data[0] & 0xf0
            if kind not in (0x80, 0x90) or event.data[0] & 15 == 9:
                continue
            key = (event.data[0] & 15, event.data[1])
            if kind == 0x90 and event.data[2]:
                active[key].append(event)
            elif active[key]:
                on = active[key].popleft()
                notes.append((on, event, on.tick, event.tick))
            # MuseScore 会在辅助通道写没有对应起音的 note_on(velocity=0)；
            # MIDI 允许这样的复位松键，原样保留，不当作需要扰动的音符。
        if any(active.values()):
            raise MidiError("unpaired note-on")
    if not notes:
        raise MidiError("no pitched notes")
    rng, knots, groups = random.Random(seed), {}, defaultdict(list)
    for note in notes:
        groups[note[2]].append(note)

    def bounded(sigma):
        limit = config.MUSIC_HUMANIZE_SIGMA_LIMIT * sigma
        return max(-limit, min(limit, rng.gauss(0, sigma)))

    def drift(tick):
        position = tick / ppq / config.MUSIC_HUMANIZE_DRIFT_BEATS
        left = int(position)
        for index in (left, left + 1):
            if index not in knots:
                knots[index] = rng.uniform(-config.MUSIC_HUMANIZE_DRIFT_MS, config.MUSIC_HUMANIZE_DRIFT_MS) / 1000
        # 三次平滑插值，不伪造乐句边界，也不累加随机游走导致整体速度漂移。
        weight = position - left
        weight = weight * weight * (3 - 2 * weight)
        return knots[left] * (1 - weight) + knots[left + 1] * weight

    ticks = sorted(groups)
    for index, tick in enumerate(ticks):
        seconds = tempo.seconds_at(tick)
        # 起音偏差不超过相邻组间隔的 20%，保护倚音、快速音群与原有琶音。
        gaps = [abs(tempo.seconds_at(ticks[j]) - seconds) for j in (index - 1, index + 1) if 0 <= j < len(ticks)]
        limit = min(gaps, default=float("inf")) * config.MUSIC_HUMANIZE_IOI_FRACTION
        shared = drift(tick) + bounded(config.MUSIC_HUMANIZE_GROUP_MS) / 1000
        velocity_shared = bounded(config.MUSIC_HUMANIZE_VELOCITY / 2)
        for on, off, old_on, old_off in groups[tick]:
            duration = tempo.seconds_at(old_off) - seconds
            shift = max(-limit, min(limit, shared + bounded(config.MUSIC_HUMANIZE_FINGER_MS) / 1000))
            new_on = tempo.tick_at(seconds + shift)
            # 与起音一起平移，再对松键加轻微变化；短音的松键变化不超过原时值 15%。
            release_limit = duration * config.MUSIC_HUMANIZE_RELEASE_FRACTION
            release = max(-release_limit, min(release_limit, bounded(config.MUSIC_HUMANIZE_RELEASE_MS) / 1000))
            new_off = tempo.tick_at(tempo.seconds_at(new_on) + duration + release)
            # MuseScore 有时导出零 tick 的装饰音；给它至少一个 tick，避免松键排到起音前。
            on.tick, off.tick = new_on, max(new_on + 1, new_off)
            velocity = round(on.data[2] + velocity_shared + bounded(config.MUSIC_HUMANIZE_VELOCITY))
            on.data = on.data[:2] + bytes([max(1, min(127, velocity))])
    repeated = defaultdict(list)
    for note in notes:
        repeated[(note[0].data[0] & 15, note[0].data[1])].append(note)
    for sequence in repeated.values():
        starts = sorted({n[2] for n in sequence})
        next_on = {}
        for on, _, old_on, _ in sequence:
            next_on[old_on] = min(next_on.get(old_on, on.tick), on.tick)
        for previous in sequence:
            # 也覆盖同 tick 的跨轨重音：每个松键都不能越过原本不重叠的后续起音。
            index = bisect_left(starts, previous[3])
            if index < len(starts):
                end = next_on[starts[index]] - 1
                previous[1].tick = min(previous[1].tick, max(previous[0].tick + 1, end))
    for track in tracks:
        track[-1].tick = max(e.tick for e in track)
        def priority(event):
            data = event.data
            if data == b"\xff\x2f\x00":
                return 3
            if data[0] & 0xf0 in (0x80, 0x90) and data[0] & 15 == 9:
                return 1
            if data[0] & 0xf0 == 0x80 or (data[0] & 0xf0 == 0x90 and data[2] == 0):
                return 0
            return 2 if data[0] & 0xf0 == 0x90 else 1
        track.sort(key=lambda e: (e.tick, priority(e)))
    tracks[0].insert(0, Event(0, _marker()))
    return write_midi(fmt, ppq, tracks)


def humanize_file(path, *, seed=None):
    """只处理转换临时目录内文件；验证与原子替换由 MusicTools 负责。"""
    path = Path(path)
    if path.stat().st_size > config.MUSIC_HUMANIZE_MAX_BYTES:
        raise MidiError("file too large")
    data = humanize(path.read_bytes(), seed=seed)
    path.write_bytes(data)
