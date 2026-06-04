# backend/core/subtitle_engine.py
# 字幕引擎 - 字幕解析、转换、生成

import os
import re
import html
from typing import Optional, List
from ..utils import get_logger

# 日志记录器
logger = get_logger("subtitle")

# YouTube 自动字幕 VTT 会包含内联时间戳和样式标签，烧录前必须清理。
INLINE_VTT_TIMESTAMP_RE = re.compile(r"<\d{2}:\d{2}:\d{2}\.\d{3}>")
VTT_TAG_RE = re.compile(r"</?[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
MIN_VTT_CUE_DURATION_MS = 250


class SubtitleEngine:
    """字幕处理引擎"""

    def parse_srt_content(self, content: str) -> List[dict]:
        """
        解析 SRT 字幕文本
        返回字幕条目列表：[{index, start, end, text}, ...]
        """
        return self._parse_timed_text(content, is_vtt=False)

    def parse_vtt_content(self, content: str) -> List[dict]:
        """
        解析 VTT 字幕文本
        返回字幕条目列表：[{index, start, end, text}, ...]
        """
        return self._parse_timed_text(content, is_vtt=True)

    def parse_srt(self, srt_path: str) -> List[dict]:
        """
        解析 SRT 字幕文件
        返回字幕条目列表：[{index, start, end, text}, ...]
        """
        if not os.path.exists(srt_path):
            raise FileNotFoundError(f"SRT 文件不存在: {srt_path}")

        with open(srt_path, "r", encoding="utf-8") as f:
            content = f.read()

        entries = self.parse_srt_content(content)
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
        entries = self.parse_vtt_content(content)
        logger.info(f"解析 VTT 完成: {len(entries)} 条字幕")
        return entries

    def entries_to_srt_content(self, entries: List[dict]) -> str:
        """将字幕条目转换为标准 SRT 文本"""
        blocks: list[str] = []
        for index, entry in enumerate(entries, 1):
            text = str(entry.get("text") or "").replace("\\N", "\n").strip()
            if not text:
                continue
            start = self.normalize_srt_time(str(entry.get("start") or "00:00:00,000"))
            end = self.normalize_srt_time(str(entry.get("end") or start))
            blocks.append(f"{len(blocks) + 1}\n{start} --> {end}\n{text}")
        return "\n\n".join(blocks) + ("\n" if blocks else "")

    def save_srt(self, entries: List[dict], output_path: str) -> str:
        """保存 SRT 字幕文件"""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(self.entries_to_srt_content(entries))
        logger.info(f"保存 SRT 字幕: {output_path}")
        return output_path

    def normalize_srt_time(self, value: str) -> str:
        """规范化时间码为 00:00:00,000 格式"""
        milliseconds = self._time_to_milliseconds(value)
        hours = milliseconds // 3600000
        minutes = (milliseconds % 3600000) // 60000
        seconds = (milliseconds % 60000) // 1000
        millis = milliseconds % 1000
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    def _parse_timed_text(self, content: str, is_vtt: bool) -> List[dict]:
        """解析 SRT/VTT 文本块，兼容 cue 标识和 VTT 附加设置"""
        normalized = content.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if is_vtt and normalized.startswith("WEBVTT"):
            normalized = normalized.split("\n", 1)[1] if "\n" in normalized else ""
        if is_vtt:
            # YouTube VTT 有时会在时间轴后放一个空格行，先移除它再按空行切块。
            normalized = re.sub(r"(-->\s*[^\n]*\n)[ \t]+\n", r"\1", normalized)

        entries: list[dict] = []
        blocks = re.split(r"\n\s*\n", normalized)
        for block in blocks:
            lines = [line.rstrip() for line in block.split("\n") if line.strip()]
            if not lines:
                continue
            if is_vtt and lines[0].strip().upper().startswith(("NOTE", "STYLE", "REGION")):
                continue

            timing_index = next((i for i, line in enumerate(lines) if "-->" in line), -1)
            if timing_index < 0:
                continue

            timing = lines[timing_index]
            start_raw, end_raw = [part.strip() for part in timing.split("-->", 1)]
            end_raw = end_raw.split()[0]
            text_lines = self._normalize_vtt_text_lines(lines[timing_index + 1:]) if is_vtt else lines[timing_index + 1:]
            if not text_lines:
                continue

            entries.append({
                "index": len(entries) + 1,
                "start": self.normalize_srt_time(start_raw),
                "end": self.normalize_srt_time(end_raw),
                "text": self._join_vtt_text_lines(text_lines) if is_vtt else "\n".join(text_lines).strip(),
            })
        return self._dedupe_vtt_rolling_entries(entries) if is_vtt else entries

    def _normalize_vtt_text_lines(self, lines: list[str]) -> list[str]:
        """清理 VTT 文本行，移除 YouTube 内联时间戳、样式标签和多余空白"""
        normalized: list[str] = []
        for line in lines:
            cleaned = html.unescape(line)
            cleaned = INLINE_VTT_TIMESTAMP_RE.sub("", cleaned)
            cleaned = VTT_TAG_RE.sub("", cleaned)
            cleaned = WHITESPACE_RE.sub(" ", cleaned).strip()
            if cleaned:
                normalized.append(cleaned)
        return normalized

    def _join_vtt_text_lines(self, lines: list[str]) -> str:
        """合并 VTT 多行文本，中文/日文不断词，英文数字之间保留空格"""
        text = ""
        for line in lines:
            if not text:
                text = line
                continue
            separator = " " if self._needs_word_separator(text[-1], line[0]) else ""
            text = f"{text}{separator}{line}"
        return text.strip()

    def _needs_word_separator(self, left: str, right: str) -> bool:
        """判断两段字幕拼接时是否需要空格"""
        if not left or not right:
            return False
        if re.match(r"[\w\]]", left, re.ASCII) and re.match(r"[\w\[]", right, re.ASCII):
            return True
        return False

    def _dedupe_vtt_rolling_entries(self, entries: list[dict]) -> list[dict]:
        """去除 YouTube 滚动字幕的短碎片和重复前缀，只保留新增内容"""
        deduped: list[dict] = []
        previous_text = ""
        for entry in entries:
            start_ms = self._time_to_milliseconds(str(entry.get("start") or "00:00:00,000"))
            end_ms = self._time_to_milliseconds(str(entry.get("end") or "00:00:00,000"))
            text = str(entry.get("text") or "").strip()
            if not text or end_ms - start_ms < MIN_VTT_CUE_DURATION_MS:
                continue

            text = self._remove_rolling_prefix(text, previous_text)
            if not text:
                continue

            next_entry = dict(entry)
            next_entry["index"] = len(deduped) + 1
            next_entry["text"] = text
            deduped.append(next_entry)
            previous_text = text
        return deduped

    def _remove_rolling_prefix(self, text: str, previous_text: str) -> str:
        """当前 VTT cue 以前一条完整或尾部文本开头时，只保留新增部分"""
        if not previous_text:
            return text
        if text == previous_text or previous_text.startswith(text):
            return ""
        if text.startswith(previous_text):
            return text[len(previous_text):].strip()
        max_overlap = min(len(previous_text), len(text))
        for size in range(max_overlap, 2, -1):
            if text.startswith(previous_text[-size:]):
                return text[size:].strip()
        return text

    def _time_to_milliseconds(self, value: str) -> int:
        """把 SRT/VTT 时间码转换为毫秒"""
        value = value.strip().replace(",", ".")
        parts = value.split(":")
        try:
            if len(parts) == 3:
                hours = int(parts[0])
                minutes = int(parts[1])
                seconds = float(parts[2])
            elif len(parts) == 2:
                hours = 0
                minutes = int(parts[0])
                seconds = float(parts[1])
            else:
                return 0
        except ValueError:
            return 0
        return max(0, int(round((hours * 3600 + minutes * 60 + seconds) * 1000)))

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
        font_color = self._color_to_ass(preset.get("font_color", "&H00FFFFFF"))
        secondary_color = self._color_to_ass(preset.get("secondary_color", "&H000000FF"))
        outline_color = self._color_to_ass(preset.get("outline_color", "&H00000000"))
        outline_width = preset.get("outline_width", 2)
        shadow_color = self._color_to_ass(preset.get("shadow_color", "&H80000000"), default_alpha="80")
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
Style: Default,{font_name},{font_size},{font_color},{secondary_color},{outline_color},{shadow_color},0,0,0,0,100,100,0,0,1,{outline_width},1,{alignment},10,10,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

        # 添加字幕条目
        for entry in entries:
            start = self._srt_time_to_ass(entry["start"])
            end = self._srt_time_to_ass(entry["end"])
            text = self._format_ass_dialogue_text(str(entry.get("text") or ""), preset)
            ass_content += f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n"

        # 写入文件
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(ass_content)

        logger.info(f"生成 ASS 字幕: {output_path}")
        return output_path

    def _format_ass_dialogue_text(self, text: str, preset: dict) -> str:
        """格式化 ASS 单条字幕，自动清理空白并给长句换行"""
        normalized = WHITESPACE_RE.sub(" ", text.replace("\\N", " ").replace("\n", " ")).strip()
        if not normalized:
            return ""
        font_size = int(preset.get("font_size") or 48)
        max_chars = max(18, min(34, int(1400 / max(font_size, 1))))
        return "\\N".join(self._wrap_subtitle_text(normalized, max_chars))

    def _wrap_subtitle_text(self, text: str, max_chars: int) -> list[str]:
        """按显示宽度拆字幕行，优先在标点或空格处断行"""
        if len(text) <= max_chars:
            return [text]

        lines: list[str] = []
        remaining = text
        break_chars = "，。、！？；,.!?; "
        while len(remaining) > max_chars:
            split_at = max((remaining.rfind(char, 0, max_chars + 1) for char in break_chars), default=-1)
            if split_at < max_chars // 2:
                split_at = max_chars
            line = remaining[:split_at].strip(" ，。、！？；,.!?;")
            if line:
                lines.append(line)
            remaining = remaining[split_at:].strip()
        if remaining:
            lines.append(remaining)
        return lines

    def _srt_time_to_ass(self, srt_time: str) -> str:
        """将 SRT 时间格式转换为 ASS 格式"""
        # SRT: 00:01:23,456 -> ASS: 0:01:23.46
        srt_time = self.normalize_srt_time(srt_time).replace(",", ".")
        parts = srt_time.split(":")
        hours = int(parts[0])
        minutes = parts[1]
        seconds_parts = parts[2].split(".")
        seconds = seconds_parts[0]
        millis = seconds_parts[1][:2] if len(seconds_parts) > 1 else "00"

        return f"{hours}:{minutes}:{seconds}.{millis}"

    def _color_to_ass(self, color: str, default_alpha: str = "00") -> str:
        """将十六进制颜色转换为 ASS 使用的 AABBGGRR 格式"""
        if not color:
            return f"&H{default_alpha}FFFFFF"
        if color.startswith("&H"):
            return color

        raw = color.lstrip("#")
        if len(raw) == 8:
            alpha = raw[0:2]
            rgb = raw[2:8]
        elif len(raw) == 6:
            alpha = default_alpha
            rgb = raw
        else:
            return f"&H{default_alpha}FFFFFF"

        red, green, blue = rgb[0:2], rgb[2:4], rgb[4:6]
        return f"&H{alpha}{blue}{green}{red}"

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
