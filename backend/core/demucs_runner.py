# backend/core/demucs_runner.py
# 本地 Demucs 启动器 - 复用 Demucs/CUDA 分离能力，并用 soundfile 保存 WAV，避开 torchaudio 新版 torchcodec 依赖问题

from pathlib import Path
import sys
from typing import Any


def _save_audio_with_soundfile(
    wav: Any,
    path: str | Path,
    samplerate: int,
    bitrate: int = 320,
    clip: str = "rescale",
    bits_per_sample: int = 16,
    as_float: bool = False,
    preset: int = 2,
) -> None:
    """保存 Demucs 输出音频；WAV/FLAC 走 soundfile，MP3 仍交给 Demucs 原实现"""
    from demucs import audio as demucs_audio
    import soundfile as sf

    output_path = Path(path)
    suffix = output_path.suffix.lower()
    if suffix not in {".wav", ".flac"}:
        demucs_audio._original_save_audio(wav, path, samplerate, bitrate, clip, bits_per_sample, as_float, preset)
        return

    clipped = demucs_audio.prevent_clip(wav, mode=clip).detach().cpu()
    if clipped.dim() == 1:
        samples = clipped.numpy()
    elif clipped.dim() == 2:
        # Demucs 使用 [channels, samples]，soundfile 需要 [samples, channels]。
        samples = clipped.transpose(0, 1).numpy()
    else:
        raise ValueError(f"不支持的音频维度: {tuple(clipped.shape)}")

    if as_float:
        subtype = "FLOAT"
    elif bits_per_sample == 24:
        subtype = "PCM_24"
    else:
        subtype = "PCM_16"
    sf.write(str(output_path), samples, samplerate, subtype=subtype)


def main() -> None:
    """补丁式启动 Demucs，保持原命令行参数兼容"""
    from demucs import audio as demucs_audio
    from demucs import separate as demucs_separate

    if not hasattr(demucs_audio, "_original_save_audio"):
        demucs_audio._original_save_audio = demucs_audio.save_audio
    demucs_audio.save_audio = _save_audio_with_soundfile
    demucs_separate.save_audio = _save_audio_with_soundfile
    demucs_separate.main(sys.argv[1:])


if __name__ == "__main__":
    main()
