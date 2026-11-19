"""隔离 Python 环境中的钢琴音频转录入口；不导入 OIT 或 tkinter。"""

import argparse
import os


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True)
    parser.add_argument("--midi", required=True)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    os.environ["MPLBACKEND"] = "Agg"
    os.environ["OMP_NUM_THREADS"] = str(max(1, args.threads))
    import onnxruntime as ort
    create_session = ort.InferenceSession

    def cpu_session(model_path, **kwargs):
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, args.threads)
        options.inter_op_num_threads = 1
        return create_session(model_path, sess_options=options, **kwargs)

    ort.InferenceSession = cpu_session
    from basic_pitch.inference import predict
    _, midi, _ = predict(args.audio)
    midi.write(args.midi)


if __name__ == "__main__":
    main()
