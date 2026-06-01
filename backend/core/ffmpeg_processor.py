# backend/core/ffmpeg_processor.py
# FFmpeg 处理封装 - 视频合成、字幕烧录、音频处理

import os
import random
import subprocess
from typing import Any, Optional
from ..utils import get_logger
from .paths import ensure_project_dirs

# 日志记录器
logger = get_logger("ffmpeg")

# 工具路径配置
TOOLS_DIR = r"D:\tools"
FFMPEG_PATH = os.path.join(TOOLS_DIR, "ffmpeg", "ffmpeg.exe")

class FFmpegProcessor:
    """FFmpeg 视频处理封装类"""

    def __init__(self):
        """初始化处理器，检查 ffmpeg 是否可用"""
        if os.path.exists(FFMPEG_PATH):
            self.ffmpeg_cmd = FFMPEG_PATH
        else:
            self.ffmpeg_cmd = "ffmpeg"
        logger.info(f"ffmpeg 路径: {self.ffmpeg_cmd}")

    def apply_effects(
        self,
        video_path: str,
        preset: dict[str, Any],
        output_path: Optional[str] = None,
        preview: bool = False,
        start_time: float = 0,
        duration: float = 8,
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
            output_path = os.path.join(ensure_project_dirs()["output_dir"], f"{base_name}_{suffix}.mp4")

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        filter_graph = self.build_effect_filter_graph(preset)
        bitrate = self._resolve_bitrate(preset)

        cmd = [self.ffmpeg_cmd]
        if preview:
            cmd.extend(["-ss", str(max(start_time, 0)), "-t", str(max(duration, 1))])

        cmd.extend(["-i", video_path])

        if filter_graph:
            cmd.extend(["-vf", filter_graph])

        cmd.extend([
            "-c:v", "libx264",
            "-preset", "medium",
            "-pix_fmt", "yuv420p",
        ])

        if bitrate:
            cmd.extend(["-b:v", bitrate])
        else:
            cmd.extend(["-crf", "23"])

        # 画面处理需要重新编码视频，音频默认直接复制以减少失真。
        cmd.extend(["-c:a", "copy", "-movflags", "+faststart", "-y", output_path])

        logger.info(f"应用画面处理: {video_path} -> {output_path}")
        logger.info(f"ffmpeg 滤镜: {filter_graph or '无'}")

        return self._run_ffmpeg(cmd, "画面处理", timeout=900)

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
            if target_width and target_height:
                if mode == "stretch":
                    filters.append(f"scale={target_width}:{target_height}")
                elif mode == "crop":
                    filters.append(
                        f"scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
                        f"crop={target_width}:{target_height}"
                    )
                elif mode == "blur_background":
                    filters.append(
                        f"split[fg][bg];[bg]scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
                        f"crop={target_width}:{target_height},boxblur=20:2[bg];"
                        f"[fg]scale={target_width}:{target_height}:force_original_aspect_ratio=decrease[fg];"
                        f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
                    )
                else:
                    filters.append(
                        f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
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
                filters.append(f"select='not(eq(mod(n\\,{interval})\\,0))',setpts=N/FRAME_RATE/TB")

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
        preset: Optional[dict] = None
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
            output_path = os.path.join(ensure_project_dirs()["output_dir"], f"{base_name}_subtitled.mp4")

        # 构建字幕滤镜
        subtitle_filter = self._build_subtitle_filter(subtitle_path, preset)

        # 构建 ffmpeg 命令
        cmd = [
            self.ffmpeg_cmd,
            "-i", video_path,           # 输入视频
            "-vf", subtitle_filter,     # 字幕滤镜
            "-c:a", "copy",             # 音频直接复制
            "-c:v", "libx264",          # 视频编码
            "-preset", "medium",        # 编码预设
            "-crf", "23",              # 质量因子
            "-y",                       # 覆盖输出文件
            output_path
        ]

        logger.info(f"烧录字幕: {video_path} -> {output_path}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=600  # 10 分钟超时
            )

            if result.returncode != 0:
                raise RuntimeError(f"字幕烧录失败: {result.stderr}")

            logger.info(f"字幕烧录完成: {output_path}")
            return output_path

        except subprocess.TimeoutExpired:
            raise RuntimeError("字幕烧录超时")

    def _build_subtitle_filter(self, subtitle_path: str, preset: Optional[dict] = None) -> str:
        """构建字幕滤镜字符串"""
        # 转义路径中的特殊字符
        escaped_path = subtitle_path.replace("\\", "/").replace(":", "\\:")

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

    def merge_audio_video(
        self,
        video_path: str,
        audio_path: str,
        output_path: Optional[str] = None,
        mode: str = "replace",
        volume_ratio: float = 1.0
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
            output_path = os.path.join(ensure_project_dirs()["output_dir"], f"{base_name}_voiced.mp4")

        if mode == "replace":
            # 替换原声
            cmd = [
                self.ffmpeg_cmd,
                "-i", video_path,
                "-i", audio_path,
                "-c:v", "copy",
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-shortest",
                "-y",
                output_path
            ]
        elif mode == "mix":
            # 混合音频
            cmd = [
                self.ffmpeg_cmd,
                "-i", video_path,
                "-i", audio_path,
                "-filter_complex",
                f"[0:a]volume={volume_ratio}[a0];[1:a]volume=1.0[a1];[a0][a1]amix=inputs=2:duration=shortest[out]",
                "-map", "0:v:0",
                "-map", "[out]",
                "-c:v", "copy",
                "-y",
                output_path
            ]
        else:
            raise ValueError(f"不支持的合并模式: {mode}")

        logger.info(f"合并音视频: {video_path} + {audio_path} -> {output_path}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=600
            )

            if result.returncode != 0:
                raise RuntimeError(f"音视频合并失败: {result.stderr}")

            logger.info(f"音视频合并完成: {output_path}")
            return output_path

        except subprocess.TimeoutExpired:
            raise RuntimeError("音视频合并超时")

    def convert_format(
        self,
        input_path: str,
        output_format: str = "mp4",
        output_path: Optional[str] = None
    ) -> str:
        """转换视频格式"""
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"输入文件不存在: {input_path}")

        if output_path is None:
            base_name = os.path.splitext(os.path.basename(input_path))[0]
            output_path = os.path.join(ensure_project_dirs()["exports_dir"], f"{base_name}.{output_format}")

        cmd = [
            self.ffmpeg_cmd,
            "-i", input_path,
            "-c", "copy",
            "-y",
            output_path
        ]

        logger.info(f"转换格式: {input_path} -> {output_path}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=300
            )

            if result.returncode != 0:
                raise RuntimeError(f"格式转换失败: {result.stderr}")

            return output_path

        except subprocess.TimeoutExpired:
            raise RuntimeError("格式转换超时")

    def _run_ffmpeg(self, cmd: list[str], action_name: str, timeout: int = 600) -> str:
        """执行 ffmpeg 命令并统一处理错误"""
        output_path = cmd[-1]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )

            if result.returncode != 0:
                raise RuntimeError(f"{action_name}失败: {result.stderr}")

            logger.info(f"{action_name}完成: {output_path}")
            return output_path
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"{action_name}超时")

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
