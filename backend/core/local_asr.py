# backend/core/local_asr.py
# 本地语音识别 - 使用 faster-whisper 在本机从视频音频生成带时间轴字幕

import os
from functools import lru_cache
from typing import Callable, Optional

from ..utils import get_logger


logger = get_logger("local_asr")

# 模型缓存，避免同一轮任务重复加载模型。
_MODEL_CACHE: dict[tuple[str, str, str, str, int], object] = {}


def default_asr_model_dir() -> str:
    """返回本地 Whisper 模型目录，默认放在 D:\\tools，避免污染项目目录"""
    tools_dir = os.environ.get("YTV_TOOLS_DIR") or "D:\\tools"
    return os.environ.get("YTV_ASR_MODEL_DIR") or os.path.join(tools_dir, "whisper-models")


def default_asr_model_name() -> str:
    """低配机器默认用 base，速度和准确率比 tiny 更均衡"""
    return os.environ.get("YTV_ASR_MODEL") or "base"


@lru_cache(maxsize=1)
def cuda_device_count() -> int:
    """返回 CTranslate2 可用的 CUDA 设备数量"""
    try:
        import ctranslate2
        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        return 0


def default_asr_device() -> str:
    """默认优先 GPU，没有 CUDA 时自动回退 CPU"""
    requested = (os.environ.get("YTV_ASR_DEVICE") or "auto").strip().lower()
    if requested and requested != "auto":
        return requested
    return "cuda" if cuda_device_count() > 0 else "cpu"


def default_asr_compute_type(device: str) -> str:
    """根据设备选择默认计算精度，兼顾速度和兼容性"""
    configured = os.environ.get("YTV_ASR_COMPUTE_TYPE")
    if configured:
        return configured
    return "float16" if device == "cuda" else "int8"


class LocalSpeechRecognizer:
    """本地语音识别器，默认优先 GPU，没有 GPU 时回退 CPU"""

    def __init__(
        self,
        model_name: Optional[str] = None,
        model_dir: Optional[str] = None,
        device: Optional[str] = None,
        compute_type: Optional[str] = None,
        cpu_threads: Optional[int] = None,
    ):
        """初始化本地识别参数，不在构造阶段加载模型"""
        self.model_name = model_name or default_asr_model_name()
        self.model_dir = model_dir or default_asr_model_dir()
        requested_device = device or os.environ.get("YTV_ASR_DEVICE") or "auto"
        normalized_device = str(requested_device).strip().lower()
        self.allow_cpu_fallback = normalized_device == "auto"
        self.device = default_asr_device() if normalized_device == "auto" else normalized_device
        self.compute_type = compute_type or default_asr_compute_type(self.device)
        self.cpu_threads = cpu_threads or min(4, max(1, os.cpu_count() or 1))

    def transcribe_video(
        self,
        video_path: str,
        language: Optional[str] = None,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> tuple[list[dict], str]:
        """识别视频音频并返回字幕条目和检测到的语言"""
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"本地识别视频不存在: {video_path}")

        try:
            segments, info = self._transcribe_with_model(video_path, language, progress_callback)
        except Exception as exc:
            if not self._should_fallback_to_cpu():
                raise
            logger.warning(f"GPU 本地识别失败，自动回退 CPU: {exc}")
            self.device = "cpu"
            self.compute_type = os.environ.get("YTV_ASR_CPU_COMPUTE_TYPE") or "int8"
            segments, info = self._transcribe_with_model(video_path, language, progress_callback)

        detected_language = str(getattr(info, "language", "") or language or "auto")
        duration = float(getattr(info, "duration", 0) or 0)
        entries: list[dict] = []
        for segment in segments:
            text = " ".join(str(getattr(segment, "text", "") or "").split())
            if not text:
                continue
            start = float(getattr(segment, "start", 0) or 0)
            end = float(getattr(segment, "end", start) or start)
            entries.append({
                "index": len(entries) + 1,
                "start": self._seconds_to_srt_time(start),
                "end": self._seconds_to_srt_time(max(end, start + 0.2)),
                "text": text,
            })
            if progress_callback and duration > 0:
                progress_callback(min(95.0, 5.0 + end / duration * 90.0))

        if progress_callback:
            progress_callback(100)
        if not entries:
            raise RuntimeError("本地语音识别没有识别到字幕内容")
        logger.info(f"本地识别完成: {len(entries)} 条字幕, language={detected_language}")
        return entries, detected_language

    def _transcribe_with_model(
        self,
        video_path: str,
        language: Optional[str],
        progress_callback: Optional[Callable[[float], None]],
    ):
        """加载模型并执行一次识别"""
        model = self._load_model()
        logger.info(f"本地识别字幕: model={self.model_name}, device={self.device}, compute={self.compute_type}")
        if progress_callback:
            progress_callback(5)

        return model.transcribe(
            video_path,
            language=language or None,
            vad_filter=True,
            beam_size=3,
            condition_on_previous_text=False,
        )

    def _should_fallback_to_cpu(self) -> bool:
        """判断当前识别失败时是否允许回退 CPU"""
        return self.allow_cpu_fallback and self.device == "cuda"

    def _load_model(self):
        """延迟加载 faster-whisper 模型，缺依赖时给出明确提示"""
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("缺少本地识别依赖 faster-whisper，请先安装到 D:\\tools 的 Python 环境") from exc

        key = (self.model_name, self.model_dir, self.device, self.compute_type, self.cpu_threads)
        if key in _MODEL_CACHE:
            return _MODEL_CACHE[key]

        os.makedirs(self.model_dir, exist_ok=True)
        model = WhisperModel(
            self.model_name,
            device=self.device,
            compute_type=self.compute_type,
            cpu_threads=self.cpu_threads,
            download_root=self.model_dir,
        )
        _MODEL_CACHE[key] = model
        return model

    def _seconds_to_srt_time(self, seconds: float) -> str:
        """把秒转换成 SRT 时间码"""
        milliseconds = max(0, int(round(seconds * 1000)))
        hours = milliseconds // 3600000
        minutes = (milliseconds % 3600000) // 60000
        secs = (milliseconds % 60000) // 1000
        millis = milliseconds % 1000
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
