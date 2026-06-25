# backend/core/local_asr.py
# 本地语音识别 - 使用 faster-whisper 在本机从视频音频生成带时间轴字幕

import json
import os
import queue
import re
import subprocess
import sys
import site
import tempfile
import threading
import time
from functools import lru_cache
from types import SimpleNamespace
from typing import Any, Callable, Optional

from .tooling import get_ffmpeg_command
from ..utils import get_logger


logger = get_logger("local_asr")
TERMINAL_PUNCTUATION = "，。、！？；,.!?;:："
# Whisper 在无人声段常见的幻觉句式，补漏识别时直接丢弃
HALLUCINATION_PHRASES = (
    "ご視聴ありがとう",
    "チャンネル登録",
    "次の動画でお会いしましょう",
    "字幕作成者",
    "感谢观看",
    "请订阅",
    "Thanks for watching",
    "Subscribe to",
)
LEADING_FRAGMENT_WORDS = {
    "a",
    "an",
    "the",
    "to",
    "of",
    "in",
    "on",
    "at",
    "for",
    "with",
    "and",
    "or",
    "but",
    "so",
    "if",
    "when",
    "while",
    "that",
    "this",
    "these",
    "those",
}

# 模型缓存，避免同一轮任务重复加载模型；不同运行时会带上各自模型路径。
_MODEL_CACHE: dict[tuple[Any, ...], object] = {}
# CUDA 一旦出现运行级错误，短时间内继续使用 CUDA 往往会反复失败。
_CUDA_ASR_DISABLED = False
_CUDA_ASR_DISABLED_UNTIL = 0.0
_CUDA_ASR_DISABLED_REASON = ""
_CUDA_DLL_HANDLES: list[Any] = []
_CUDA_DLL_REGISTERED_DIRS: set[str] = set()
CUDA_DISABLED_FLAG_NAME = "asr-cuda-disabled.flag"
ASR_WORKER_EVENT_PREFIX = "__YTV_ASR_WORKER__"
ASR_INPUT_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".opus", ".ogg"}
WHISPER_ASR_MODEL_NAMES = {"tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"}
SENSEVOICE_MODEL_NAME = "sensevoice"
QWEN3_ASR_MODEL_NAME = "qwen3-asr"
LOCAL_ASR_MODEL_NAMES = WHISPER_ASR_MODEL_NAMES | {SENSEVOICE_MODEL_NAME, QWEN3_ASR_MODEL_NAME}
CUDA_RUNTIME_DLL_NAMES = (
    "cublas64_12.dll",
    "cublasLt64_12.dll",
    "cudnn64_9.dll",
    "cudnn_ops64_9.dll",
    "cudnn_cnn64_9.dll",
    "nvrtc64_120_0.dll",
)


def default_asr_model_dir() -> str:
    """返回本地 Whisper 模型目录，默认放在 D:\\tools，避免污染项目目录"""
    tools_dir = os.environ.get("YTV_TOOLS_DIR") or "D:\\tools"
    return os.environ.get("YTV_ASR_MODEL_DIR") or os.path.join(tools_dir, "whisper-models")


def default_asr_model_name() -> str:
    """低配机器默认用 base，速度和准确率比 tiny 更均衡"""
    return os.environ.get("YTV_ASR_MODEL") or "base"


