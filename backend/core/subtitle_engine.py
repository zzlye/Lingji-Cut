# backend/core/subtitle_engine.py
# 字幕引擎 - 字幕解析、转换、生成

import os
import re
from typing import Optional, List
from ..utils import get_logger

# 日志记录器
logger = get_logger("subtitle")


class SubtitleEngine:
    """字幕处理引擎"""

    def parse_srt(self, srt_path: str) -> List[dict]:
        """
        解析 SRT 字幕文件
        返回字幕条目列表：[{index, start, end, text}, ...]
        """
        if not os.path.exists(srt_path):
            raise FileNotFoundError(f"SRT 文件不存在: {srt_path}")

        with open(srt_path, "r", encoding="utf-8") as f:
            content = f.read()

        # SRT 格式正则
        pattern = re.compile(
            r'(\d+)\n'
            r'(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\n'
            r'((?:.*\n)*?)',
            re.MULTILINE
        )

        entries = []
        for match in pattern.finditer(content):
            entries.append({
                "index": int(match.group(1)),
                "start": match.group(2),
                "end": match.group(3),
                "text": match.group(4).strip(),
            })

        logger.info(f"解析 SRT 完成: {len(entries)} 条字幕")
        return entries

    def parse_vtt(self, vtt_path: str) -> List[dict]:
        """
        解析 VTT 字幕文件
        返回字幕条目列表
        """
        if not os.path.exists(vtt_path):
            raise FileNotFoundError(f"VTT 文件不存在: {vtt_path}")

        with open(vtt_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 跳过 WEBVTT 头部
        if content.startswith("WEBVTT"):
            content = content.split("\n\n", 1)[-1]

        # VTT 格式正则
        pattern = re.compile(
            r'(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})\n'
            r'((?:.*\n)*?)',
            re.MULTILINE
        )

        entries = []
        for i, match in enumerate(pattern.finditer(content), 1):
            entries.append({
                "index": i,
                "start": match.group(1).replace(".", ","),
                "end": match.group(2).replace(".", ","),
                "text": match.group(3).strip(),
            })

        logger.info(f"解析 VTT 完成: {len(entries)} 条字幕")
        return entries

    def generate_ass(
        self,
        entries: List[dict],
        output_path: str,
        preset: Optional[dict] = None
    ) -> str:
        """
        生成 ASS 字幕文件
        返回输出文件路径
        """
        # 默认样式
        if preset is None:
            preset = {}

        font_name = preset.get("font_name", "Microsoft YaHei")
        font_size = preset.get("font_size", 48)
        font_color = preset.get("font_color", "&H00FFFFFF")
        outline_color = preset.get("outline_color", "&H00000000")
        outline_width = preset.get("outline_width", 2)
        shadow_color = preset.get("shadow_color", "&H80000000")
        position = preset.get("position", "bottom")
        margin_v = preset.get("margin_v", 30)

        # 位置映射，ASS 对齐值按数字键盘方向定义
        alignment_map = {
            "bottom_left": 1,   # 左下
            "bottom": 2,        # 底部居中
            "bottom_right": 3,  # 右下
            "middle_left": 4,   # 左中
            "center": 5,        # 居中
            "middle_right": 6,  # 右中
            "top_left": 7,      # 左上
            "top": 8,           # 顶部居中
            "top_right": 9,     # 右上
        }
        alignment = alignment_map.get(position, 2)

        # 生成 ASS 文件内容
        ass_content = f"""[Script Info]
Title: YouTube Video Subtitle
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.601
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},{font_color},&H000000FF,{outline_color},{shadow_color},0,0,0,0,100,100,0,0,1,{outline_width},1,{alignment},10,10,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

        # 添加字幕条目
        for entry in entries:
            start = self._srt_time_to_ass(entry["start"])
            end = self._srt_time_to_ass(entry["end"])
            text = entry["text"].replace("\n", "\\N")
            ass_content += f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n"

        # 写入文件
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(ass_content)

        logger.info(f"生成 ASS 字幕: {output_path}")
        return output_path

    def _srt_time_to_ass(self, srt_time: str) -> str:
        """将 SRT 时间格式转换为 ASS 格式"""
        # SRT: 00:01:23,456 -> ASS: 0:01:23.46
        srt_time = srt_time.replace(",", ".")
        parts = srt_time.split(":")
        hours = int(parts[0])
        minutes = parts[1]
        seconds_parts = parts[2].split(".")
        seconds = seconds_parts[0]
        millis = seconds_parts[1][:2] if len(seconds_parts) > 1 else "00"

        return f"{hours}:{minutes}:{seconds}.{millis}"

    def split_by_duration(
        self,
        entries: List[dict],
        max_duration: float = 5.0
    ) -> List[List[dict]]:
        """
        按时长分割字幕（用于配音分段）
        max_duration: 每段最大时长（秒）
        """
        segments = []
        current_segment = []
        current_duration = 0.0

        for entry in entries:
            # 计算字幕时长
            start_sec = self._time_to_seconds(entry["start"])
            end_sec = self._time_to_seconds(entry["end"])
            duration = end_sec - start_sec

            if current_duration + duration > max_duration and current_segment:
                segments.append(current_segment)
                current_segment = []
                current_duration = 0.0

            current_segment.append(entry)
            current_duration += duration

        if current_segment:
            segments.append(current_segment)

        logger.info(f"字幕分割完成: {len(segments)} 段")
        return segments

    def _time_to_seconds(self, time_str: str) -> float:
        """将时间字符串转换为秒数"""
        time_str = time_str.replace(",", ".")
        parts = time_str.split(":")
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])
        return hours * 3600 + minutes * 60 + seconds
