# backend/core/ffmpeg_processor.py
# FFmpeg 处理封装 - 视频合成、字幕烧录、音频处理

import os
import queue
import random
import re
import shutil
import subprocess
import tempfile
import threading
import time
from functools import lru_cache
from typing import Any, Callable, Optional
from ..utils import get_logger
from .paths import ensure_project_dirs, detect_video_workspace
from .process_control import normalize_control_keys, raise_if_control_requested, register_process, subprocess_creation_flags, terminate_process, unregister_process
from .tooling import get_ffmpeg_command

# 日志记录器
logger = get_logger("ffmpeg")

GPU_ENCODER_ORDER = ["h264_nvenc", "h264_qsv", "h264_amf"]
GPU_ENCODER_LABELS = {
    "h264_nvenc": "NVIDIA NVENC",
    "h264_qsv": "Intel QSV",
    "h264_amf": "AMD AMF",
}
FAST_SCALE_FLAGS = "flags=fast_bilinear"
VIDEO_ENCODER_OPTION_FLAGS = {
    "-c:v",
    "-preset",
    "-pix_fmt",
    "-crf",
    "-cq",
    "-global_quality",
    "-quality",
    "-b:v",
    "-qp_i",
    "-qp_p",
    "-rc",
    "-tune",
    "-multipass",
    "-bf",
    "-rc-lookahead",
    "-spatial-aq",
    "-temporal-aq",
    "-zerolatency",
}


