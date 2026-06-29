# backend/core/index_tts2_bridge.py
# IndexTTS2 本地桥接脚本 - 在用户配置的 IndexTTS2 Python 环境里执行官方推理入口

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any


def _bool_flag(value: bool) -> bool:
    """保留布尔值，便于参数组装时语义清楚"""
    return bool(value)


def _resolve_path(path: str, base_dir: str) -> str:
    """把相对路径解析到 IndexTTS2 项目目录下"""
    normalized = str(path or "").strip()
    if not normalized:
        return ""
    if os.path.isabs(normalized):
        return os.path.abspath(os.path.expanduser(normalized))
    return os.path.abspath(os.path.join(base_dir, normalized))


def _parse_emo_vector(value: str) -> list[float] | None:
    """解析情感向量，支持 JSON 数组或逗号分隔数字"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [float(item) for item in parsed]
    except Exception:
        pass
    return [float(item.strip()) for item in text.split(",") if item.strip()]


def _build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器"""
    parser = argparse.ArgumentParser(description="IndexTTS2 bridge")
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--text-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--speaker-audio", required=True)
    parser.add_argument("--model-dir", default="checkpoints")
    parser.add_argument("--cfg-path", default="")
    parser.add_argument("--emo-method", default="speaker", choices=["speaker", "audio", "vector", "text"])
    parser.add_argument("--emo-audio", default="")
    parser.add_argument("--emo-text", default="")
    parser.add_argument("--emo-vector", default="")
    parser.add_argument("--emo-alpha", type=float, default=1.0)
    parser.add_argument("--max-text-tokens-per-segment", type=int, default=120)
    parser.add_argument("--max-mel-tokens", type=int, default=1500)
    parser.add_argument("--top-p", type=float, default=0.8)
    parser.add_argument("--top-k", type=int, default=30)
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--length-penalty", type=float, default=0.0)
    parser.add_argument("--num-beams", type=int, default=3)
    parser.add_argument("--repetition-penalty", type=float, default=10.0)
    parser.add_argument("--do-sample", action="store_true")
    parser.add_argument("--use-random", action="store_true")
    parser.add_argument("--use-fp16", action="store_true")
    parser.add_argument("--use-cuda-kernel", action="store_true")
    parser.add_argument("--use-deepspeed", action="store_true")
    return parser


def main() -> int:
    """执行 IndexTTS2 推理并返回进程退出码"""
    args = _build_parser().parse_args()
    repo_dir = os.path.abspath(os.path.expanduser(args.repo_dir))
    if not os.path.isdir(repo_dir):
        raise FileNotFoundError(f"IndexTTS2 项目目录不存在: {repo_dir}")
    os.chdir(repo_dir)
    if repo_dir not in sys.path:
        sys.path.insert(0, repo_dir)

    from indextts.infer_v2 import IndexTTS2  # noqa: WPS433

    with open(args.text_file, "r", encoding="utf-8") as file:
        text = file.read().strip()
    if not text:
        raise ValueError("待合成文本不能为空")

    model_dir = _resolve_path(args.model_dir, repo_dir)
    cfg_path = _resolve_path(args.cfg_path, repo_dir) if args.cfg_path else os.path.join(model_dir, "config.yaml")
    speaker_audio = _resolve_path(args.speaker_audio, repo_dir)
    if not os.path.exists(speaker_audio):
        raise FileNotFoundError(f"发音参考音频不存在: {speaker_audio}")

    tts = IndexTTS2(
        cfg_path=cfg_path,
        model_dir=model_dir,
        use_fp16=_bool_flag(args.use_fp16),
        use_cuda_kernel=_bool_flag(args.use_cuda_kernel),
        use_deepspeed=_bool_flag(args.use_deepspeed),
    )

    emo_audio_prompt = None
    emo_vector = None
    use_emo_text = False
    emo_text = None
    if args.emo_method == "audio":
        emo_audio_prompt = _resolve_path(args.emo_audio, repo_dir) if args.emo_audio else None
    elif args.emo_method == "vector":
        emo_vector = _parse_emo_vector(args.emo_vector)
    elif args.emo_method == "text":
        use_emo_text = True
        emo_text = args.emo_text or text

    generation_kwargs: dict[str, Any] = {
        "do_sample": _bool_flag(args.do_sample),
        "top_p": args.top_p,
        "top_k": args.top_k if args.top_k > 0 else None,
        "temperature": args.temperature,
        "length_penalty": args.length_penalty,
        "num_beams": args.num_beams,
        "repetition_penalty": args.repetition_penalty,
        "max_mel_tokens": args.max_mel_tokens,
    }

    result = tts.infer(
        spk_audio_prompt=speaker_audio,
        text=text,
        output_path=args.output,
        emo_audio_prompt=emo_audio_prompt,
        emo_alpha=args.emo_alpha,
        emo_vector=emo_vector,
        use_emo_text=use_emo_text,
        emo_text=emo_text,
        use_random=_bool_flag(args.use_random),
        verbose=False,
        max_text_tokens_per_segment=args.max_text_tokens_per_segment,
        **generation_kwargs,
    )
    output_path = result or args.output
    if output_path != args.output and os.path.exists(output_path) and not os.path.exists(args.output):
        import shutil

        shutil.copyfile(output_path, args.output)
    if not os.path.exists(args.output):
        raise RuntimeError(f"IndexTTS2 未生成输出文件: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
