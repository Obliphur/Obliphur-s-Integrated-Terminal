"""隔离 Python 环境中的钢琴音频转录入口；不导入 OIT 或 tkinter。"""

import argparse
import os


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True)
    parser.add_argument("--midi", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--threads", type=int, required=True)
    args = parser.parse_args()
    # 禁止第三方库自行从 HOME 下载权重，必须使用 OIT 校验后的本地文件。
    if not os.path.isfile(args.checkpoint):
        raise FileNotFoundError(args.checkpoint)
    os.environ["MPLBACKEND"] = "Agg"
    import numpy as np
    # 上游旧版后处理仍使用旧的 NumPy 类型别名，仅在此隔离进程兼容。
    for key, value in (("float", float), ("int", int), ("complex", complex)):
        if key not in np.__dict__:
            setattr(np, key, value)
    import torch
    torch.set_num_threads(max(1, args.threads))
    import librosa
    from piano_transcription_inference import PianoTranscription, sample_rate
    audio, _ = librosa.load(args.audio, sr=sample_rate, mono=True)
    model = PianoTranscription(device="cpu", checkpoint_path=args.checkpoint)
    model.transcribe(audio, args.midi)


if __name__ == "__main__":
    main()