@lru_cache(maxsize=1)
def available_ffmpeg_encoders(ffmpeg_cmd: str) -> set[str]:
    """读取当前 ffmpeg 支持的编码器列表，用于自动选择 GPU 编码"""
    try:
        result = subprocess.run(
            [ffmpeg_cmd, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except Exception:
        return set()
    if result.returncode != 0:
        return set()
    return set(result.stdout.split())


@lru_cache(maxsize=1)
def working_gpu_encoders(ffmpeg_cmd: str) -> tuple[str, ...]:
    """实际跑一段短编码，过滤掉列出但运行不可用的 GPU 编码器"""
    encoders = available_ffmpeg_encoders(ffmpeg_cmd)
    working: list[str] = []
    for encoder in GPU_ENCODER_ORDER:
        if encoder not in encoders:
            continue
        if _probe_gpu_encoder(ffmpeg_cmd, encoder):
            working.append(encoder)
    return tuple(working)


def _probe_gpu_encoder(ffmpeg_cmd: str, encoder: str) -> bool:
    """用短测试视频确认 GPU 编码器可以真正打开"""
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f"_{encoder}.mp4")
    temp_path = temp_file.name
    temp_file.close()
    cmd = [
        ffmpeg_cmd,
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-f", "lavfi",
        "-i", "testsrc=size=320x180:rate=24:duration=0.6",
        "-frames:v", "12",
        "-pix_fmt", "yuv420p",
    ]
    cmd.extend(_probe_encoder_args(encoder))
    cmd.append(temp_path)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
        return result.returncode == 0 and os.path.exists(temp_path) and os.path.getsize(temp_path) > 0
    except Exception:
        return False
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass


def _probe_encoder_args(encoder: str) -> list[str]:
    """生成 GPU 编码器探测参数"""
    if encoder == "h264_nvenc":
        return ["-c:v", encoder, "-preset", "p1", "-rc", "vbr", "-cq", "28"]
    if encoder == "h264_qsv":
        return ["-c:v", encoder, "-global_quality", "28"]
    if encoder == "h264_amf":
        return ["-c:v", encoder, "-quality", "speed", "-qp_i", "28", "-qp_p", "28"]
    return ["-c:v", encoder]


class FFmpegProcessor:
    """FFmpeg 视频处理封装类"""

    def __init__(self):
        """初始化处理器，检查 ffmpeg 是否可用"""
        self.ffmpeg_cmd = get_ffmpeg_command()
        logger.info(f"ffmpeg 路径: {self.ffmpeg_cmd}")

    def apply_effects(
        self,
        video_path: str,
        preset: dict[str, Any],
        output_path: Optional[str] = None,
        preview: bool = False,
        start_time: float = 0,
        duration: float = 8,
        control_keys: Optional[list[str]] = None,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> str:
        """
        应用画面处理预设
        preview=True 时只导出短片段，用于前端快速预览。
        """
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        if output_path is None:
            base_name = os.path.splitext(os.path.basename(video_path))[0]
            suffix = "preview" if preview else "enhanced"
            workspace_paths = detect_video_workspace(video_path)
            output_root = workspace_paths["output_dir"] if workspace_paths else ensure_project_dirs()["output_dir"]
            output_path = os.path.join(output_root, f"{base_name}_{suffix}.mp4")

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        filter_graph = self.build_effect_filter_graph(preset)
        bitrate = self._resolve_bitrate(preset)

        cmd = [self.ffmpeg_cmd]
        if preview:
            cmd.extend(["-ss", str(max(start_time, 0)), "-t", str(max(duration, 1))])

        cmd.extend(["-i", video_path])

        if filter_graph:
            cmd.extend(["-vf", filter_graph])

        encoder = self._resolve_video_encoder(preset)
        cmd.extend(self._video_encoder_args(preset, for_subtitles=False, encoder=encoder))

        if bitrate:
            cmd.extend(["-b:v", bitrate])
        elif encoder == "libx264":
            cmd.extend(["-crf", "23"])

        # 画面处理需要重新编码视频，音频默认直接复制以减少失真。
        cmd.extend(["-c:a", "copy", "-movflags", "+faststart", "-y", output_path])

        logger.info(f"应用画面处理: {video_path} -> {output_path}")
        logger.info(f"ffmpeg 滤镜: {filter_graph or '无'}")
        logger.info(f"视频编码器: {encoder}")

        return self._run_ffmpeg_with_cpu_fallback(
            cmd,
            "画面处理",
            timeout=120 if preview else 21600,
            encoder=encoder,
            preset=preset,
            for_subtitles=False,
            control_keys=control_keys,
            progress_callback=progress_callback,
            progress_total_seconds=max(duration, 1) if preview else self._media_duration_seconds(video_path),
        )

    def build_effect_filter_graph(self, preset: dict[str, Any]) -> str:
        """将画面处理预设转换为 ffmpeg filter graph"""
        filters: list[str] = []

        adjustments = preset.get("adjustments", {})
        if adjustments.get("enabled", True):
            eq_parts = []
            brightness = self._value(adjustments.get("brightness"))
            contrast = self._value(adjustments.get("contrast"))
            saturation = self._value(adjustments.get("saturation"))
            if brightness is not None:
                eq_parts.append(f"brightness={brightness:.4f}")
            if contrast is not None:
                eq_parts.append(f"contrast={contrast:.4f}")
            if saturation is not None:
                eq_parts.append(f"saturation={saturation:.4f}")
            if eq_parts:
                filters.append("eq=" + ":".join(eq_parts))

            sharpness = self._value(adjustments.get("sharpness"))
            if sharpness is not None and sharpness > 0:
                amount = max(0.0, min(sharpness, 5.0))
                filters.append(f"unsharp=5:5:{amount:.3f}:5:5:0.0")

            denoise = self._value(adjustments.get("denoise"))
            if denoise is not None and denoise > 0:
                strength = max(0.0, min(denoise, 10.0))
                filters.append(f"hqdn3d={strength:.3f}:{strength:.3f}:{strength * 2:.3f}:{strength * 2:.3f}")

        transform = preset.get("transform", {})
        if transform.get("enabled", True):
            rotate_mode = transform.get("rotate_mode", "none")
            if rotate_mode == "left90":
                filters.append("transpose=2")
            elif rotate_mode == "right90":
                filters.append("transpose=1")

            if transform.get("flip_horizontal"):
                filters.append("hflip")
            if transform.get("flip_vertical"):
                filters.append("vflip")

            angle = self._value(transform.get("random_rotate"))
            if angle is not None and abs(angle) > 0:
                radians = angle * 0.017453292519943295
                filters.append(f"rotate={radians:.6f}:fillcolor=black")

            if transform.get("remove_black_bars"):
                filters.append("cropdetect=24:16:0")

        canvas = preset.get("canvas", {})
        if canvas.get("enabled", True):
            target_width, target_height = self._target_size(canvas)
            mode = canvas.get("mode", "keep")
            scale_args = self._scale_args(canvas)
            if target_width and target_height:
                if mode == "stretch":
                    filters.append(f"scale={target_width}:{target_height}:{scale_args}")
                elif mode == "crop":
                    filters.append(
                        f"scale={target_width}:{target_height}:{scale_args}:force_original_aspect_ratio=increase,"
                        f"crop={target_width}:{target_height}"
                    )
                elif mode == "blur_background":
                    filters.append(
                        f"split[fg][bg];[bg]scale={target_width}:{target_height}:{scale_args}:force_original_aspect_ratio=increase,"
                        f"crop={target_width}:{target_height},boxblur=20:2[bg];"
                        f"[fg]scale={target_width}:{target_height}:{scale_args}:force_original_aspect_ratio=decrease[fg];"
                        f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
                    )
                else:
                    filters.append(
                        f"scale={target_width}:{target_height}:{scale_args}:force_original_aspect_ratio=decrease,"
                        f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:color=black"
                    )

        timing = preset.get("timing", {})
        if timing.get("enabled", True):
            fps = self._value(timing.get("fps"))
            if fps is not None and fps > 0:
                filters.append(f"fps={fps:.3f}")

            drop_frame = timing.get("drop_frame", {})
            if drop_frame.get("enabled"):
                interval = max(2, int(round(self._value(drop_frame.get("interval")) or 25)))
                filters.append(f"select='not(eq(mod(n\\,{interval})\\,0))'")

            zoom = self._value(timing.get("dynamic_zoom"))
            if zoom is not None and zoom > 0:
                zoom_value = max(1.0, min(1.0 + zoom, 1.5))
                filters.append(f"scale=iw*{zoom_value:.5f}:ih*{zoom_value:.5f},crop=iw/{zoom_value:.5f}:ih/{zoom_value:.5f}")

        return ",".join(filters)

    def burn_subtitles(
        self,
        video_path: str,
        subtitle_path: str,
        output_path: Optional[str] = None,
        preset: Optional[dict] = None,
        control_keys: Optional[list[str]] = None,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> str:
        """
        将字幕烧录到视频（硬字幕）
        返回：输出文件路径
        """
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"视频文件不存在: {video_path}")
        if not os.path.exists(subtitle_path):
            raise FileNotFoundError(f"字幕文件不存在: {subtitle_path}")

        # 生成输出路径
        if output_path is None:
            base_name = os.path.splitext(os.path.basename(video_path))[0]
            workspace_paths = detect_video_workspace(video_path)
            output_root = workspace_paths["output_dir"] if workspace_paths else ensure_project_dirs()["output_dir"]
            output_path = os.path.join(output_root, f"{base_name}_subtitled.mp4")

        filter_subtitle_path = self._prepare_subtitle_filter_file(subtitle_path)
        try:
            # 构建字幕滤镜
            subtitle_filter = self._build_subtitle_filter(filter_subtitle_path, preset)

            # 构建 ffmpeg 命令
            preset_dict = preset or {}
            bitrate = self._resolve_bitrate(preset_dict)
            # 字幕滤镜会把画面重新合成，这里仍优先 GPU，但编码参数会走字幕专用保守配置。
            encoder = self._resolve_video_encoder(preset_dict)
            cmd = [
                self.ffmpeg_cmd,
                "-i", video_path,           # 输入视频
                "-vf", subtitle_filter,     # 字幕滤镜
                "-c:a", "copy",             # 音频直接复制
            ]
            cmd.extend(self._video_encoder_args(preset_dict, for_subtitles=True, encoder=encoder))
            if bitrate:
                cmd.extend(["-b:v", bitrate])
            cmd.extend(["-y", output_path])

            logger.info(f"烧录字幕: {video_path} -> {output_path}")
            logger.info(f"字幕烧录视频编码器: {encoder}")

            return self._run_ffmpeg_with_cpu_fallback(
                cmd,
                "字幕烧录",
                timeout=21600,
                encoder=encoder,
                preset=preset_dict,
                for_subtitles=True,
                control_keys=control_keys,
                progress_callback=progress_callback,
                progress_total_seconds=self._media_duration_seconds(video_path),
            )
        finally:
            self._cleanup_subtitle_filter_file(filter_subtitle_path, subtitle_path)

    def _build_subtitle_filter(self, subtitle_path: str, preset: Optional[dict] = None) -> str:
        """构建字幕滤镜字符串"""
        # subtitles 滤镜要转义 Windows 盘符；复杂文件名已在调用前复制成安全临时文件。
        escaped_path = subtitle_path.replace("\\", "/").replace(":", "\\:")
        subtitle_ext = os.path.splitext(subtitle_path)[1].lower()

        # ASS 已经带完整样式，不能再用 force_style 覆盖，否则双行颜色/字号会被主样式抹掉。
        if subtitle_ext == ".ass":
            return f"subtitles='{escaped_path}'"

        if preset is None:
            # 默认字幕样式
            return f"subtitles='{escaped_path}':force_style='FontSize=24,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,Shadow=1'"

        # 根据预设构建样式
        style_parts = []

        if "font_name" in preset:
            style_parts.append(f"FontName={preset['font_name']}")
        if "font_size" in preset:
            style_parts.append(f"FontSize={preset['font_size']}")
        if "font_color" in preset:
            # 将十六进制颜色转换为 ASS 格式 (BGR)
            color = preset["font_color"].lstrip("#")
            bgr = color[4:6] + color[2:4] + color[0:2]
            style_parts.append(f"PrimaryColour=&H00{bgr}")
        if "secondary_color" in preset:
            color = preset["secondary_color"].lstrip("#")
            bgr = color[4:6] + color[2:4] + color[0:2]
            style_parts.append(f"SecondaryColour=&H00{bgr}")
        if "outline_color" in preset:
            color = preset["outline_color"].lstrip("#")
            bgr = color[4:6] + color[2:4] + color[0:2]
            style_parts.append(f"OutlineColour=&H00{bgr}")
        if "outline_width" in preset:
            style_parts.append(f"Outline={preset['outline_width']}")
        if preset.get("shadow_enabled", True):
            style_parts.append(f"Shadow={max(abs(int(preset.get('shadow_x', 2))), abs(int(preset.get('shadow_y', 2))))}")
        else:
            style_parts.append("Shadow=0")
        if "background_alpha" in preset:
            alpha = max(0, min(int(preset["background_alpha"]), 255))
            style_parts.append(f"BackColour=&H{alpha:02X}000000")
        if "margin_v" in preset:
            style_parts.append(f"MarginV={preset['margin_v']}")
        if "position" in preset:
            alignment_map = {
                "bottom_left": 1,
                "bottom": 2,
                "bottom_right": 3,
                "middle_left": 4,
                "center": 5,
                "middle_right": 6,
                "top_left": 7,
                "top": 8,
                "top_right": 9,
            }
            style_parts.append(f"Alignment={alignment_map.get(preset['position'], 2)}")

        style_str = ",".join(style_parts) if style_parts else ""
        return f"subtitles='{escaped_path}':force_style='{style_str}'"

    def _prepare_subtitle_filter_file(self, subtitle_path: str) -> str:
        """复制字幕到安全临时文件名，绕开 FFmpeg 对单引号和特殊字符路径的解析问题"""
        subtitle_ext = os.path.splitext(subtitle_path)[1] or ".ass"
        temp_file = tempfile.NamedTemporaryFile(delete=False, prefix="ff_subtitle_", suffix=subtitle_ext)
        temp_path = temp_file.name
        temp_file.close()
        shutil.copyfile(subtitle_path, temp_path)
        return temp_path

    def _cleanup_subtitle_filter_file(self, filter_subtitle_path: str, original_subtitle_path: str) -> None:
        """清理字幕滤镜临时文件，失败不影响主流程错误上抛"""
        if os.path.abspath(filter_subtitle_path) == os.path.abspath(original_subtitle_path):
            return
        try:
            if os.path.exists(filter_subtitle_path):
                os.remove(filter_subtitle_path)
        except OSError:
            pass

    def merge_audio_video(
        self,
        video_path: str,
        audio_path: str,
        output_path: Optional[str] = None,
        mode: str = "replace",
        volume_ratio: float = 1.0,
        control_keys: Optional[list[str]] = None,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> str:
        """
        合并音频和视频
        mode: replace（替换原声）/ mix（混合）/ overlay（叠加）
        volume_ratio: 原声音量比例（0.0-1.0）
        """
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"视频文件不存在: {video_path}")
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"音频文件不存在: {audio_path}")

        # 生成输出路径
        if output_path is None:
            base_name = os.path.splitext(os.path.basename(video_path))[0]
            workspace_paths = detect_video_workspace(video_path)
            output_root = workspace_paths["output_dir"] if workspace_paths else ensure_project_dirs()["output_dir"]
            output_path = os.path.join(output_root, f"{base_name}_voiced.mp4")

        normalized_mode = (mode or "mix").strip().lower()
        if normalized_mode == "replace":
            # 替换原声
            duration_seconds = self._media_duration_seconds(video_path)
            cmd = [
                self.ffmpeg_cmd,
                "-i", video_path,
                "-i", audio_path,
                "-c:v", "copy",
                "-map", "0:v:0",
                "-map", "1:a:0",
            ]
            if duration_seconds:
                cmd.extend(["-t", f"{duration_seconds:.3f}"])
            cmd.extend([
                "-y",
                output_path
            ])
        elif normalized_mode == "mix":
            # 混合音频，保留原视频的 BGM、游戏声音和环境声。
            cmd = [
                self.ffmpeg_cmd,
                "-i", video_path,
                "-i", audio_path,
                "-filter_complex",
                f"[0:a:0]volume={volume_ratio}[a0];[1:a:0]volume=1.0[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=0[out]",
                "-map", "0:v:0",
                "-map", "[out]",
                "-c:v", "copy",
                "-y",
                output_path
            ]
        else:
            raise ValueError(f"不支持的合并模式: {mode}")

        logger.info(f"合并音视频: {video_path} + {audio_path} -> {output_path}")
        return self._run_ffmpeg(
            cmd,
            "音视频合并",
            timeout=21600,
            control_keys=control_keys,
            progress_callback=progress_callback,
            progress_total_seconds=self._media_duration_seconds(video_path),
        )

    def convert_format(
        self,
        input_path: str,
        output_format: str = "mp4",
        output_path: Optional[str] = None,
        control_keys: Optional[list[str]] = None,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> str:
        """转换视频格式；同格式导出直接复制，避免无意义启动 ffmpeg"""
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"输入文件不存在: {input_path}")

        output_format = (output_format or "mp4").strip().lower().lstrip(".") or "mp4"
        if output_path is None:
            base_name = os.path.splitext(os.path.basename(input_path))[0]
            workspace_paths = detect_video_workspace(input_path)
            output_root = workspace_paths["exports_dir"] if workspace_paths else ensure_project_dirs()["exports_dir"]
            output_path = os.path.join(output_root, f"{base_name}.{output_format}")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        input_ext = os.path.splitext(input_path)[1].lower().lstrip(".")
        if input_ext == output_format:
            logger.info(f"复制导出: {input_path} -> {output_path}")
            return self._copy_media_file(
                input_path=input_path,
                output_path=output_path,
                control_keys=control_keys,
                progress_callback=progress_callback,
            )

        temp_output_path = self._temporary_output_path(output_path)
        cmd = [
            self.ffmpeg_cmd,
            "-i", input_path,
            "-c", "copy",
            "-y",
            temp_output_path
        ]

        logger.info(f"转换格式: {input_path} -> {output_path}")

        try:
            self._run_ffmpeg(
                cmd,
                "格式转换",
                timeout=7200,
                control_keys=control_keys,
                progress_callback=progress_callback,
                progress_total_seconds=self._media_duration_seconds(input_path),
            )
            os.replace(temp_output_path, output_path)
            return output_path
        finally:
            if os.path.exists(temp_output_path):
                try:
                    os.remove(temp_output_path)
                except OSError:
                    pass

    def _copy_media_file(
        self,
        input_path: str,
        output_path: str,
        control_keys: Optional[list[str]] = None,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> str:
        """按块复制媒体文件，成功后原子替换最终导出文件"""
        source_path = os.path.abspath(input_path)
        target_path = os.path.abspath(output_path)
        if source_path == target_path:
            if progress_callback:
                progress_callback(100)
            return output_path

        control_keys = normalize_control_keys(control_keys)
        temp_output_path = self._temporary_output_path(output_path)
        total_size = max(1, os.path.getsize(input_path))
        copied_size = 0
        try:
            raise_if_control_requested(control_keys)
            with open(input_path, "rb") as source, open(temp_output_path, "wb") as target:
                while True:
                    raise_if_control_requested(control_keys)
                    chunk = source.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    target.write(chunk)
                    copied_size += len(chunk)
                    if progress_callback:
                        progress_callback(min(99.0, copied_size / total_size * 100))
            os.replace(temp_output_path, output_path)
            if progress_callback:
                progress_callback(100)
            return output_path
        finally:
            if os.path.exists(temp_output_path):
                try:
                    os.remove(temp_output_path)
                except OSError:
                    pass

    def _temporary_output_path(self, output_path: str) -> str:
        """生成同目录临时输出路径，避免失败时留下半截成品"""
        directory = os.path.dirname(output_path) or "."
        stem, ext = os.path.splitext(os.path.basename(output_path))
        return os.path.join(directory, f".{stem}.{os.getpid()}.{threading.get_ident()}.tmp{ext}")

    def _run_ffmpeg(
        self,
        cmd: list[str],
        action_name: str,
        timeout: int = 600,
        control_keys: Optional[list[str]] = None,
        progress_callback: Optional[Callable[[float], None]] = None,
        progress_total_seconds: Optional[float] = None,
    ) -> str:
        """执行 ffmpeg 命令并统一处理错误"""
        output_path = cmd[-1]
        control_keys = normalize_control_keys(control_keys)
        process = None
        try:
            raise_if_control_requested(control_keys)
            run_cmd = self._command_with_progress(cmd) if progress_callback else cmd
            process = subprocess.Popen(
                run_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT if progress_callback else subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess_creation_flags(),
            )
            register_process(control_keys, process, run_cmd)
            if progress_callback:
                stdout, stderr = self._communicate_with_progress(
                    process=process,
                    timeout=timeout,
                    control_keys=control_keys,
                    progress_callback=progress_callback,
                    total_seconds=progress_total_seconds,
                )
            else:
                stdout, stderr = process.communicate(timeout=timeout)
            raise_if_control_requested(control_keys)

            if process.returncode != 0:
                raise RuntimeError(f"{action_name}失败: {self._format_ffmpeg_error(stderr or stdout, process.returncode)}")

            logger.info(f"{action_name}完成: {output_path}")
            return output_path
        except subprocess.TimeoutExpired:
            if process:
                terminate_process(process)
            raise RuntimeError(f"{action_name}超时")
        except Exception:
            if process and process.poll() is None:
                terminate_process(process)
            raise
        finally:
            if process:
                unregister_process(process)

    def _run_ffmpeg_with_cpu_fallback(
        self,
        cmd: list[str],
        action_name: str,
        timeout: int,
        encoder: str,
        preset: dict[str, Any],
        for_subtitles: bool,
        control_keys: Optional[list[str]] = None,
        progress_callback: Optional[Callable[[float], None]] = None,
        progress_total_seconds: Optional[float] = None,
    ) -> str:
        """GPU 编码失败时先换其他 GPU，最后才回退 CPU，避免直接进入慢速路径"""
        attempts: list[tuple[str, list[str], str]] = [(encoder, cmd, action_name)]
        if encoder != "libx264":
            for fallback_encoder in self._fallback_gpu_encoders(encoder):
                gpu_args = self._video_encoder_args(preset, for_subtitles=for_subtitles, encoder=fallback_encoder)
                attempts.append((fallback_encoder, self._replace_video_encoder_args(cmd, gpu_args), f"{action_name}{GPU_ENCODER_LABELS.get(fallback_encoder, fallback_encoder)}回退"))
            cpu_args = self._video_encoder_args(preset, for_subtitles=for_subtitles, encoder="libx264")
            attempts.append(("libx264", self._replace_video_encoder_args(cmd, cpu_args), f"{action_name}CPU兜底"))

        errors: list[str] = []
        for index, (attempt_encoder, attempt_cmd, attempt_name) in enumerate(attempts):
            try:
                if index > 0:
                    logger.warning(f"{action_name}切换编码器到 {GPU_ENCODER_LABELS.get(attempt_encoder, attempt_encoder)}")
                return self._run_ffmpeg(
                    attempt_cmd,
                    attempt_name,
                    timeout=timeout,
                    control_keys=control_keys,
                    progress_callback=progress_callback,
                    progress_total_seconds=progress_total_seconds,
                )
            except RuntimeError as exc:
                errors.append(f"{attempt_encoder}: {exc}")
                if attempt_encoder == "libx264":
                    raise RuntimeError(f"{action_name}失败，GPU 和 CPU 兜底均不可用: {'；'.join(errors)}") from exc
                logger.warning(f"{action_name}使用 {attempt_encoder} 失败: {exc}")

        raise RuntimeError(f"{action_name}失败: {'；'.join(errors)}")

    def _communicate_with_progress(
        self,
        process: subprocess.Popen,
        timeout: int,
        control_keys: list[str],
        progress_callback: Callable[[float], None],
        total_seconds: Optional[float],
    ) -> tuple[str, str]:
        """实时读取 ffmpeg -progress 输出，并把处理进度回调给任务系统"""
        output_lines: list[str] = []
        line_queue: queue.Queue[str | None] = queue.Queue()

        def read_stdout() -> None:
            """后台读取管道，避免主线程阻塞后无法响应取消"""
            try:
                if process.stdout:
                    for raw_line in process.stdout:
                        line_queue.put(raw_line)
            finally:
                line_queue.put(None)

        reader = threading.Thread(target=read_stdout, daemon=True)
        reader.start()
        deadline = time.time() + timeout
        reader_done = False
        last_progress = -1.0

        while True:
            raise_if_control_requested(control_keys)
            if time.time() > deadline:
                terminate_process(process)
                raise subprocess.TimeoutExpired(process.args, timeout)

            try:
                line = line_queue.get(timeout=0.2)
            except queue.Empty:
                if process.poll() is not None and reader_done:
                    break
                continue

            if line is None:
                reader_done = True
                if process.poll() is not None:
                    break
                continue

            text = line.strip()
            if text:
                output_lines.append(text)
                output_lines = output_lines[-160:]
                parsed_progress = self._parse_progress_line(text, total_seconds)
                if parsed_progress is not None and parsed_progress >= last_progress:
                    last_progress = parsed_progress
                    progress_callback(parsed_progress)

            if process.poll() is not None and reader_done:
                break

        process.wait(timeout=5)
        reader.join(timeout=2)
        if process.stdout:
            process.stdout.close()
        if process.returncode == 0:
            progress_callback(100)
        return "\n".join(output_lines), ""

    def _format_ffmpeg_error(self, output: str, returncode: int) -> str:
        """压缩 ffmpeg 错误，只保留真正有用的尾部信息"""
        lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
        useful_lines = [
            line for line in lines
            if not line.startswith(("ffmpeg version", "built with", "configuration:", "libav", "libsw"))
        ]
        tail = useful_lines[-30:] if useful_lines else lines[-30:]
        detail = "\n".join(tail).strip()
        return f"退出码 {returncode}" + (f"\n{detail}" if detail else "")

    def _command_with_progress(self, cmd: list[str]) -> list[str]:
        """给 ffmpeg 命令加上机器可读进度输出"""
        if "-progress" in cmd:
            return cmd
        return [cmd[0], "-nostdin", "-nostats", "-stats_period", "1", "-progress", "pipe:1", *cmd[1:]]

    def _parse_progress_line(self, line: str, total_seconds: Optional[float]) -> Optional[float]:
        """解析 ffmpeg 进度行，转换为 0-99 的百分比"""
        if not total_seconds or total_seconds <= 0:
            return None
        seconds = None
        if line.startswith(("out_time_ms=", "out_time_us=")):
            try:
                seconds = int(line.split("=", 1)[1]) / 1_000_000
            except ValueError:
                return None
        elif line.startswith("out_time="):
            seconds = self._timestamp_to_seconds(line.split("=", 1)[1])
        if seconds is None:
            return None
        return max(0.0, min(99.0, seconds / total_seconds * 100))

    def _timestamp_to_seconds(self, value: str) -> Optional[float]:
        """把 ffmpeg 时间戳转换成秒"""
        match = re.match(r"(?P<h>\d+):(?P<m>\d+):(?P<s>\d+(?:\.\d+)?)", value.strip())
        if not match:
            return None
        return int(match.group("h")) * 3600 + int(match.group("m")) * 60 + float(match.group("s"))

    def _media_duration_seconds(self, media_path: str) -> Optional[float]:
        """读取媒体时长，用于把 ffmpeg 时间进度换算成百分比"""
        ffprobe_cmd = self._ffprobe_command()
        if ffprobe_cmd:
            try:
                result = subprocess.run(
                    [
                        ffprobe_cmd,
                        "-v", "error",
                        "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1",
                        media_path,
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=20,
                    check=False,
                )
                if result.returncode == 0 and result.stdout.strip():
                    duration = float(result.stdout.strip())
                    return duration if duration > 0 else None
            except Exception:
                pass
        return self._media_duration_from_ffmpeg(media_path)

    def media_video_size(self, media_path: str) -> Optional[tuple[int, int]]:
        """读取视频实际宽高，用于让 ASS 字幕坐标和烧录画布保持一致"""
        ffprobe_cmd = self._ffprobe_command()
        if ffprobe_cmd:
            try:
                result = subprocess.run(
                    [
                        ffprobe_cmd,
                        "-v", "error",
                        "-select_streams", "v:0",
                        "-show_entries", "stream=width,height",
                        "-of", "csv=p=0:s=x",
                        media_path,
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=20,
                    check=False,
                )
                if result.returncode == 0:
                    parsed = self._parse_video_size(result.stdout)
                    if parsed:
                        return parsed
            except Exception:
                pass
        return self._media_video_size_from_ffmpeg(media_path)

    def _media_video_size_from_ffmpeg(self, media_path: str) -> Optional[tuple[int, int]]:
        """没有 ffprobe 时，从 ffmpeg 探测输出里解析视频分辨率"""
        try:
            result = subprocess.run(
                [self.ffmpeg_cmd, "-hide_banner", "-i", media_path],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                check=False,
            )
        except Exception:
            return None
        return self._parse_video_size(f"{result.stdout}\n{result.stderr}")

    def _parse_video_size(self, output: str) -> Optional[tuple[int, int]]:
        """从 ffprobe/ffmpeg 输出里提取第一路视频宽高"""
        match = re.search(r"(?P<w>\d{2,5})x(?P<h>\d{2,5})(?:\s|,|\[)", str(output or ""))
        if not match:
            return None
        width = int(match.group("w"))
        height = int(match.group("h"))
        if width <= 0 or height <= 0:
            return None
        return width, height

    def _ffprobe_command(self) -> Optional[str]:
        """优先使用 D:\\tools\\ffmpeg 同目录里的 ffprobe"""
        ffmpeg_dir = os.path.dirname(self.ffmpeg_cmd) if os.path.isabs(self.ffmpeg_cmd) else ""
        if ffmpeg_dir:
            ffprobe_path = os.path.join(ffmpeg_dir, "ffprobe.exe" if os.name == "nt" else "ffprobe")
            if os.path.exists(ffprobe_path):
                return ffprobe_path
        return None

    def _media_duration_from_ffmpeg(self, media_path: str) -> Optional[float]:
        """没有 ffprobe 时，从 ffmpeg 探测输出里兜底解析时长"""
        try:
            result = subprocess.run(
                [self.ffmpeg_cmd, "-hide_banner", "-i", media_path],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                check=False,
            )
        except Exception:
            return None
        output = f"{result.stdout}\n{result.stderr}"
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
        if not match:
            return None
        return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))

    def _replace_video_encoder_args(self, cmd: list[str], replacement_args: list[str]) -> list[str]:
        """把命令中的视频编码器参数替换为新的编码器参数"""
        cleaned: list[str] = []
        index = 0
        while index < len(cmd):
            item = cmd[index]
            if item in VIDEO_ENCODER_OPTION_FLAGS and index + 1 < len(cmd):
                index += 2
                continue
            cleaned.append(item)
            index += 1

        try:
            insert_index = len(cleaned) - 1 - list(reversed(cleaned)).index("-y")
        except ValueError:
            insert_index = max(len(cleaned) - 1, 0)
        return cleaned[:insert_index] + replacement_args + cleaned[insert_index:]

    def _value(self, config: Any) -> Optional[float]:
        """解析固定值或随机范围配置"""
        if config is None:
            return None
        if isinstance(config, (int, float)):
            return float(config)
        if isinstance(config, dict):
            if config.get("enabled") is False:
                return None
            if config.get("random"):
                min_value = float(config.get("min", config.get("value", 0)))
                max_value = float(config.get("max", min_value))
                if max_value < min_value:
                    min_value, max_value = max_value, min_value
                return random.uniform(min_value, max_value)
            if "value" in config:
                return float(config["value"])
            if "min" in config and "max" in config:
                return float(config["min"])
        return None

    def _target_size(self, canvas: dict[str, Any]) -> tuple[Optional[int], Optional[int]]:
        """解析目标画布尺寸"""
        resolution = canvas.get("resolution", "720p")
        if resolution == "720p":
            return 1280, 720
        if resolution == "1080p":
            return 1920, 1080
        if resolution == "custom":
            return int(canvas.get("width") or 1280), int(canvas.get("height") or 720)
        return None, None

    def _scale_args(self, canvas: dict[str, Any]) -> str:
        """生成缩放参数，默认用最快算法换取更快的 1080p 输出"""
        return str(canvas.get("scale_flags") or FAST_SCALE_FLAGS)

    def _resolve_bitrate(self, preset: dict[str, Any]) -> Optional[str]:
        """解析码率配置"""
        bitrate = preset.get("bitrate", {})
        if not bitrate.get("enabled", True):
            return None
        mode = bitrate.get("mode", "fixed")
        if mode == "fixed":
            kbps = int(self._value(bitrate.get("fixed_kbps")) or 0)
            return f"{kbps}k" if kbps > 0 else None
        return None

    def _resolve_video_encoder(self, preset: dict[str, Any]) -> str:
        """根据用户配置和本机能力选择视频编码器"""
        acceleration = preset.get("acceleration", {}) if isinstance(preset, dict) else {}
        if acceleration.get("enabled") is False:
            return "libx264"
        mode = str(acceleration.get("mode") or "auto")
        encoder_map = {
            "cpu": "libx264",
            "nvidia": "h264_nvenc",
            "intel": "h264_qsv",
            "amd": "h264_amf",
        }
        if mode in encoder_map:
            encoder = encoder_map[mode]
            if encoder == "libx264" or encoder in working_gpu_encoders(self.ffmpeg_cmd):
                return encoder
            return self._first_working_gpu_encoder() or "libx264"

        return self._first_working_gpu_encoder() or "libx264"

    def _first_working_gpu_encoder(self) -> Optional[str]:
        """读取首个实测可用 GPU 编码器"""
        encoders = working_gpu_encoders(self.ffmpeg_cmd)
        return encoders[0] if encoders else None

    def _fallback_gpu_encoders(self, current_encoder: str) -> list[str]:
        """返回除当前编码器外的实测可用 GPU 兜底列表"""
        return [encoder for encoder in working_gpu_encoders(self.ffmpeg_cmd) if encoder != current_encoder]

    def _video_encoder_args(self, preset: dict[str, Any], for_subtitles: bool, encoder: Optional[str] = None) -> list[str]:
        """生成视频编码参数，GPU 不可用时回退 CPU"""
        encoder = encoder or self._resolve_video_encoder(preset)
        bitrate = self._resolve_bitrate(preset)
        if encoder == "libx264":
            cpu_preset = "fast" if for_subtitles else "medium"
            args = ["-c:v", "libx264", "-preset", cpu_preset, "-pix_fmt", "yuv420p"]
            if for_subtitles and not bitrate:
                # 字幕烧录必须重编码，但默认不应该把体积顶得比原片大很多。
                args.extend(["-crf", "23"])
            return args

        quality = str((preset.get("acceleration") or {}).get("quality") or "balanced")
        nvenc_preset = {"quality": "p5", "balanced": "p3", "size": "p1"}.get(quality, "p3")
        cq_value = "20" if quality == "quality" else "28" if quality == "size" else "24"
        args = ["-c:v", encoder, "-pix_fmt", "yuv420p"]
        if encoder == "h264_nvenc":
            if for_subtitles:
                subtitle_preset = {"quality": "p5", "balanced": "p4", "size": "p3"}.get(quality, "p4")
                subtitle_cq = "20" if quality == "quality" else "26" if quality == "size" else "23"
                args.extend(["-preset", subtitle_preset, "-rc", "vbr", "-cq", subtitle_cq])
                if not bitrate:
                    args.extend(["-b:v", "0"])
            else:
                args.extend(["-preset", nvenc_preset, "-tune", "ull", "-rc", "vbr", "-cq", cq_value, "-multipass", "disabled", "-bf", "0", "-rc-lookahead", "0", "-spatial-aq", "0", "-temporal-aq", "0", "-zerolatency", "1"])
        elif encoder == "h264_qsv":
            args.extend(["-global_quality", cq_value])
        elif encoder == "h264_amf":
            amf_quality = {"quality": "quality", "balanced": "balanced", "size": "speed"}.get(quality, "balanced")
            args.extend(["-quality", amf_quality, "-qp_i", cq_value, "-qp_p", cq_value])
        return args
