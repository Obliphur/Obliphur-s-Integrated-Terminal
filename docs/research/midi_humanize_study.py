"""小样本考察：固定版本 (n)ASAP 的和弦起音离散与力度差；不分发原始演奏。"""

import csv
import json
from collections import defaultdict
from pathlib import Path
import statistics
import sys
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import tempstore
import updater


REVISION = "4097b45757bed854818cf87e77b92323ebf90615"
COMPOSERS = ("Bach", "Beethoven", "Chopin", "Debussy", "Schumann")


def quantile(values, fraction):
    values = sorted(values)
    index = (len(values) - 1) * fraction
    lo = int(index)
    return round(values[lo] + (values[min(lo + 1, len(values) - 1)] - values[lo]) * (index - lo), 3)


def collect():
    work = Path(tempstore.session("midi-humanize-study"))

    def read(name):
        # GitHub API 内容接口走统一下载器，固定版本与下载上限。
        destination = work / "download.csv"
        updater.download({"url": "https://api.github.com/repos/CPJKU/asap-dataset/contents/" + quote(name)
                          + "?ref=" + REVISION, "accept": "application/vnd.github.raw+json"},
                         str(destination), max_download=8 * 1024 * 1024)
        with destination.open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream))

    metadata = read("metadata.csv")
    samples = []
    all_spans, all_residuals = [], []
    for composer in COMPOSERS:
        seen = set()
        for row in metadata:
            if row["composer"] != composer or row["robust_note_alignment"] != "1.0" or row["title"] in seen:
                continue
            seen.add(row["title"])
            folder = row["parangonada_data_folder"]
            score = {r["id"]: r for r in read(folder + "/part.csv")}
            performance = {r["id"]: r for r in read(folder + "/ppart.csv")}
            groups = defaultdict(list)
            for match in read(folder + "/align.csv"):
                if match["matchtype"] != "0":
                    continue
                s, p = score.get(match["partid"]), performance.get(match["ppartid"])
                if s and p and float(s["duration_beat"]) > 0:
                    groups[float(s["onset_beat"])].append(p)
            spans, residuals = [], []
            for notes in groups.values():
                if len(notes) < 2:
                    continue
                onsets = [float(n["onset_sec"]) for n in notes]
                spans.append((max(onsets) - min(onsets)) * 1000)
                velocities = [int(n["velocity"]) for n in notes]
                center = statistics.median(velocities)
                residuals.extend(abs(v - center) for v in velocities)
            if not spans:
                raise ValueError("样本没有对齐的和弦: " + row["midi_performance"])
            all_spans.extend(spans)
            all_residuals.extend(residuals)
            sample = {"composer": composer, "performance": row["midi_performance"],
                      "chords": len(spans), "span_median_ms": quantile(spans, .5),
                      "span_p90_ms": quantile(spans, .9),
                      "over_100ms": sum(s > 100 for s in spans),
                      "velocity_deviation_median": quantile(residuals, .5)}
            samples.append(sample)
            print(json.dumps(sample, ensure_ascii=False), file=sys.stderr, flush=True)
            if len(seen) == 2:
                break
    narrow = [s for s in all_spans if s <= 100]
    return {"repository": "https://github.com/CPJKU/asap-dataset", "revision": REVISION,
            "license": "CC BY-NC-SA 4.0", "selection": "metadata order; up to first two robust=1.0 distinct titles per composer",
            "samples": samples, "pooled": {"chords": len(all_spans),
                "span_median_ms": quantile(all_spans, .5), "span_p90_ms": quantile(all_spans, .9),
                "over_100ms": len(all_spans) - len(narrow),
                "span_median_within_100ms": quantile(narrow, .5),
                "span_p90_within_100ms": quantile(narrow, .9),
                "velocity_deviation_median": quantile(all_residuals, .5)}}


if __name__ == "__main__":
    print(json.dumps(collect(), ensure_ascii=False, indent=2))
