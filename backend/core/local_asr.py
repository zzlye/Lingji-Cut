# backend/core/local_asr.py
# 本地语音识别 - 使用 faster-whisper 在本机从视频音频生成带时间轴字幕

import json
import os
import re
import subprocess
import sys
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

# 模型缓存，避免同一轮任务重复加载模型。
_MODEL_CACHE: dict[tuple[str, str, str, str, int], object] = {}
# CUDA 一旦出现运行级错误，当前进程内继续使用 CUDA 往往会反复失败。
_CUDA_ASR_DISABLED = False
CUDA_DISABLED_FLAG_NAME = "asr-cuda-disabled.flag"
ASR_WORKER_EVENT_PREFIX = "__YTV_ASR_WORKER__"
ASR_INPUT_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".opus", ".ogg"}


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


def asr_cuda_disabled_flag_path() -> str:
    """返回 CUDA ASR 熔断标记路径，Electron 检测到原生崩溃后会写入该文件"""
    return os.environ.get("YTV_ASR_CUDA_DISABLED_FLAG") or os.path.join(default_data_root(), "data", CUDA_DISABLED_FLAG_NAME)


def _asr_force_cuda_enabled() -> bool:
    """读取强制 CUDA 开关，调试时允许临时忽略熔断标记"""
    return str(os.environ.get("YTV_ASR_FORCE_CUDA") or "").strip().lower() in {"1", "true", "yes", "on"}


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
    """判断是否因上次 CUDA 原生崩溃而禁用本地识别 CUDA"""
    if _asr_force_cuda_enabled():
        return False
    try:
        return os.path.exists(asr_cuda_disabled_flag_path())
    except OSError:
        return False


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
    if _CUDA_ASR_DISABLED:
        logger.warning("本地识别 CUDA 已在当前进程内禁用，自动改用 CPU")
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
        self.model_name = configured_model or (default_gpu_asr_model_name() if self.device == "cuda" else default_asr_model_name())
        self.compute_type = compute_type or default_asr_compute_type(self.device)
        self.cpu_threads = cpu_threads or min(4, max(1, os.cpu_count() or 1))
        self.beam_size = beam_size
        self._log_device_profile(requested_device, normalized_device)

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
            try:
                return self._transcribe_video_in_worker(video_path, language, progress_callback)
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

    def _transcribe_video_in_worker(
        self,
        video_path: str,
        language: Optional[str],
        progress_callback: Optional[Callable[[float], None]],
    ) -> tuple[list[dict], str]:
        """在独立 Python 子进程中执行 CUDA 识别，子进程崩溃时主后端仍可回退 CPU"""
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        worker_path = os.path.join(project_root, "backend", "core", "local_asr_worker.py")
        env = os.environ.copy()
        env["YTV_ASR_CHILD_WORKER"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONPATH"] = os.pathsep.join([project_root, env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
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

        logger.info(f"本地识别 CUDA 子进程启动: model={self.model_name}, compute={self.compute_type}")
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
        for raw_line in process.stdout:
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
                continue
            logger.info(f"本地识别子进程: {line}")

        return_code = process.wait()
        if return_code != 0:
            reason = error_message or f"退出码 {return_code}"
            if return_code != 1:
                mark_asr_cuda_disabled(f"本地识别 CUDA 子进程异常退出，{reason}")
            raise RuntimeError(f"本地识别 CUDA 子进程失败: {reason}")
        if not result_payload:
            raise RuntimeError("本地识别 CUDA 子进程没有返回结果")
        entries = result_payload.get("entries")
        if not isinstance(entries, list):
            raise RuntimeError("本地识别 CUDA 子进程返回结果格式错误")
        language_value = str(result_payload.get("language") or language or "auto")
        return entries, language_value

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
        """当前进程内禁用后续自动 CUDA 识别，避免 CUDA 上下文损坏后反复重启"""
        global _CUDA_ASR_DISABLED
        _CUDA_ASR_DISABLED = True
        for key in list(_MODEL_CACHE):
            if len(key) >= 3 and key[2] == "cuda":
                _MODEL_CACHE.pop(key, None)
        logger.warning(f"后续本地识别将改用 CPU，原因: {reason}")

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