def default_data_root() -> str:
    """返回后端可写数据目录根路径，开发环境默认项目根目录"""
    return os.environ.get("YTV_DATA_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


def _site_package_roots() -> tuple[str, ...]:
    """收集当前 Python 的 site-packages 目录，用来查找 NVIDIA 官方 wheel 带的 CUDA DLL"""
    roots: list[str] = []

    def add(path: str) -> None:
        """加入存在的目录并去重"""
        if not path:
            return
        normalized = os.path.abspath(os.path.expanduser(str(path)))
        if normalized and os.path.isdir(normalized) and normalized not in roots:
            roots.append(normalized)

    try:
        for path in site.getsitepackages():
            add(path)
    except Exception:
        pass
    try:
        add(site.getusersitepackages())
    except Exception:
        pass
    add(os.path.join(os.path.dirname(sys.executable), "Lib", "site-packages"))
    for path in sys.path:
        if str(path or "").lower().endswith("site-packages"):
            add(path)
    return tuple(roots)


@lru_cache(maxsize=1)
def cuda_dll_search_dirs() -> tuple[str, ...]:
    """返回 CTranslate2 CUDA 运行时 DLL 目录，兼容 pip NVIDIA 包和系统 CUDA 安装"""
    dirs: list[str] = []

    def add(path: str) -> None:
        """加入存在的 DLL 目录并去重"""
        if not path:
            return
        normalized = os.path.abspath(os.path.expanduser(str(path)))
        if normalized and os.path.isdir(normalized) and normalized not in dirs:
            dirs.append(normalized)

    extra_dirs = str(os.environ.get("YTV_ASR_CUDA_DLL_DIRS") or "")
    for path in extra_dirs.split(os.pathsep):
        add(path)

    for root in _site_package_roots():
        add(os.path.join(root, "ctranslate2"))
        add(os.path.join(root, "nvidia", "cublas", "bin"))
        add(os.path.join(root, "nvidia", "cudnn", "bin"))
        add(os.path.join(root, "nvidia", "cuda_nvrtc", "bin"))

    cuda_path = os.environ.get("CUDA_PATH") or os.environ.get("CUDA_HOME")
    if cuda_path:
        add(os.path.join(cuda_path, "bin"))
    return tuple(dirs)


def _prepend_env_paths(env: dict[str, str], paths: tuple[str, ...]) -> None:
    """把 CUDA DLL 目录前置到 PATH，保证子进程按同一套依赖启动"""
    if not paths:
        return
    path_key = "PATH"
    if os.name == "nt":
        for key in ("PATH", "Path", "path"):
            if key in env:
                path_key = key
                break
    current = str(env.get(path_key) or env.get("PATH") or env.get("Path") or "")
    existing = {
        os.path.normcase(os.path.abspath(part))
        for part in current.split(os.pathsep)
        if part
    }
    prepend = [
        path
        for path in paths
        if os.path.normcase(os.path.abspath(path)) not in existing
    ]
    if not prepend:
        return
    env[path_key] = os.pathsep.join(prepend + ([current] if current else []))
    if os.name == "nt" and path_key != "PATH":
        env["PATH"] = env[path_key]


def configure_cuda_dll_search_paths(env: Optional[dict[str, str]] = None) -> tuple[str, ...]:
    """注册 CUDA DLL 搜索路径，并可同步写入子进程环境变量"""
    dirs = cuda_dll_search_dirs()
    if os.name == "nt":
        for directory in dirs:
            if directory in _CUDA_DLL_REGISTERED_DIRS:
                continue
            try:
                if hasattr(os, "add_dll_directory"):
                    _CUDA_DLL_HANDLES.append(os.add_dll_directory(directory))
                _CUDA_DLL_REGISTERED_DIRS.add(directory)
            except OSError as exc:
                logger.warning(f"注册 CUDA DLL 目录失败: {directory}, {exc}")
    _prepend_env_paths(os.environ, dirs)
    if env is not None:
        _prepend_env_paths(env, dirs)
    return dirs


def cuda_runtime_dependency_report() -> dict[str, Any]:
    """返回 CUDA 运行时 DLL 查找结果，方便用户从日志判断 GPU 为什么不能用"""
    dirs = configure_cuda_dll_search_paths()
    found: dict[str, str] = {}
    missing: list[str] = []
    for dll_name in CUDA_RUNTIME_DLL_NAMES:
        dll_path = next(
            (os.path.join(directory, dll_name) for directory in dirs if os.path.exists(os.path.join(directory, dll_name))),
            "",
        )
        if dll_path:
            found[dll_name] = dll_path
        else:
            missing.append(dll_name)
    return {"dirs": list(dirs), "found": found, "missing": missing}


def asr_cuda_disabled_flag_path() -> str:
    """返回 CUDA ASR 熔断标记路径，Electron 检测到原生崩溃后会写入该文件"""
    return os.environ.get("YTV_ASR_CUDA_DISABLED_FLAG") or os.path.join(default_data_root(), "data", CUDA_DISABLED_FLAG_NAME)


def _asr_force_cuda_enabled() -> bool:
    """读取强制 CUDA 开关，调试时允许临时忽略熔断标记"""
    return str(os.environ.get("YTV_ASR_FORCE_CUDA") or "").strip().lower() in {"1", "true", "yes", "on"}


def _asr_cuda_marker_ttl_seconds() -> float:
    """读取 CUDA 熔断标记有效期，过期后自动恢复 GPU 探测"""
    try:
        value = float(os.environ.get("YTV_ASR_CUDA_DISABLED_TTL_SECONDS", "1800"))
    except (TypeError, ValueError):
        value = 1800.0
    return max(0.0, value)


def _remove_asr_cuda_disabled_marker(flag_path: str) -> None:
    """删除过期 CUDA 熔断标记，失败只记录日志不阻断识别"""
    try:
        os.remove(flag_path)
        logger.info(f"本地识别 CUDA 熔断标记已过期并清理: {flag_path}")
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning(f"清理 CUDA 熔断标记失败: {exc}")


def read_asr_cuda_disabled_reason() -> str:
    """读取 CUDA 熔断标记内容，日志里展示 CPU 回退原因"""
    try:
        flag_path = asr_cuda_disabled_flag_path()
        if not os.path.exists(flag_path):
            return ""
        with open(flag_path, "r", encoding="utf-8") as file:
            return " | ".join(line.strip() for line in file.readlines()[:3] if line.strip())
    except OSError:
        return ""


def asr_cuda_disabled_by_marker() -> bool:
    """判断是否因近期 CUDA 原生崩溃而临时禁用本地识别 CUDA"""
    if _asr_force_cuda_enabled():
        return False
    try:
        flag_path = asr_cuda_disabled_flag_path()
        if not os.path.exists(flag_path):
            return False
        ttl_seconds = _asr_cuda_marker_ttl_seconds()
        if ttl_seconds > 0:
            age_seconds = time.time() - os.path.getmtime(flag_path)
            if age_seconds > ttl_seconds:
                _remove_asr_cuda_disabled_marker(flag_path)
                return False
        return True
    except OSError:
        return False


def _asr_cuda_process_cooldown_seconds() -> float:
    """读取当前进程内 CUDA 失败后的冷却秒数，避免一次失败后永久走 CPU"""
    try:
        value = float(os.environ.get("YTV_ASR_CUDA_COOLDOWN_SECONDS", "60"))
    except (TypeError, ValueError):
        value = 180.0
    return max(0.0, value)


def _clear_asr_cuda_process_cooldown() -> None:
    """清理当前进程内 CUDA 冷却状态"""
    global _CUDA_ASR_DISABLED, _CUDA_ASR_DISABLED_UNTIL, _CUDA_ASR_DISABLED_REASON
    _CUDA_ASR_DISABLED = False
    _CUDA_ASR_DISABLED_UNTIL = 0.0
    _CUDA_ASR_DISABLED_REASON = ""


def _asr_cuda_process_cooldown_remaining() -> float:
    """返回当前进程 CUDA 冷却剩余秒数，过期后自动恢复 GPU 尝试"""
    global _CUDA_ASR_DISABLED
    if _asr_force_cuda_enabled() or not _CUDA_ASR_DISABLED:
        return 0.0
    remaining = _CUDA_ASR_DISABLED_UNTIL - time.time()
    if remaining <= 0:
        _clear_asr_cuda_process_cooldown()
        logger.info("本地识别 CUDA 当前进程冷却已结束，后续自动重新尝试 GPU")
        return 0.0
    return remaining


def mark_asr_cuda_disabled(reason: str) -> None:
    """写入 CUDA ASR 熔断标记，避免下次启动后继续触发同一类原生崩溃"""
    try:
        flag_path = asr_cuda_disabled_flag_path()
        os.makedirs(os.path.dirname(flag_path), exist_ok=True)
        with open(flag_path, "w", encoding="utf-8") as file:
            file.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\n{reason}\n")
    except OSError as exc:
        logger.warning(f"写入 CUDA 熔断标记失败: {exc}")


def _query_nvidia_smi_mib(field: str) -> int:
    """查询第一块 CUDA 显卡的显存数值（MiB），失败时返回 0"""
    try:
        result = subprocess.run(
            ["nvidia-smi", f"--query-gpu={field}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except Exception:
        return 0
    if result.returncode != 0:
        return 0
    first_line = (result.stdout or "").splitlines()[0:1]
    if not first_line:
        return 0
    try:
        return int(float(first_line[0].strip().split()[0]))
    except (ValueError, IndexError):
        return 0


@lru_cache(maxsize=1)
def cuda_memory_mib() -> int:
    """读取第一块 CUDA 显卡总显存，用于自动选择本地识别模型大小"""
    return _query_nvidia_smi_mib("memory.total")


@lru_cache(maxsize=1)
def cuda_free_memory_mib() -> int:
    """读取第一块 CUDA 显卡空闲显存；壁纸、浏览器等会常驻占用显存，必须按空闲量选模型"""
    return _query_nvidia_smi_mib("memory.free")


def default_gpu_asr_model_name() -> str:
    """优先按空闲显存选择模型，显存被其他程序占用时自动降级，避免推理中途显存不足崩溃"""
    free_mib = cuda_free_memory_mib()
    # large-v3-turbo 约需 4GB 显存，准确率接近 large-v3 但速度快 3-4 倍
    if free_mib >= 4500:
        return "large-v3-turbo"
    if free_mib >= 3500:
        return "medium"
    if free_mib >= 2000:
        return "small"
    if free_mib > 0:
        return "base"
    # 空闲显存查询失败时退回按总显存估算
    memory_mib = cuda_memory_mib()
    if memory_mib >= 7000:
        return "large-v3-turbo"
    if memory_mib >= 3500:
        return "medium"
    return "base"


@lru_cache(maxsize=1)
def cuda_device_count() -> int:
    """返回 CTranslate2 可用的 CUDA 设备数量"""
    try:
        configure_cuda_dll_search_paths()
        import ctranslate2
        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        return 0


def default_asr_device() -> str:
    """默认优先 GPU，没有 CUDA 时自动回退 CPU"""
    if asr_cuda_disabled_by_marker():
        logger.warning(f"本地识别 CUDA 已被熔断标记禁用: flag={asr_cuda_disabled_flag_path()}, reason={read_asr_cuda_disabled_reason() or '未记录'}")
        return "cpu"
    requested = (os.environ.get("YTV_ASR_DEVICE") or "auto").strip().lower()
    if requested and requested != "auto":
        return requested
    remaining = _asr_cuda_process_cooldown_remaining()
    if remaining > 0:
        logger.warning(f"本地识别 CUDA 正在当前进程冷却，剩余约 {remaining:.0f} 秒，自动改用 CPU: {_CUDA_ASR_DISABLED_REASON or '未记录'}")
        return "cpu"
    count = cuda_device_count()
    if count > 0:
        return "cuda"
    logger.info("本地识别未检测到可用 CUDA 设备，自动使用 CPU")
    return "cpu"


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
        beam_size: Optional[int] = None,
        allow_cpu_fallback: Optional[bool] = None,
        gap_rescue: Optional[bool] = None,
    ):
        """初始化本地识别参数，不在构造阶段加载模型"""
        # 补漏识别开关覆盖：模式3 时间轴识别不需要 CPU 逐个漏区重跑（内容由 Gemini 补），传 False 关闭可大幅提速
        self._gap_rescue_override = gap_rescue
        self.model_dir = model_dir or default_asr_model_dir()
        requested_device = device or os.environ.get("YTV_ASR_DEVICE") or "auto"
        normalized_device = str(requested_device).strip().lower()
        self.device = default_asr_device() if normalized_device == "auto" else normalized_device
        self.allow_cpu_fallback = self._resolve_cpu_fallback(normalized_device, allow_cpu_fallback)
        configured_model = model_name or os.environ.get("YTV_ASR_MODEL")
        self.auto_model_name = not configured_model
        # 有 GPU 时按显存提升模型准确率，CPU 仍用 base，兼顾准确率和中低配可用性。
        self.model_name = self._normalize_model_name(configured_model or (default_gpu_asr_model_name() if self.device == "cuda" else default_asr_model_name()))
        self.compute_type = compute_type or default_asr_compute_type(self.device)
        self.cpu_threads = cpu_threads or min(4, max(1, os.cpu_count() or 1))
        self.beam_size = beam_size
        self._log_device_profile(requested_device, normalized_device)

    def _normalize_model_name(self, model_name: str) -> str:
        """规范本地识别模型名，兼容前端展示名和环境变量写法"""
        normalized = str(model_name or "").strip()
        lowered = normalized.lower()
        aliases = {
            "sensevoice-small": SENSEVOICE_MODEL_NAME,
            "sensevoicesmall": SENSEVOICE_MODEL_NAME,
            "sensevoice": SENSEVOICE_MODEL_NAME,
            "qwen3_asr": QWEN3_ASR_MODEL_NAME,
            "qwen3asr": QWEN3_ASR_MODEL_NAME,
            "qwen3-asr": QWEN3_ASR_MODEL_NAME,
            "qwen3-asr-0.6b": QWEN3_ASR_MODEL_NAME,
            "qwen/qwen3-asr-0.6b": QWEN3_ASR_MODEL_NAME,
        }
        return aliases.get(lowered, lowered or default_asr_model_name())

    def _model_backend(self) -> str:
        """返回当前模型所属运行时，非 Whisper 模型需要走专用推理接口"""
        if self.model_name == SENSEVOICE_MODEL_NAME:
            return "sensevoice"
        if self.model_name == QWEN3_ASR_MODEL_NAME:
            return "qwen3-asr"
        return "whisper"

    def _log_device_profile(self, requested_device: str, normalized_device: str) -> None:
        """记录本地识别最终使用的模型和设备，方便排查 CPU/GPU 选择"""
        marker_reason = read_asr_cuda_disabled_reason()
        logger.info(
            "本地识别配置: "
            f"requested={requested_device}, normalized={normalized_device}, "
            f"resolved_device={self.device}, model={self.model_name}, compute={self.compute_type}, "
            f"cpu_threads={self.cpu_threads}, cuda_devices={cuda_device_count()}, "
            f"free_vram_mib={cuda_free_memory_mib()}, total_vram_mib={cuda_memory_mib()}, "
            f"cuda_marker={asr_cuda_disabled_flag_path() if marker_reason else ''}, "
            f"marker_reason={marker_reason or ''}, force_cuda={_asr_force_cuda_enabled()}"
        )

    def _validate_input_media_path(self, video_path: str) -> str:
        """校验本地识别输入，避免把时间轴 JSON 或字幕缓存交给 ffmpeg"""
        normalized = os.path.abspath(os.path.expanduser(str(video_path or "").strip()))
        ext = os.path.splitext(normalized)[1].lower()
        logger.info(f"本地识别输入: path={normalized}, ext={ext or '无后缀'}, exists={os.path.isfile(normalized)}, device={self.device}, model={self.model_name}")
        if not normalized:
            raise ValueError("本地识别输入为空")
        if not os.path.isfile(normalized):
            raise FileNotFoundError(f"本地识别视频不存在: {normalized}")
        if ext not in ASR_INPUT_EXTENSIONS:
            raise ValueError(f"本地识别输入必须是音视频文件，当前是 {ext or '无后缀'}: {normalized}")
        return normalized

    def _resolve_cpu_fallback(self, normalized_device: str, configured: Optional[bool]) -> bool:
        """判断 CUDA 失败时是否允许回退 CPU，默认保护自动流程不中断"""
        if configured is not None:
            return bool(configured)
        fallback_setting = str(os.environ.get("YTV_ASR_ALLOW_CPU_FALLBACK") or "true").strip().lower()
        if fallback_setting in {"0", "false", "no", "off"}:
            return False
        return normalized_device in {"auto", "cuda"}

    def transcribe_video(
        self,
        video_path: str,
        language: Optional[str] = None,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> tuple[list[dict], str]:
        """识别视频音频并返回字幕条目和检测到的语言"""
        video_path = self._validate_input_media_path(video_path)

        if self._should_use_cuda_worker():
            worker_input_path, worker_temp_audio_path = self._prepare_audio(video_path)
            try:
                return self._transcribe_video_with_cuda_worker_retries(
                    worker_input_path,
                    language,
                    progress_callback,
                    preprocessed_input=bool(worker_temp_audio_path),
                )
            except Exception as exc:
                if not self._should_fallback_to_cpu():
                    raise
                import traceback
                summary = f"GPU 本地识别子进程失败，自动回退 CPU: {type(exc).__name__}: {exc}"
                logger.warning(
                    f"{summary}\n{traceback.format_exc()}",
                    extra={"activity_message": summary},
                )
                self._switch_to_cpu_after_cuda_failure(exc)
            finally:
                if worker_temp_audio_path:
                    try:
                        os.remove(worker_temp_audio_path)
                    except OSError:
                        pass

        # 预处理音频：响度归一化让低声说话更容易被识别，时间轴保持不变。
        audio_path, temp_audio_path = self._prepare_audio(video_path)
        try:
            try:
                entries, detected_language = self._collect_transcription_entries(audio_path, language, progress_callback)
            except Exception as exc:
                if not self._should_fallback_to_cpu():
                    raise
                logger.warning(f"GPU 本地识别失败，自动回退 CPU: {exc}")
                self._switch_to_cpu_after_cuda_failure(exc)
                entries, detected_language = self._collect_transcription_entries(audio_path, language, progress_callback)

            # 解码一次音频数组，供空洞补漏和 VAD 边界校准共用
            audio_array = self._decode_audio_array(audio_path)
            # 计算一次 VAD 语音区间，补漏和边界校准复用，避免重复跑 VAD
            vad_regions = self._compute_vad_regions(audio_array)
            # 二次补漏：凡是 VAD 检测到有语音但字幕没覆盖的地方都重识别，找回被主识别漏掉的短语音
            rescued = self._rescue_gap_entries(audio_array, entries, vad_regions, detected_language)
            if rescued:
                entries = self._merge_rescued_entries(entries, rescued)
            # 用 VAD 语音边界校准字幕起止，纠正单音节语气词字幕比声音慢的问题
            self._calibrate_entries_with_vad(entries, vad_regions)
        finally:
            # 识别结束后清理预处理生成的临时音频
            if temp_audio_path:
                try:
                    os.remove(temp_audio_path)
                except OSError:
                    pass

        if progress_callback:
            progress_callback(100)
        if not entries:
            raise RuntimeError("本地语音识别没有识别到字幕内容")
        entries = self._merge_short_entries(entries)
        for index, entry in enumerate(entries, 1):
            entry["index"] = index
        logger.info(f"本地识别完成: {len(entries)} 条字幕, language={detected_language}")
        return entries, detected_language

    def _collect_transcription_entries(
        self,
        audio_path: str,
        language: Optional[str],
        progress_callback: Optional[Callable[[float], None]],
    ) -> tuple[list[dict], str]:
        """执行识别并消费字幕段，确保推理迭代阶段出错也能被外层捕获回退"""
        emit_progress = self._monotonic_progress_callback(progress_callback)
        heartbeat_stop = self._start_transcribe_heartbeat(emit_progress)
        try:
            segments, info = self._transcribe_with_model(audio_path, language, emit_progress)
            detected_language = str(getattr(info, "language", "") or language or "auto")
            duration = float(getattr(info, "duration", 0) or 0)
            entries: list[dict] = []
            for segment in segments:
                segment_entries = self._segment_to_entries(segment)
                for entry in segment_entries:
                    if not str(entry.get("text") or "").strip():
                        continue
                    entry["index"] = len(entries) + 1
                    entries.append(entry)
                if progress_callback and duration > 0:
                    end = max(
                        (self._srt_time_to_seconds(str(entry.get("end", "00:00:00,000"))) for entry in segment_entries),
                        default=float(getattr(segment, "end", 0) or 0),
                    )
                    emit_progress(min(95.0, 5.0 + end / duration * 90.0))
            return entries, detected_language
        finally:
            heartbeat_stop()

    def _should_use_cuda_worker(self) -> bool:
        """CUDA 识别默认放到子进程里跑，避免原生崩溃拖垮后端主进程"""
        configured = str(os.environ.get("YTV_ASR_CUDA_WORKER") or "true").strip().lower()
        if configured in {"0", "false", "no", "off"}:
            return False
        if str(os.environ.get("YTV_ASR_CHILD_WORKER") or "").strip().lower() in {"1", "true", "yes", "on"}:
            return False
        return self.device == "cuda"

    def _transcribe_video_with_cuda_worker_retries(
        self,
        audio_path: str,
        language: Optional[str],
        progress_callback: Optional[Callable[[float], None]],
        preprocessed_input: bool,
    ) -> tuple[list[dict], str]:
        """CUDA 子进程失败时先尝试更稳的 GPU 档位，全部失败后才交给 CPU 回退"""
        original_model = self.model_name
        original_compute = self.compute_type
        original_beam = self.beam_size
        errors: list[str] = []
        emit_progress = self._monotonic_progress_callback(progress_callback)
        profiles = self._cuda_worker_retry_profiles()

        for index, profile in enumerate(profiles, 1):
            self.model_name = str(profile["model_name"])
            self.compute_type = str(profile["compute_type"])
            self.beam_size = int(profile["beam_size"])
            logger.info(
                "本地识别 CUDA 尝试: "
                f"{index}/{len(profiles)}, "
                f"model={self.model_name}, compute={self.compute_type}, beam={self.beam_size}, "
                f"reason={profile['reason']}"
            )
            try:
                return self._transcribe_video_in_worker(
                    audio_path,
                    language,
                    emit_progress,
                    preprocessed_input=preprocessed_input,
                    mark_native_failure=False,
                )
            except Exception as exc:
                errors.append(f"{self.model_name}/{self.compute_type}/beam{self.beam_size}: {exc}")
                logger.warning(f"本地识别 CUDA 档位失败，继续尝试下一个 GPU 档位: {errors[-1]}")

        self.model_name = original_model
        self.compute_type = original_compute
        self.beam_size = original_beam
        reason = "；".join(errors[-4:]) or "未知错误"
        mark_asr_cuda_disabled(f"本地识别 CUDA 子进程所有 GPU 档位均失败，{reason}")
        raise RuntimeError(f"本地识别 CUDA 子进程所有 GPU 档位均失败: {reason}")

    def _cuda_worker_retry_profiles(self) -> list[dict[str, Any]]:
        """生成 CUDA 子进程重试档位：先保准确率，再用 int8_float16 和降模型降低显存压力"""
        original_model = self.model_name
        original_compute = self.compute_type
        original_beam = self._beam_size()
        profiles: list[dict[str, Any]] = []
        seen: set[tuple[str, str, int]] = set()

        def add(model_name: str, compute_type: str, beam_size: int, reason: str) -> None:
            """加入一个未重复的 CUDA 尝试档位"""
            normalized = (model_name, compute_type, max(1, min(8, int(beam_size))))
            if normalized in seen:
                return
            seen.add(normalized)
            profiles.append({
                "model_name": normalized[0],
                "compute_type": normalized[1],
                "beam_size": normalized[2],
                "reason": reason,
            })

        add(original_model, original_compute, original_beam, "原始配置")
        if original_compute != "int8_float16":
            add(original_model, "int8_float16", min(original_beam, 3), "省显存精度")
        if original_beam > 1:
            add(original_model, original_compute, 1, "降低 beam 减少瞬时显存")
        lower_model = self._next_smaller_cuda_model(original_model)
        if lower_model:
            add(lower_model, original_compute, min(original_beam, 3), "降低模型保留 GPU")
            if original_compute != "int8_float16":
                add(lower_model, "int8_float16", min(original_beam, 3), "降低模型并省显存")
        return profiles

    def _next_smaller_cuda_model(self, model_name: str) -> str:
        """根据当前模型选择下一个更稳的 GPU 模型，避免直接退到 CPU"""
        order = ["large-v3", "large-v3-turbo", "medium", "small", "base", "tiny"]
        normalized = str(model_name or "").strip().lower()
        if normalized not in order:
            return ""
        index = order.index(normalized)
        if index >= len(order) - 1:
            return ""
        return order[index + 1]

    def _transcribe_video_in_worker(
        self,
        video_path: str,
        language: Optional[str],
        progress_callback: Optional[Callable[[float], None]],
        preprocessed_input: bool = False,
        mark_native_failure: bool = True,
    ) -> tuple[list[dict], str]:
        """在独立 Python 子进程中执行 CUDA 识别，子进程崩溃时主后端仍可回退 CPU"""
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        worker_path = os.path.join(project_root, "backend", "core", "local_asr_worker.py")
        env = os.environ.copy()
        env["YTV_ASR_CHILD_WORKER"] = "1"
        # CUDA worker 只负责尝试 GPU 档位，失败必须返回父进程统一调度；
        # 不能在子进程里偷偷回退 CPU，否则父进程只能等 stdout，卡死时任务会一直 running。
        if self.device == "cuda":
            env["YTV_ASR_ALLOW_CPU_FALLBACK"] = "0"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONPATH"] = os.pathsep.join([project_root, env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
        if preprocessed_input:
            # 父进程已经生成 16k WAV，子进程只跑 CUDA 推理，减少崩溃面和重复 ffmpeg 开销。
            env["YTV_ASR_PREPROCESS"] = "0"
        cuda_dirs = configure_cuda_dll_search_paths(env)
        command = [
            sys.executable,
            worker_path,
            "--video-path", video_path,
            "--model-name", self.model_name,
            "--model-dir", self.model_dir,
            "--device", self.device,
            "--compute-type", self.compute_type,
            "--cpu-threads", str(self.cpu_threads),
        ]
        if self.beam_size is not None:
            command.extend(["--beam-size", str(self.beam_size)])
        if language:
            command.extend(["--language", language])
        # 补漏开关被显式覆盖时透传给子进程（模式3 时间轴识别关补漏提速），否则子进程读环境变量
        if self._gap_rescue_override is not None:
            command.extend(["--gap-rescue", "1" if self._gap_rescue_override else "0"])

        logger.info(
            "本地识别 CUDA 子进程启动: "
            f"model={self.model_name}, compute={self.compute_type}, beam={self._beam_size()}, "
            f"dll_dirs={list(cuda_dirs)}"
        )
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        process = subprocess.Popen(
            command,
            cwd=project_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )

        result_payload: Optional[dict[str, Any]] = None
        error_message = ""
        assert process.stdout is not None
        line_queue: queue.Queue[Optional[str]] = queue.Queue()
        silence_timeout = self._worker_silence_timeout_seconds()
        last_output_at = time.monotonic()

        def read_stdout() -> None:
            """后台读取 worker stdout，让父线程能检测无输出卡死"""
            try:
                for raw in process.stdout:
                    line_queue.put(raw)
            finally:
                line_queue.put(None)

        reader = threading.Thread(target=read_stdout, name="ytv-asr-worker-stdout", daemon=True)
        reader.start()

        while True:
            try:
                raw_line = line_queue.get(timeout=1.0)
            except queue.Empty:
                if time.monotonic() - last_output_at > silence_timeout:
                    self._terminate_worker_process(process)
                    raise RuntimeError(f"本地识别 CUDA 子进程超过 {silence_timeout:.0f} 秒没有输出，已终止避免任务卡死")
                continue
            if raw_line is None:
                break
            last_output_at = time.monotonic()
            line = raw_line.rstrip()
            if not line:
                continue
            if line.startswith(ASR_WORKER_EVENT_PREFIX):
                try:
                    event = json.loads(line[len(ASR_WORKER_EVENT_PREFIX):])
                except json.JSONDecodeError:
                    logger.warning(f"本地识别子进程事件解析失败: {line[:200]}")
                    continue
                event_type = str(event.get("type") or "")
                if event_type == "progress" and progress_callback:
                    progress_callback(float(event.get("value") or 0))
                elif event_type == "result":
                    result_payload = event
                elif event_type == "error":
                    error_message = str(event.get("message") or event.get("error_type") or "")
                elif event_type == "diagnostic":
                    logger.info(f"本地识别子进程诊断: {event}")
                continue
            logger.info(f"本地识别子进程: {line}")

        return_code = process.wait()
        if return_code != 0:
            reason = error_message or f"退出码 {return_code}"
            if mark_native_failure and return_code != 1:
                mark_asr_cuda_disabled(f"本地识别 CUDA 子进程异常退出，{reason}")
            raise RuntimeError(f"本地识别 CUDA 子进程失败: {reason}")
        if not result_payload:
            raise RuntimeError("本地识别 CUDA 子进程没有返回结果")
        entries = result_payload.get("entries")
        if not isinstance(entries, list):
            raise RuntimeError("本地识别 CUDA 子进程返回结果格式错误")
        language_value = str(result_payload.get("language") or language or "auto")
        return entries, language_value

    def _worker_silence_timeout_seconds(self) -> float:
        """读取 CUDA worker 无输出超时，避免 native 推理卡死后任务永久停在字幕准备"""
        return self._env_float("YTV_ASR_WORKER_SILENCE_TIMEOUT_S", 300.0, 30.0, 3600.0)

    def _terminate_worker_process(self, process: Any) -> None:
        """终止卡死的本地识别 worker，失败只记录日志，后续由父流程回退处理"""
        try:
            process.terminate()
        except Exception:
            pass
        try:
            process.wait(timeout=5)
            return
        except Exception:
            pass
        try:
            process.kill()
        except Exception as exc:
            logger.warning(f"终止卡死的本地识别子进程失败: {exc}")

    def _switch_to_cpu_after_cuda_failure(self, reason: Exception) -> None:
        """切换到 CPU 识别参数，供 CUDA 初始化、推理和子进程失败共用"""
        self._disable_cuda_asr_for_process(reason)
        self.device = "cpu"
        if self.auto_model_name:
            self.model_name = default_asr_model_name()
        self.compute_type = os.environ.get("YTV_ASR_CPU_COMPUTE_TYPE") or "int8"

    def _monotonic_progress_callback(self, progress_callback: Optional[Callable[[float], None]]) -> Optional[Callable[[float], None]]:
        """包装进度回调，保证心跳进度不会覆盖真实识别进度造成倒退"""
        if not progress_callback:
            return None
        lock = threading.Lock()
        last_progress = 0.0

        def emit(progress: float) -> None:
            """只向外发送递增的进度值"""
            nonlocal last_progress
            normalized = max(0.0, min(100.0, float(progress)))
            with lock:
                if normalized < last_progress:
                    return
                last_progress = normalized
            progress_callback(normalized)

        return emit

    def _transcribe_with_model(
        self,
        video_path: str,
        language: Optional[str],
        progress_callback: Optional[Callable[[float], None]],
    ):
        """加载模型并执行一次识别"""
        backend = self._model_backend()
        if backend == "sensevoice":
            return self._transcribe_with_sensevoice(video_path, language, progress_callback)
        if backend == "qwen3-asr":
            return self._transcribe_with_qwen3_asr(video_path, language, progress_callback)

        model = self._load_model()
        logger.info(f"本地识别字幕: model={self.model_name}, device={self.device}, compute={self.compute_type}")
        if progress_callback:
            progress_callback(5)

        return model.transcribe(
            video_path,
            language=language or None,
            vad_filter=self._vad_filter_enabled(),
            vad_parameters=self._vad_parameters(),
            word_timestamps=True,
            beam_size=self._beam_size(),
            no_speech_threshold=self._no_speech_threshold(),
            hallucination_silence_threshold=self._hallucination_silence_threshold(),
            condition_on_previous_text=False,
        )

    def _transcribe_with_sensevoice(
        self,
        audio_path: str,
        language: Optional[str],
        progress_callback: Optional[Callable[[float], None]],
    ):
        """使用 FunASR SenseVoice 本地识别，并转换成 faster-whisper 兼容片段"""
        model = self._load_sensevoice_model()
        logger.info(f"本地识别字幕: model={self.model_name}, runtime=funasr, device={self.device}")
        if progress_callback:
            progress_callback(5)
        result = self._call_sensevoice_generate(model, audio_path, language)
        segments, detected_language = self._segments_from_sensevoice_result(result, audio_path, language)
        if progress_callback:
            progress_callback(95)
        return segments, SimpleNamespace(language=detected_language, duration=self._audio_duration_seconds(audio_path) or 0.0)

    def _transcribe_with_qwen3_asr(
        self,
        audio_path: str,
        language: Optional[str],
        progress_callback: Optional[Callable[[float], None]],
    ):
        """使用 Qwen3-ASR 本地识别，并尽量读取 forced aligner 产出的时间戳"""
        model = self._load_qwen3_asr_model()
        logger.info(f"本地识别字幕: model={self.model_name}, runtime=qwen-asr, device={self.device}")
        if progress_callback:
            progress_callback(5)
        result = self._call_qwen3_asr_transcribe(model, audio_path, language)
        segments, detected_language = self._segments_from_qwen3_result(result, audio_path, language)
        if progress_callback:
            progress_callback(95)
        return segments, SimpleNamespace(language=detected_language, duration=self._audio_duration_seconds(audio_path) or 0.0)

    def _call_sensevoice_generate(self, model: Any, audio_path: str, language: Optional[str]) -> Any:
        """调用 SenseVoice generate；不同 funasr 版本参数有差异，按能力逐级降级"""
        base_kwargs = {
            "input": audio_path,
            "cache": {},
            "language": language or "auto",
            "use_itn": True,
            "batch_size_s": self._env_int("YTV_SENSEVOICE_BATCH_SIZE_S", 60, 5, 300),
            "merge_vad": True,
            "merge_length_s": self._env_int("YTV_SENSEVOICE_MERGE_LENGTH_S", 15, 1, 120),
        }
        attempts = [
            base_kwargs,
            {key: value for key, value in base_kwargs.items() if key not in {"merge_vad", "merge_length_s"}},
            {"input": audio_path, "language": language or "auto", "use_itn": True},
            {"input": audio_path},
        ]
        last_error: Optional[Exception] = None
        for kwargs in attempts:
            try:
                return model.generate(**kwargs)
            except TypeError as exc:
                last_error = exc
                continue
        raise RuntimeError(f"SenseVoice 调用失败，当前 funasr 版本不兼容: {last_error}")

    def _call_qwen3_asr_transcribe(self, model: Any, audio_path: str, language: Optional[str]) -> Any:
        """调用 Qwen3-ASR transcribe，兼容不同版本的时间戳参数命名"""
        language_arg = language or "auto"
        attempts = [
            ([audio_path], {"language": language_arg, "return_timestamps": True}),
            ([audio_path], {"language": language_arg, "enable_timestamp": True}),
            ([audio_path], {"language": language_arg, "timestamps": True}),
            ([audio_path], {"language": language_arg}),
            (audio_path, {"language": language_arg, "return_timestamps": True}),
            (audio_path, {"language": language_arg}),
        ]
        last_error: Optional[Exception] = None
        for first_arg, kwargs in attempts:
            try:
                return model.transcribe(first_arg, **kwargs)
            except TypeError as exc:
                last_error = exc
                continue
        raise RuntimeError(f"Qwen3-ASR 调用失败，当前 qwen-asr 版本不兼容: {last_error}")

    def _start_transcribe_heartbeat(self, progress_callback: Optional[Callable[[float], None]]) -> Callable[[], None]:
        """识别过程中发送心跳进度，避免长视频 CPU/GPU 推理看起来卡死"""
        if not progress_callback:
            return lambda: None

        interval = self._env_float("YTV_ASR_HEARTBEAT_INTERVAL_S", 15.0, 1.0, 120.0)
        max_progress = self._env_float("YTV_ASR_HEARTBEAT_MAX_PROGRESS", 90.0, 6.0, 95.0)
        stop_event = threading.Event()

        def run() -> None:
            """按时间缓慢推进到上限，真实字幕段进度返回后会继续覆盖它"""
            started_at = time.monotonic()
            while not stop_event.wait(interval):
                elapsed = max(0.0, time.monotonic() - started_at)
                progress = min(max_progress, 5.0 + elapsed / max(interval, 1.0) * 1.5)
                try:
                    progress_callback(progress)
                except Exception as exc:
                    logger.warning(f"本地识别心跳进度回调失败: {exc}")

        thread = threading.Thread(target=run, name="ytv-asr-progress-heartbeat", daemon=True)
        thread.start()

        def stop() -> None:
            """停止心跳线程，避免识别结束后继续更新旧任务进度"""
            stop_event.set()
            thread.join(timeout=0.2)

        return stop

    def _prepare_audio(self, video_path: str) -> tuple[str, Optional[str]]:
        """提取 16k 单声道音频并做响度归一化，返回(识别用路径, 待清理临时文件)"""
        if not self._preprocess_enabled():
            return video_path, None

        temp_path = ""
        try:
            fd, temp_path = tempfile.mkstemp(prefix="ytv_asr_", suffix=".wav")
            os.close(fd)
            result = subprocess.run(
                [
                    get_ffmpeg_command(),
                    "-y",
                    "-i", video_path,
                    "-vn",
                    "-ac", "1",
                    "-ar", "16000",
                    "-af", self._audio_filter(),
                    "-c:a", "pcm_s16le",
                    temp_path,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=900,
                check=False,
            )
            if result.returncode == 0 and os.path.getsize(temp_path) > 44:
                logger.info("本地识别音频预处理完成，已做人声响度归一化")
                return temp_path, temp_path
            logger.warning(f"音频预处理失败(code={result.returncode})，使用原始视频识别")
        except Exception as exc:
            logger.warning(f"音频预处理异常，使用原始视频识别: {exc}")
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass
        return video_path, None

    def _preprocess_enabled(self) -> bool:
        """读取音频预处理开关，默认开启以提升低声说话的识别率"""
        configured = str(os.environ.get("YTV_ASR_PREPROCESS") or "true").strip().lower()
        return configured not in {"0", "false", "no", "off"}

    def _audio_filter(self) -> str:
        """识别前的音频滤镜：滤掉低频隆隆声并做响度归一化，把低声说话抬到可识别响度"""
        return os.environ.get("YTV_ASR_AUDIO_FILTER") or "highpass=f=70,loudnorm=I=-16:TP=-1.5:LRA=11"

    def _hallucination_silence_threshold(self) -> float:
        """静音超过该秒数时跳过其中的幻觉词，避免字幕挂在没有人声的位置"""
        return self._env_float("YTV_ASR_HALLUCINATION_SILENCE", 2.0, 0.5, 10.0)

    def _decode_audio_array(self, audio_path: str) -> Optional[Any]:
        """把音频解码成 16k 采样数组，供补漏识别和 VAD 校准共用"""
        try:
            from faster_whisper.audio import decode_audio
            return decode_audio(audio_path, sampling_rate=16000)
        except Exception as exc:
            logger.warning(f"音频解码失败，跳过空洞补漏和 VAD 校准: {exc}")
            return None

    def _compute_vad_regions(self, audio: Optional[Any]) -> list[tuple[float, float]]:
        """跑一次 VAD 得到语音区间（秒），补漏找漏区和边界校准都基于它，避免重复计算"""
        if audio is None:
            return []
        try:
            from faster_whisper.vad import get_speech_timestamps, VadOptions
            options = VadOptions(
                threshold=self._env_float("YTV_ASR_VAD_THRESHOLD", 0.2, 0.05, 0.9),
                min_speech_duration_ms=self._env_int("YTV_ASR_VAD_MIN_SPEECH_MS", 80, 20, 1000),
                min_silence_duration_ms=self._env_int("YTV_ASR_VAD_MIN_SILENCE_MS", 250, 80, 2000),
                speech_pad_ms=0,
            )
            return [(chunk["start"] / 16000.0, chunk["end"] / 16000.0) for chunk in get_speech_timestamps(audio, options)]
        except Exception as exc:
            logger.warning(f"VAD 语音区间计算失败，跳过补漏和边界校准: {exc}")
            return []

    def _rescue_gap_entries(self, audio: Optional[Any], entries: list[dict], vad_regions: list[tuple[float, float]], language: str) -> list[dict]:
        """对 VAD 检测到有语音但字幕没覆盖的区段关闭 VAD 重新识别，找回被主识别漏掉的短语音"""
        if audio is None or not vad_regions or not self._gap_rescue_enabled():
            return []
        gaps = self._find_uncovered_speech_gaps(entries, vad_regions)
        if not gaps:
            return []

        model = self._load_model()
        clip_language = None if not language or language == "auto" else language
        total_samples = len(audio)
        # 短漏区前后各留一点上下文，识别更稳；偏移时再扣回来
        pad = self._env_float("YTV_ASR_RESCUE_PAD_S", 0.3, 0.0, 2.0)
        rescued: list[dict] = []
        for gap_start, gap_end in gaps:
            clip_start = max(0.0, gap_start - pad)
            clip_end = min(total_samples / 16000.0, gap_end + pad)
            clip = audio[int(clip_start * 16000):int(clip_end * 16000)]
            # 不足 0.3 秒的片段没有补漏价值
            if len(clip) < int(16000 * 0.3):
                continue
            try:
                segments, _ = model.transcribe(
                    clip,
                    language=clip_language,
                    vad_filter=False,
                    word_timestamps=True,
                    beam_size=self._beam_size(),
                    no_speech_threshold=self._no_speech_threshold(),
                    condition_on_previous_text=False,
                )
                for segment in segments:
                    if not self._rescue_segment_acceptable(segment):
                        continue
                    shifted = self._shift_segment(segment, clip_start, clip_end)
                    for entry in self._segment_to_entries(shifted):
                        if not str(entry.get("text") or "").strip():
                            continue
                        # 只保留和漏区真正重叠的字幕，padding 区域里属于相邻句子的内容丢弃
                        entry_start = self._srt_time_to_seconds(str(entry.get("start") or "00:00:00,000"))
                        entry_end = self._srt_time_to_seconds(str(entry.get("end") or "00:00:00,000"))
                        if entry_end > gap_start and entry_start < gap_end:
                            rescued.append(entry)
            except Exception as exc:
                logger.warning(f"补漏识别区段 {gap_start:.1f}-{gap_end:.1f}s 失败: {exc}")
                if self._should_fallback_to_cpu():
                    self._disable_cuda_asr_for_process(exc)
        if rescued:
            logger.info(f"补漏识别从 {len(gaps)} 个漏区中找回 {len(rescued)} 条字幕")
        return rescued

    def _gap_rescue_enabled(self) -> bool:
        """读取空洞补漏开关，默认开启保证轻声细语也能出字幕"""
        # 构造时显式指定的开关优先级最高（模式3 时间轴识别用它关闭补漏提速），否则读环境变量
        if self._gap_rescue_override is not None:
            return bool(self._gap_rescue_override)
        configured = str(os.environ.get("YTV_ASR_GAP_RESCUE") or "true").strip().lower()
        return configured not in {"0", "false", "no", "off"}

    def _find_uncovered_speech_gaps(self, entries: list[dict], vad_regions: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """在每个 VAD 语音区间内减去已有字幕覆盖，得到真正没字幕的漏区（含极短空洞）"""
        min_dur = self._env_float("YTV_ASR_GAP_RESCUE_MIN_S", 0.4, 0.2, 30.0)
        # 字幕覆盖区间留出边距，避免边界抖动把一句话切出碎片漏区
        margin = 0.15
        covered = sorted(
            (
                self._srt_time_to_seconds(str(entry.get("start") or "00:00:00,000")) - margin,
                self._srt_time_to_seconds(str(entry.get("end") or "00:00:00,000")) + margin,
            )
            for entry in entries
        )
        gaps: list[tuple[float, float]] = []
        for region_start, region_end in vad_regions:
            cursor = region_start
            for cover_start, cover_end in covered:
                if cover_end <= cursor or cover_start >= region_end:
                    continue
                if cover_start - cursor >= min_dur:
                    gaps.append((cursor, cover_start))
                cursor = max(cursor, cover_end)
                if cursor >= region_end:
                    break
            if region_end - cursor >= min_dur:
                gaps.append((cursor, region_end))
        return gaps

    def _merge_rescued_entries(self, entries: list[dict], rescued: list[dict]) -> list[dict]:
        """把补漏字幕并入主字幕并按时间排序，丢弃与已有字幕高度重叠且文本重复的补漏项"""
        merged = list(entries)
        for candidate in rescued:
            cand_start = self._srt_time_to_seconds(str(candidate.get("start") or "00:00:00,000"))
            cand_end = self._srt_time_to_seconds(str(candidate.get("end") or "00:00:00,000"))
            cand_text = " ".join(str(candidate.get("text") or "").split())
            duplicate = False
            for existing in merged:
                exist_start = self._srt_time_to_seconds(str(existing.get("start") or "00:00:00,000"))
                exist_end = self._srt_time_to_seconds(str(existing.get("end") or "00:00:00,000"))
                overlap = min(cand_end, exist_end) - max(cand_start, exist_start)
                if overlap > 0.2 and cand_text == " ".join(str(existing.get("text") or "").split()):
                    duplicate = True
                    break
            if not duplicate:
                merged.append(candidate)
        merged.sort(key=lambda item: self._srt_time_to_seconds(str(item.get("start") or "00:00:00,000")))
        for index, entry in enumerate(merged, 1):
            entry["index"] = index
        return merged

    def _rescue_segment_acceptable(self, segment: Any) -> bool:
        """补漏识别用更严格的门槛过滤，避免背景音乐被识别成幻觉字幕"""
        text = " ".join(str(getattr(segment, "text", "") or "").split())
        if not text:
            return False
        # 命中常见幻觉句式直接丢弃
        for phrase in HALLUCINATION_PHRASES:
            if phrase.lower() in text.lower():
                return False
        no_speech_prob = float(getattr(segment, "no_speech_prob", 0) or 0)
        if no_speech_prob > self._env_float("YTV_ASR_RESCUE_NO_SPEECH", 0.5, 0.1, 1.0):
            return False
        avg_logprob = float(getattr(segment, "avg_logprob", 0) or 0)
        if avg_logprob < self._env_float("YTV_ASR_RESCUE_MIN_LOGPROB", -1.25, -3.0, 0.0):
            return False
        compression_ratio = float(getattr(segment, "compression_ratio", 1) or 1)
        if compression_ratio > self._env_float("YTV_ASR_RESCUE_MAX_COMPRESSION", 2.6, 1.0, 10.0):
            return False
        return True

    def _shift_segment(self, segment: Any, gap_start: float, gap_end: float) -> Any:
        """把空洞内识别的相对时间平移回完整视频时间轴，并限制在空洞范围内"""
        max_end = gap_end + 0.3
        start = min(max_end, gap_start + float(getattr(segment, "start", 0) or 0))
        end = min(max_end, gap_start + float(getattr(segment, "end", 0) or 0))
        words = []
        for word in (getattr(segment, "words", None) or []):
            word_start = min(max_end, gap_start + float(getattr(word, "start", 0) or 0))
            word_end = min(max_end, gap_start + float(getattr(word, "end", 0) or 0))
            words.append(SimpleNamespace(word=str(getattr(word, "word", "") or ""), start=word_start, end=word_end))
        return SimpleNamespace(start=start, end=max(start, end), text=str(getattr(segment, "text", "") or ""), words=words)

    def _calibrate_entries_with_vad(self, entries: list[dict], regions: list[tuple[float, float]]) -> None:
        """用 VAD 语音区间边界校准字幕起止：单音节语气词的词级对齐常偏慢，VAD 对声音起点更准"""
        if not entries or not regions or not self._vad_calibrate_enabled():
            return
        # 把字幕时间换算成秒，并记录每个语音区间的第一条/最后一条字幕
        timeline = [
            [self._srt_time_to_seconds(str(entry.get("start") or "00:00:00,000")),
             self._srt_time_to_seconds(str(entry.get("end") or "00:00:00,000")),
             entry]
            for entry in entries
        ]
        adjusted = 0
        for index, item in enumerate(timeline):
            start, end, entry = item
            overlapping = [region for region in regions if start < region[1] and end > region[0]]
            if not overlapping:
                continue
            first_region = overlapping[0]
            last_region = overlapping[-1]
            is_first_in_region = not any(
                other is not item and other[0] < first_region[1] and other[1] > first_region[0] and other[0] < start
                for other in timeline
            )
            is_last_in_region = not any(
                other is not item and other[0] < last_region[1] and other[1] > last_region[0] and other[1] > end
                for other in timeline
            )
            prev_end = timeline[index - 1][1] if index > 0 else 0.0
            if is_first_in_region:
                new_start = start
                if start - first_region[0] > 0.3:
                    # 字幕比语音起点慢时贴回语音边界，让声音一出字幕就出
                    new_start = first_region[0] + 0.05
                elif first_region[0] - start > 0.3:
                    # 字幕比语音起点早时往后收，避免声音没出字幕先出
                    new_start = first_region[0] - 0.1
                new_start = max(new_start, prev_end)
                if abs(new_start - start) > 0.01 and new_start < end:
                    item[0] = new_start
                    entry["start"] = self._seconds_to_srt_time(new_start)
                    adjusted += 1
            if is_last_in_region and end - last_region[1] > 0.5:
                # 字幕结尾拖到语音结束之后太久时收回，避免声音停了字幕还挂着
                new_end = last_region[1] + 0.25
                if new_end > item[0]:
                    item[1] = new_end
                    entry["end"] = self._seconds_to_srt_time(new_end)
                    adjusted += 1
        if adjusted:
            logger.info(f"VAD 边界校准调整了 {adjusted} 处字幕时间")

    def _vad_calibrate_enabled(self) -> bool:
        """读取 VAD 边界校准开关，默认开启提升音画同步"""
        configured = str(os.environ.get("YTV_ASR_VAD_CALIBRATE") or "true").strip().lower()
        return configured not in {"0", "false", "no", "off"}

    def _segment_to_entries(self, segment: Any) -> list[dict]:
        """优先用词级时间戳切分字幕，没有词时间戳时退回整段时间"""
        words = [word for word in (getattr(segment, "words", None) or []) if str(getattr(word, "word", "") or "").strip()]
        if not words:
            return [self._segment_fallback_entry(segment)]
        words = self._sanitize_words(words)

        entries: list[dict] = []
        current: list[Any] = []
        max_chars = 28
        hard_limit = 42
        min_punctuation_chars = 12
        max_duration = 6.5
        pause_seconds = 0.35
        break_chars = "，。、！？；,.!?;"
        for index, word in enumerate(words):
            current.append(word)
            text = self._words_text(current)
            if not text:
                current.clear()
                continue
            next_word = words[index + 1] if index + 1 < len(words) else None
            should_break = self._should_break_words(
                current,
                text,
                next_word,
                break_chars,
                max_chars,
                hard_limit,
                min_punctuation_chars,
                max_duration,
                pause_seconds,
            )
            if should_break:
                entries.append(self._words_to_entry(current))
                current = []

        if current:
            entries.append(self._words_to_entry(current))
        return entries or [self._segment_fallback_entry(segment)]

    def _sanitize_words(self, words: list[Any]) -> list[Any]:
        """钳制词级时间戳，纠正背景音乐导致的单词拉长和时间倒退"""
        sanitized: list[Any] = []
        prev_end: Optional[float] = None
        for word in words:
            text = str(getattr(word, "word", "") or "")
            start = float(getattr(word, "start", 0) or 0)
            end = float(getattr(word, "end", start) or start)
            # 词开始时间早于上一个词结尾时，贴回上一个词的结尾，避免字幕提前出现
            if prev_end is not None and start < prev_end:
                start = prev_end
            if end < start:
                end = start
            # 单词被对齐算法拉长时按字数封顶，避免字幕在声音结束后仍然滞留
            max_duration = self._max_word_duration(text)
            if end - start > max_duration:
                end = start + max_duration
            sanitized.append(SimpleNamespace(word=text, start=start, end=end))
            prev_end = end
        return sanitized

    def _max_word_duration(self, text: str) -> float:
        """按字符数估算单个词的最长合理发音时长"""
        chars = len(text.strip())
        return min(3.0, 0.6 + 0.15 * max(1, chars))

    def _segment_fallback_entry(self, segment: Any) -> dict:
        """把 Whisper 整段结果转换成字幕条目"""
        text = " ".join(str(getattr(segment, "text", "") or "").split())
        start = float(getattr(segment, "start", 0) or 0)
        end = float(getattr(segment, "end", start) or start)
        return {
            "index": 0,
            "start": self._seconds_to_srt_time(start),
            "end": self._seconds_to_srt_time(max(end, start + 0.2)),
            "text": text,
        }

    def _words_to_entry(self, words: list[Any]) -> dict:
        """把一组词转换成更贴近语音边界的字幕条目"""
        start = float(getattr(words[0], "start", 0) or 0)
        end = float(getattr(words[-1], "end", start) or start)
        return {
            "index": 0,
            "start": self._seconds_to_srt_time(start),
            "end": self._seconds_to_srt_time(max(end, start + 0.2)),
            "text": self._words_text(words),
        }

    def _should_break_words(
        self,
        words: list[Any],
        text: str,
        next_word: Any,
        break_chars: str,
        max_chars: int,
        hard_limit: int,
        min_punctuation_chars: int,
        max_duration: float,
        pause_seconds: float,
    ) -> bool:
        """判断词级字幕是否应该断句，避免把日语词尾单独切到下一条"""
        if not next_word:
            return False
        start = float(getattr(words[0], "start", 0) or 0)
        end = float(getattr(words[-1], "end", start) or start)
        next_start = float(getattr(next_word, "start", end) or end)
        next_text = str(getattr(next_word, "word", "") or "").strip()
        duration = max(0.0, end - start)
        pause = max(0.0, next_start - end)
        if text[-1] in break_chars and len(text) >= min_punctuation_chars:
            return True
        if pause >= pause_seconds and not self._is_short_japanese_tail(next_text):
            return True
        if len(text) < max_chars and duration < max_duration:
            return False
        if self._should_defer_break_for_next_content_word(text, next_text, hard_limit):
            return False
        if len(text) >= hard_limit:
            return not self._is_short_japanese_tail(next_text)
        return duration >= max_duration and not self._is_short_japanese_tail(next_text)

    def _should_defer_break_for_next_content_word(self, current_text: str, next_text: str, hard_limit: int) -> bool:
        """时长硬断前把紧邻内容词收进上一条，减少“烧制/圆石”这类割裂断句"""
        current = " ".join(str(current_text or "").split()).strip()
        core = self._boundary_word_core(next_text)
        if not current or not core:
            return False
        if current[-1] in TERMINAL_PUNCTUATION:
            return False
        lowered = core.lower()
        if lowered in LEADING_FRAGMENT_WORDS:
            return False
        combined_length = len(current) + 1 + len(core)
        if combined_length > hard_limit + 16:
            return False
        if re.fullmatch(r"[A-Za-z][A-Za-z'-]{1,17}", core):
            return True
        # 中日韩短内容词也允许收入上一条，避免短名词被甩到下一条开头。
        return bool(re.fullmatch(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]{1,4}", core))

    def _boundary_word_core(self, text: str) -> str:
        """取出用于判断断句边界的词芯，忽略 Whisper 常带的前导空格和外层标点"""
        return str(text or "").strip().strip(TERMINAL_PUNCTUATION + "\"'()[]{}“”‘’")

    def _is_short_japanese_tail(self, text: str) -> bool:
        """识别日语短词尾，硬断时尽量和前一句保持在同一条字幕里"""
        normalized = text.strip(" 　、。,.!?！？；;")
        return normalized in {
            "です",
            "ます",
            "した",
            "して",
            "ますね",
            "ですね",
            "なんですね",
            "ります",
            "りそう",
            "そうです",
            "ています",
            "ている",
            "けれども",
            "ですけれども",
        }

    def _words_text(self, words: list[Any]) -> str:
        """合并词级文本，兼容英文前导空格和中日韩无空格文本"""
        raw = "".join(str(getattr(word, "word", "") or "") for word in words)
        return " ".join(raw.split())

    def _merge_short_entries(self, entries: list[dict]) -> list[dict]:
        """合并极短字幕碎片，避免单词或词尾独立闪现导致时间轴观感发飘"""
        if len(entries) < 2:
            return entries

        merged = [dict(entry) for entry in entries]
        index = 0
        max_duration_ms = 350
        short_tail_duration_ms = 900
        max_gap_ms = 120
        while index < len(merged):
            current = merged[index]
            prev_entry = merged[index - 1] if index > 0 else None
            next_entry = merged[index + 1] if index + 1 < len(merged) else None
            duration_ms = self._entry_duration_ms(current)
            continuation_tail = self._is_short_continuation(prev_entry, current, duration_ms, short_tail_duration_ms)
            if (duration_ms > max_duration_ms and not continuation_tail) or not self._is_merge_candidate_text(str(current.get("text") or "")):
                index += 1
                continue

            prev_gap = self._entry_gap_ms(prev_entry, current)
            next_gap = self._entry_gap_ms(current, next_entry)
            merge_forward = next_entry is not None and next_gap <= max_gap_ms
            merge_backward = prev_entry is not None and prev_gap <= max_gap_ms
            if merge_forward and not continuation_tail and (self._prefer_merge_forward(str(current.get("text") or "")) or not merge_backward):
                next_entry["start"] = current["start"]
                next_entry["text"] = self._join_entry_text(str(current.get("text") or ""), str(next_entry.get("text") or ""))
                merged.pop(index)
                continue
            if merge_backward:
                prev_entry["end"] = current["end"]
                prev_entry["text"] = self._join_entry_text(str(prev_entry.get("text") or ""), str(current.get("text") or ""))
                merged.pop(index)
                index = max(0, index - 1)
                continue
            index += 1
        return merged

    def _is_merge_candidate_text(self, text: str) -> bool:
        """判断是否属于需要平滑的超短字幕内容"""
        normalized = " ".join(str(text or "").split())
        if not normalized:
            return False
        words = normalized.split()
        compact = normalized.strip(TERMINAL_PUNCTUATION + "\"'()[]{}“”‘’")
        return len(words) <= 2 or len(compact) <= 2

    def _is_short_continuation(self, prev_entry: Optional[dict], current: dict, duration_ms: int, max_duration_ms: int) -> bool:
        """识别上一句没收完就被拆出去的短尾巴，优先并回上一条字幕"""
        if not prev_entry or duration_ms > max_duration_ms:
            return False
        prev_text = " ".join(str(prev_entry.get("text") or "").split()).strip()
        current_text = " ".join(str(current.get("text") or "").split()).strip()
        if not prev_text or not current_text:
            return False
        if prev_text[-1] in TERMINAL_PUNCTUATION:
            return False
        return len(current_text.split()) <= 2

    def _prefer_merge_forward(self, text: str) -> bool:
        """冠词、介词等前导碎片优先并到后一条，词尾则并回前一条"""
        normalized = " ".join(str(text or "").split()).strip()
        if not normalized:
            return False
        if normalized[-1] in TERMINAL_PUNCTUATION:
            return False
        lowered = normalized.strip("\"'()[]{}“”‘’").lower()
        if lowered in LEADING_FRAGMENT_WORDS:
            return True
        return lowered.isalpha() and len(lowered.split()) == 1 and len(lowered) <= 3

    def _join_entry_text(self, left: str, right: str) -> str:
        """合并相邻字幕文本，英文保留空格，中日韩连续文本不额外插空格"""
        left_clean = " ".join(str(left or "").split())
        right_clean = " ".join(str(right or "").split())
        if not left_clean:
            return right_clean
        if not right_clean:
            return left_clean
        if self._is_cjk_tail(left_clean[-1]) and self._is_cjk_head(right_clean[0]):
            return f"{left_clean}{right_clean}"
        if right_clean[0] in TERMINAL_PUNCTUATION:
            return f"{left_clean}{right_clean}"
        return f"{left_clean} {right_clean}"

    def _entry_duration_ms(self, entry: dict) -> int:
        """读取字幕条目时长，统一按毫秒比较阈值"""
        start_ms = int(round(self._srt_time_to_seconds(str(entry.get("start") or "00:00:00,000")) * 1000))
        end_ms = int(round(self._srt_time_to_seconds(str(entry.get("end") or "00:00:00,000")) * 1000))
        return max(0, end_ms - start_ms)

    def _entry_gap_ms(self, left: Optional[dict], right: Optional[dict]) -> int:
        """计算相邻字幕之间的间隔，重叠时按 0 处理，方便平滑边界碎片"""
        if not left or not right:
            return 10**9
        left_end = int(round(self._srt_time_to_seconds(str(left.get("end") or "00:00:00,000")) * 1000))
        right_start = int(round(self._srt_time_to_seconds(str(right.get("start") or "00:00:00,000")) * 1000))
        return max(0, right_start - left_end)

    def _is_cjk_tail(self, char: str) -> bool:
        """判断末尾字符是否为中日韩文字，避免合并时插入多余空格"""
        return bool(re.match(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", char or ""))

    def _is_cjk_head(self, char: str) -> bool:
        """判断开头字符是否为中日韩文字，避免合并时插入多余空格"""
        return self._is_cjk_tail(char)

    def _beam_size(self) -> int:
        """读取识别 beam size，GPU 默认更准，CPU 默认更快"""
        if self.beam_size is not None:
            return max(1, min(8, int(self.beam_size)))
        configured = os.environ.get("YTV_ASR_BEAM_SIZE")
        if configured:
            try:
                return max(1, min(8, int(configured)))
            except ValueError:
                pass
        return 5 if self.device == "cuda" else 3

    def _vad_filter_enabled(self) -> bool:
        """读取 VAD 开关；默认开启但降低阈值，尽量保留细声细语"""
        configured = str(os.environ.get("YTV_ASR_VAD_FILTER") or "true").strip().lower()
        return configured not in {"0", "false", "no", "off"}

    def _vad_parameters(self) -> dict[str, int | float]:
        """生成更适合短视频低音量说话的 VAD 参数"""
        return {
            # 阈值偏低优先抓到轻声说话，纯音乐误入由补漏门槛和抗幻觉参数兜底
            "threshold": self._env_float("YTV_ASR_VAD_THRESHOLD", 0.2, 0.05, 0.9),
            "min_speech_duration_ms": self._env_int("YTV_ASR_VAD_MIN_SPEECH_MS", 80, 20, 1000),
            "min_silence_duration_ms": self._env_int("YTV_ASR_VAD_MIN_SILENCE_MS", 250, 80, 2000),
            # 垫片过大字幕会比声音早出，200ms 在保留词首和音画同步之间取平衡
            "speech_pad_ms": self._env_int("YTV_ASR_VAD_SPEECH_PAD_MS", 200, 0, 1000),
        }

    def _no_speech_threshold(self) -> float:
        """放宽 Whisper 的静音判定，避免低声句子被当成无语音跳过"""
        return self._env_float("YTV_ASR_NO_SPEECH_THRESHOLD", 0.8, 0.1, 1.0)

    def _env_int(self, key: str, default: int, minimum: int, maximum: int) -> int:
        """读取整数环境变量，并限制在安全范围内"""
        try:
            value = int(os.environ.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    def _env_float(self, key: str, default: float, minimum: float, maximum: float) -> float:
        """读取浮点环境变量，并限制在安全范围内"""
        try:
            value = float(os.environ.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    def _should_fallback_to_cpu(self) -> bool:
        """判断当前识别失败时是否允许回退 CPU"""
        return self.allow_cpu_fallback and self.device == "cuda"

    def _disable_cuda_asr_for_process(self, reason: Exception) -> None:
        """当前进程内短暂冷却自动 CUDA 识别，避免 CUDA 上下文损坏后反复重启"""
        global _CUDA_ASR_DISABLED, _CUDA_ASR_DISABLED_UNTIL, _CUDA_ASR_DISABLED_REASON
        cooldown_seconds = _asr_cuda_process_cooldown_seconds()
        _CUDA_ASR_DISABLED = cooldown_seconds > 0
        _CUDA_ASR_DISABLED_UNTIL = time.time() + cooldown_seconds if cooldown_seconds > 0 else 0.0
        _CUDA_ASR_DISABLED_REASON = str(reason or "").strip()
        for key in list(_MODEL_CACHE):
            if any(part == "cuda" or part == "cuda:0" for part in key):
                _MODEL_CACHE.pop(key, None)
        if cooldown_seconds > 0:
            logger.warning(f"本地识别 CUDA 冷却 {cooldown_seconds:.0f} 秒后会自动重试，当前先改用 CPU，原因: {reason}")
        else:
            logger.warning(f"本地识别 CUDA 失败但未启用进程冷却，原因: {reason}")

    def _load_sensevoice_model(self):
        """延迟加载 SenseVoice 模型，依赖缺失时给出可执行提示"""
        try:
            from funasr import AutoModel
        except ImportError as exc:
            raise RuntimeError("缺少 SenseVoice 本地识别依赖 funasr，请安装到 D:\\tools 的 Python 环境后再选择 SenseVoice") from exc

        model_id = os.environ.get("YTV_SENSEVOICE_MODEL") or self._local_model_path_or_default("SenseVoiceSmall", "iic/SenseVoiceSmall")
        vad_model = os.environ.get("YTV_SENSEVOICE_VAD_MODEL") or "fsmn-vad"
        key = ("sensevoice", model_id, vad_model, self.device)
        if key in _MODEL_CACHE:
            return _MODEL_CACHE[key]

        kwargs: dict[str, Any] = {
            "model": model_id,
            "trust_remote_code": True,
            "device": self._torch_device_label(),
        }
        if vad_model and vad_model.lower() not in {"none", "off", "false", "0"}:
            kwargs["vad_model"] = vad_model
            kwargs["vad_kwargs"] = {"max_single_segment_time": self._env_int("YTV_SENSEVOICE_MAX_SEGMENT_MS", 30000, 5000, 120000)}

        attempts = [
            kwargs,
            {key: value for key, value in kwargs.items() if key != "device"},
            {"model": model_id, "trust_remote_code": True},
            {"model": model_id},
        ]
        last_error: Optional[Exception] = None
        for attempt in attempts:
            try:
                model = AutoModel(**attempt)
                _MODEL_CACHE[key] = model
                return model
            except TypeError as exc:
                last_error = exc
                continue
        raise RuntimeError(f"SenseVoice 模型加载失败，当前 funasr 版本不兼容: {last_error}")

    def _load_qwen3_asr_model(self):
        """延迟加载 Qwen3-ASR 模型，默认启用 forced aligner 以获得字幕时间戳"""
        try:
            from qwen_asr import Qwen3ASRModel
        except ImportError as exc:
            raise RuntimeError("缺少 Qwen3-ASR 本地识别依赖 qwen-asr，请安装到 D:\\tools 的 Python 环境后再选择 Qwen3-ASR") from exc

        model_id = os.environ.get("YTV_QWEN3_ASR_MODEL") or self._local_model_path_or_default("Qwen3-ASR-0.6B", "Qwen/Qwen3-ASR-0.6B")
        aligner_id = os.environ.get("YTV_QWEN3_ASR_ALIGNER") or "Qwen/Qwen3-ForcedAligner-0.6B"
        key = ("qwen3-asr", model_id, aligner_id, self.device, self.compute_type)
        if key in _MODEL_CACHE:
            return _MODEL_CACHE[key]

        aligner_enabled = aligner_id and aligner_id.lower() not in {"none", "off", "false", "0"}
        device_label = self._torch_device_label()
        base_kwargs: dict[str, Any] = {}
        if aligner_enabled:
            base_kwargs["forced_aligner"] = aligner_id
        attempts = [
            {**base_kwargs, "device": device_label},
            {**base_kwargs, "device_map": device_label},
            base_kwargs,
            {},
        ]
        last_error: Optional[Exception] = None
        for kwargs in attempts:
            try:
                model = Qwen3ASRModel.from_pretrained(model_id, **kwargs)
                _MODEL_CACHE[key] = model
                return model
            except TypeError as exc:
                last_error = exc
                continue
        raise RuntimeError(f"Qwen3-ASR 模型加载失败，当前 qwen-asr 版本不兼容: {last_error}")

    def _local_model_path_or_default(self, folder_name: str, default_model_id: str) -> str:
        """本地模型目录存在时优先使用本地路径，否则使用官方模型 ID 让运行时自行下载"""
        candidate = os.path.join(self.model_dir, folder_name)
        return candidate if os.path.isdir(candidate) else default_model_id

    def _torch_device_label(self) -> str:
        """把内部设备名转换成 torch/funasr 常用设备写法"""
        return "cuda:0" if self.device == "cuda" else "cpu"

    def _segments_from_sensevoice_result(self, result: Any, audio_path: str, language: Optional[str]) -> tuple[list[Any], str]:
        """把 SenseVoice 返回结构转换成通用字幕片段"""
        segments: list[Any] = []
        detected_language = language or "auto"
        for item in self._result_items(result):
            detected_language = self._detect_result_language(item, detected_language)
            sentence_info = self._field(item, "sentence_info", "sentences", "segments", "chunks")
            if isinstance(sentence_info, list):
                for sentence in sentence_info:
                    segment = self._segment_from_timestamp_item(sentence, time_unit="ms")
                    if segment:
                        segments.append(segment)
            if not segments:
                text = self._clean_asr_text(self._field(item, "text", "sentence", "raw_text") or "")
                if text:
                    segments.append(self._whole_text_segment(text, audio_path))
        return segments, detected_language

    def _segments_from_qwen3_result(self, result: Any, audio_path: str, language: Optional[str]) -> tuple[list[Any], str]:
        """把 Qwen3-ASR 返回结构转换成通用字幕片段"""
        segments: list[Any] = []
        detected_language = language or "auto"
        for item in self._result_items(result):
            detected_language = self._detect_result_language(item, detected_language)
            text = self._clean_asr_text(self._field(item, "text", "transcription", "sentence", "raw_text") or "")
            timestamp_payload = self._field(item, "time_stamps", "timestamps", "timestamp", "segments", "chunks")
            parsed = self._segments_from_timestamp_payload(timestamp_payload, text)
            segments.extend(parsed)
            if not parsed and text:
                segments.append(self._whole_text_segment(text, audio_path))
        return segments, detected_language

    def _result_items(self, result: Any) -> list[Any]:
        """统一模型返回值为列表，兼容 tuple(result, meta) 和单对象返回"""
        payload = result[0] if isinstance(result, tuple) and result else result
        if isinstance(payload, list):
            return payload
        return [payload]

    def _detect_result_language(self, item: Any, fallback: str) -> str:
        """从识别结果中提取语言，没有就沿用入参或 auto"""
        language = self._field(item, "language", "lang", "detected_language")
        return str(language or fallback or "auto")

    def _segments_from_timestamp_payload(self, payload: Any, fallback_text: str) -> list[Any]:
        """解析常见时间戳数组结构，失败时返回空列表交给整段兜底"""
        if not isinstance(payload, list):
            return []
        segments: list[Any] = []
        for item in payload:
            segment = self._segment_from_timestamp_item(item)
            if segment:
                segments.append(segment)
        if segments:
            return segments
        if fallback_text:
            bounds = [self._timestamp_bounds(item) for item in payload]
            valid_bounds = [(start, end) for start, end in bounds if start is not None and end is not None and end > start]
            if valid_bounds:
                start = min(start for start, _ in valid_bounds)
                end = max(end for _, end in valid_bounds)
                return [SimpleNamespace(start=start, end=end, text=fallback_text, words=[])]
        return []

    def _segment_from_timestamp_item(self, item: Any, time_unit: str = "auto") -> Optional[Any]:
        """把单个带时间戳的结果项转为片段"""
        text = self._clean_asr_text(self._timestamp_text(item))
        start, end = self._timestamp_bounds(item, time_unit=time_unit)
        if not text or start is None or end is None or end <= start:
            return None
        return SimpleNamespace(start=start, end=max(end, start + 0.2), text=text, words=[])

    def _timestamp_text(self, item: Any) -> str:
        """从时间戳结果项中取文本，兼容 dict、对象和列表"""
        if isinstance(item, (list, tuple)):
            for value in item:
                if isinstance(value, str) and value.strip():
                    return value
            return ""
        return str(self._field(item, "text", "word", "sentence", "content") or "")

    def _timestamp_bounds(self, item: Any, time_unit: str = "auto") -> tuple[Optional[float], Optional[float]]:
        """从时间戳结果项中取起止时间，auto 模式按数值大小判断毫秒/秒"""
        if isinstance(item, (list, tuple)):
            numeric = [self._safe_float(value) for value in item if not isinstance(value, str)]
            numeric = [value for value in numeric if value is not None]
            if len(numeric) >= 2:
                return self._normalize_model_time(numeric[0], time_unit), self._normalize_model_time(numeric[1], time_unit)
            return None, None
        start = self._field(item, "start", "start_time", "begin", "begin_time", "start_ms", "offset_start")
        end = self._field(item, "end", "end_time", "finish", "finish_time", "end_ms", "offset_end")
        unit = "ms" if any(name in self._available_field_names(item) for name in {"start_ms", "end_ms"}) else time_unit
        start_value = self._safe_float(start)
        end_value = self._safe_float(end)
        return self._normalize_model_time(start_value, unit), self._normalize_model_time(end_value, unit)

    def _field(self, item: Any, *names: str) -> Any:
        """从 dict 或对象中按多个候选字段读取值"""
        if isinstance(item, dict):
            for name in names:
                if name in item:
                    return item.get(name)
            return None
        for name in names:
            if hasattr(item, name):
                return getattr(item, name)
        return None

    def _available_field_names(self, item: Any) -> set[str]:
        """返回结果项可见字段名，用于判断毫秒字段"""
        if isinstance(item, dict):
            return set(item.keys())
        return {name for name in dir(item) if not name.startswith("_")}

    def _safe_float(self, value: Any) -> Optional[float]:
        """安全转浮点数"""
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _normalize_model_time(self, value: Optional[float], time_unit: str = "auto") -> Optional[float]:
        """把模型时间戳统一成秒；SenseVoice 常见毫秒，Qwen 通常为秒"""
        if value is None:
            return None
        if time_unit == "ms":
            return max(0.0, value / 1000.0)
        if time_unit == "s":
            return max(0.0, value)
        return max(0.0, value / 1000.0 if abs(value) > 1000 else value)

    def _whole_text_segment(self, text: str, audio_path: str) -> Any:
        """模型未返回时间戳时，用整段音频时长兜底生成一个片段"""
        duration = self._audio_duration_seconds(audio_path) or 1.0
        return SimpleNamespace(start=0.0, end=max(0.2, duration), text=text, words=[])

    def _clean_asr_text(self, text: Any) -> str:
        """清理模型标签和多余空白，避免 SenseVoice 控制标签进入字幕"""
        cleaned = re.sub(r"<\|[^|]+?\|>", "", str(text or ""))
        return " ".join(cleaned.split())

    def _audio_duration_seconds(self, audio_path: str) -> Optional[float]:
        """读取音频时长，用于非 Whisper 模型的整段兜底和进度估算"""
        try:
            import wave
            with wave.open(audio_path, "rb") as wav_file:
                frames = wav_file.getnframes()
                rate = wav_file.getframerate()
                if rate > 0:
                    return frames / float(rate)
        except Exception:
            pass
        ffprobe = self._ffprobe_command()
        if not ffprobe:
            return None
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            if result.returncode == 0:
                duration = float(result.stdout.strip())
                return duration if duration > 0 else None
        except Exception:
            return None
        return None

    def _ffprobe_command(self) -> Optional[str]:
        """优先使用 D:\\tools\\ffmpeg 同目录的 ffprobe"""
        ffmpeg = get_ffmpeg_command()
        ffmpeg_dir = os.path.dirname(ffmpeg)
        exe_name = "ffprobe.exe" if os.name == "nt" else "ffprobe"
        candidate = os.path.join(ffmpeg_dir, exe_name)
        if candidate and os.path.exists(candidate):
            return candidate
        return exe_name

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

    def _srt_time_to_seconds(self, value: str) -> float:
        """把 SRT 时间码转换成秒，供进度估算使用"""
        parts = value.replace(",", ".").split(":")
        if len(parts) != 3:
            return 0.0
        try:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        except ValueError:
            return 0.0
