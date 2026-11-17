"""在 Partitura 独立环境中把两份 MIDI 压缩成演奏差异数据。"""

import argparse
import json
import partitura
import mido


def notes(path):
    performance = partitura.load_performance_midi(path)
    array = performance.note_array()
    fields = set(array.dtype.names or ())
    if not {"onset_sec", "duration_sec", "pitch"} <= fields:
        raise ValueError("Partitura MIDI note events are missing timing or pitch fields")
    return sorted((float(note["onset_sec"]), float(note["duration_sec"]), int(note["pitch"]),
                   int(note["velocity"]) if "velocity" in fields else 0,
                   int(note["track"]) if "track" in fields else 0) for note in array)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", required=True)
    parser.add_argument("--performance", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    ref, perf = notes(args.reference), notes(args.performance)
    ref_duration = max((n[0] + n[1] for n in ref), default=0)
    perf_duration = max((n[0] + n[1] for n in perf), default=0)
    tempo_ratio = perf_duration / ref_duration if ref_duration and perf_duration else 1.0
    unused = set(range(len(perf)))
    matched, missing, extras = [], [], []
    for r in ref:
        candidates = [i for i in unused if perf[i][2] == r[2]]
        if not candidates:
            missing.append(r)
            continue
        i = min(candidates, key=lambda n: abs(perf[n][0] / tempo_ratio - r[0]))
        p = perf[i]
        if abs(p[0] / tempo_ratio - r[0]) > max(0.75, ref_duration * 0.01):
            missing.append(r)
            continue
        unused.remove(i)
        matched.append((r, p))
    extras = [perf[i] for i in sorted(unused)]
    onset = [p[0] / tempo_ratio - r[0] for r, p in matched]
    duration = [p[1] / tempo_ratio - r[1] for r, p in matched]
    result = {
        "schema": 1,
        "summary": {"reference_notes": len(ref), "performance_notes": len(perf),
                    "matched": len(matched), "missing": len(missing), "extra": len(extras),
                    "performance_duration_ms": round(max(perf_duration, mido.MidiFile(args.performance).length) * 1000),
                    "tempo_ratio_percent": round(tempo_ratio * 100),
                    "onset_error_ms_mean": round(sum(onset) / len(onset) * 1000) if onset else None,
                    "onset_error_ms_abs_mean": round(sum(abs(v) for v in onset) / len(onset) * 1000) if onset else None,
                    "duration_error_ms_abs_mean": round(sum(abs(v) for v in duration) / len(duration) * 1000) if duration else None},
        "largest_timing_errors_ms": sorted((round(v * 1000) for v in onset), key=abs, reverse=True)[:8],
        "missing_pitch_counts": {},
        "extra_pitch_counts": {},
    }
    for group, target in ((missing, result["missing_pitch_counts"]), (extras, result["extra_pitch_counts"])):
        for event in group:
            key = str(event[2])
            target[key] = target.get(key, 0) + 1
    with open(args.output, "w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
