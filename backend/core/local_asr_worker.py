# backend/core/local_asr_worker.py
# 本地识别 CUDA 子进程入口：隔离 native 崩溃，避免拖垮后端主进程

import argparse
import json
import traceback
from typing import Any

from .local_asr import ASR_WORKER_EVENT_PREFIX, LocalSpeechRecognizer


def _emit(event: dict[str, Any]) -> None:
    """输出机器可解析事件，父进程据此接收进度和最终字幕"""
    print(f"{ASR_WORKER_EVENT_PREFIX}{json.dumps(event, ensure_ascii=False)}", flush=True)


def _build_parser() -> argparse.ArgumentParser:
    """构造本地识别子进程命令行参数"""
    parser = argparse.ArgumentParser(description="本地识别 CUDA worker")
    parser.add_argument("--video-path", required=True)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--compute-type", required=True)
    parser.add_argument("--cpu-threads", type=int, required=True)
    parser.add_argument("--beam-size", type=int)
    parser.add_argument("--language")
    parser.add_argument("--gap-rescue", type=int, choices=[0, 1])
    return parser


def main() -> int:
    """执行一次本地识别，并把结果通过 stdout 返回给父进程"""
    args = _build_parser().parse_args()
    try:
        recognizer = LocalSpeechRecognizer(
            model_name=args.model_name,
            model_dir=args.model_dir,
            device=args.device,
            compute_type=args.compute_type,
            cpu_threads=args.cpu_threads,
            beam_size=args.beam_size,
            # 补漏开关由父进程透传：模式3 时间轴识别传 0 关闭，加速 GPU 子进程识别
            gap_rescue=(None if args.gap_rescue is None else bool(args.gap_rescue)),
        )

        def on_progress(value: float) -> None:
            """把子进程识别进度转发给父进程"""
            _emit({"type": "progress", "value": float(value)})

        entries, language = recognizer.transcribe_video(
            args.video_path,
            language=args.language,
            progress_callback=on_progress,
        )
        _emit({"type": "result", "language": language, "entries": entries})
        return 0
    except Exception as exc:
        _emit({
            "type": "error",
            "error_type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(limit=8),
        })
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
