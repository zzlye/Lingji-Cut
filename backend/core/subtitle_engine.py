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
ASS_OVERRIDE_TAG_RE = re.compile(r"\{[^}]*\}")
LEADING_PUNCTUATION_RE = re.compile(r"^[，。、！？；：,.!?;:…]+")
PUNCTUATION_ONLY_RE = re.compile(r"^[\s，。、！？；：,.!?;:…]+$")
MEANINGFUL_CHAR_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
DISALLOWED_SUBTITLE_SEPARATOR_RE = re.compile(r"\.{3,}|…+|[，。、,.]")
CJK_DIGIT_SPACE_RE = re.compile(r"(?<=[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af])\s+(?=[0-9０-９])|(?<=[0-9０-９])\s+(?=[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af])")
CJK_CHAR_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
CJK_UNSAFE_SPLIT_PAIRS = {
    "产生", "发生", "反应", "生成", "无法", "别的", "女人", "男人",
    "字幕", "翻译", "原文", "识别", "视频", "导出", "保存", "下载",
    "处理", "设置", "文件", "路径", "时间", "开始", "结束", "素材",
}


def adjust_cjk_split_boundary(text: str, split_at: int, min_index: int = 1, max_index: Optional[int] = None) -> int:
    """避开常见中文词中间的硬切点，例如不要切成“产 / 生”"""
    value = str(text or "")
    if len(value) < 2:
        return split_at
    upper_bound = len(value) - 1 if max_index is None else min(max_index, len(value) - 1)
    lower_bound = max(1, min_index)
    safe_split = max(lower_bound, min(split_at, upper_bound))
    if not _is_unsafe_cjk_split_pair(value[safe_split - 1], value[safe_split]):
        return safe_split

    for candidate in range(safe_split - 1, lower_bound - 1, -1):
        if not _is_unsafe_cjk_split_pair(value[candidate - 1], value[candidate]):
            return candidate
    for candidate in range(safe_split + 1, upper_bound + 1):
        if not _is_unsafe_cjk_split_pair(value[candidate - 1], value[candidate]):
            return candidate
    return safe_split


def adjust_cjk_unit_boundary(units: list[str], split_at: int, min_index: int = 1, max_index: Optional[int] = None) -> int:
    """按文本单元回填字幕时避开中文词中间的切点"""
    if len(units) < 2:
        return split_at
    upper_bound = len(units) - 1 if max_index is None else min(max_index, len(units) - 1)
    lower_bound = max(1, min_index)
    safe_split = max(lower_bound, min(split_at, upper_bound))
    if not _is_unsafe_unit_boundary(units, safe_split):
        return safe_split

    for candidate in range(safe_split - 1, lower_bound - 1, -1):
        if not _is_unsafe_unit_boundary(units, candidate):
            return candidate
    for candidate in range(safe_split + 1, upper_bound + 1):
        if not _is_unsafe_unit_boundary(units, candidate):
            return candidate
    return safe_split


def _is_unsafe_unit_boundary(units: list[str], split_at: int) -> bool:
    """判断文本单元边界是否落在常见中文词中间"""
    left = str(units[split_at - 1] or "").strip()
    right = str(units[split_at] or "").strip()
    if not left or not right:
        return False
    return _is_unsafe_cjk_split_pair(left[-1], right[0])


def _is_unsafe_cjk_split_pair(left: str, right: str) -> bool:
    """判断两个相邻中文字符是否不应该被拆到两条字幕"""
    pair = f"{left or ''}{right or ''}"
    return bool(CJK_CHAR_RE.fullmatch(left or "") and CJK_CHAR_RE.fullmatch(right or "") and pair in CJK_UNSAFE_SPLIT_PAIRS)


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

    def parse_ass_content(self, content: str) -> List[dict]:
        """
        解析 ASS 字幕文本
        仅提取 Dialogue 行，兼容本项目生成的单行/双行字幕。
        """
        normalized = content.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
        entries: list[dict] = []
        for raw_line in normalized.split("\n"):
            line = raw_line.strip()
            if not line.startswith("Dialogue:"):
                continue
            parts = line.split(",", 9)
            if len(parts) < 10:
                continue

            text = self._normalize_ass_text(parts[9])
            if not text:
                continue

            entries.append({
                "index": len(entries) + 1,
                "start": self._ass_time_to_srt(parts[1]),
                "end": self._ass_time_to_srt(parts[2]),
                "text": text,
            })
        return entries

    def parse_ass(self, ass_path: str) -> List[dict]:
        """
        解析 ASS 字幕文件
        返回字幕条目列表：[{index, start, end, text}, ...]
        """
        if not os.path.exists(ass_path):
            raise FileNotFoundError(f"ASS 文件不存在: {ass_path}")

        with open(ass_path, "r", encoding="utf-8") as f:
            content = f.read()

        entries = self.parse_ass_content(content)
        logger.info(f"解析 ASS 完成: {len(entries)} 条字幕")
        return entries

    def entries_to_srt_content(self, entries: List[dict]) -> str:
        """将字幕条目转换为标准 SRT 文本"""
        blocks: list[str] = []
        for index, entry in enumerate(entries, 1):
            text = self.clean_subtitle_text_for_output(str(entry.get("text") or "").replace("\\N", "\n"))
            if not text or self.is_meaningless_subtitle_text(text):
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
            text = self._join_vtt_text_lines(text_lines) if is_vtt else "\n".join(text_lines).strip()
            if self.is_meaningless_subtitle_text(text):
                continue

            entries.append({
                "index": len(entries) + 1,
                "start": self.normalize_srt_time(start_raw),
                "end": self.normalize_srt_time(end_raw),
                "text": text,
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
            if not text or self.is_meaningless_subtitle_text(text):
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

    def _ass_time_to_srt(self, value: str) -> str:
        """把 ASS 时间码转换为 SRT 时间码"""
        value = value.strip()
        parts = value.split(":")
        if len(parts) != 3:
            return "00:00:00,000"
        try:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds_and_centis = parts[2].split(".", 1)
            seconds = int(seconds_and_centis[0])
            centis = int(seconds_and_centis[1]) if len(seconds_and_centis) > 1 else 0
        except ValueError:
            return "00:00:00,000"

        milliseconds = ((hours * 3600 + minutes * 60 + seconds) * 1000) + centis * 10
        hours = milliseconds // 3600000
        minutes = (milliseconds % 3600000) // 60000
        seconds = (milliseconds % 60000) // 1000
        millis = milliseconds % 1000
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    def _normalize_ass_text(self, text: str) -> str:
        """清理 ASS 文本中的样式标记，恢复成可编辑的普通字幕"""
        normalized = text.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
        normalized = ASS_OVERRIDE_TAG_RE.sub("", normalized)
        normalized = html.unescape(normalized)
        normalized = "\n".join(line.strip() for line in normalized.splitlines())
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized.strip()

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
        font_size = int(preset.get("font_size") or 48)
        secondary_font_size = int(preset.get("secondary_font_size") or max(18, round(font_size * 0.88)))
        line_mode = str(preset.get("line_mode") or "single").lower()
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
        wrap_style = 2 if line_mode == "single" else 0
        ass_content = f"""[Script Info]
Title: YouTube Video Subtitle
ScriptType: v4.00+
WrapStyle: {wrap_style}
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.601
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},{font_color},{secondary_color},{outline_color},{shadow_color},0,0,0,0,100,100,0,0,1,{outline_width},1,{alignment},10,10,{margin_v},1
Style: Secondary,{font_name},{secondary_font_size},{secondary_color},{secondary_color},{outline_color},{shadow_color},0,0,0,0,100,100,0,0,1,{outline_width},1,{alignment},10,10,{margin_v},1

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

    def normalize_entries_for_display(self, entries: List[dict], preset: Optional[dict] = None) -> List[dict]:
        """把长字幕拆成短字幕条目，避免单条字幕显示两行并改善时间轴贴合度"""
        preset = preset or {}
        if str(preset.get("line_mode") or "single").lower() == "double":
            return self._normalize_double_line_entries(entries)

        max_chars = self._max_display_chars(preset)
        normalized_entries: list[dict] = []
        for entry in entries:
            text = self.clean_subtitle_text_for_output(str(entry.get("text") or ""))
            text = WHITESPACE_RE.sub(" ", text.replace("\\N", " ").replace("\n", " ")).strip()
            if not text or self.is_meaningless_subtitle_text(text):
                continue
            parts = self._split_subtitle_text(text, max_chars)
            if not parts:
                continue
            start_ms = self._time_to_milliseconds(str(entry.get("start") or "00:00:00,000"))
            end_ms = self._time_to_milliseconds(str(entry.get("end") or "00:00:00,000"))
            if end_ms <= start_ms:
                end_ms = start_ms + 1000
            duration = max(1, end_ms - start_ms)
            weights = [max(1, len(part)) for part in parts]
            total_weight = max(1, sum(weights))
            elapsed_weight = 0
            for index, part in enumerate(parts):
                next_weight = elapsed_weight + weights[index]
                part_start = start_ms + int(duration * elapsed_weight / total_weight)
                part_end = start_ms + int(duration * next_weight / total_weight)
                elapsed_weight = next_weight
                normalized_entries.append({
                    **entry,
                    "index": len(normalized_entries) + 1,
                    "start": self._milliseconds_to_srt_time(part_start),
                    "end": self._milliseconds_to_srt_time(max(part_start + 1, part_end)),
                    "text": part,
                })
        return normalized_entries

    def _normalize_double_line_entries(self, entries: List[dict]) -> List[dict]:
        """双行模式保留原时间段，只清理每行空白，避免被单行拆分逻辑打散"""
        normalized_entries: list[dict] = []
        for entry in entries:
            lines = self._dialogue_lines(str(entry.get("text") or ""))
            if not lines:
                continue
            normalized_entries.append({
                **entry,
                "index": len(normalized_entries) + 1,
                "text": "\n".join(lines),
            })
        return normalized_entries

    def _format_ass_dialogue_text(self, text: str, preset: dict) -> str:
        """格式化 ASS 单条字幕，单行模式不再写入换行符"""
        lines = self._dialogue_lines(text)
        if not lines:
            return ""
        if str(preset.get("line_mode") or "single").lower() == "single":
            return " ".join(lines)

        primary, secondary = self._double_line_text(lines, self._max_display_chars(preset))
        if not secondary:
            return primary
        return f"{primary}\\N{{\\rSecondary}}{secondary}"

    def _dialogue_lines(self, text: str) -> list[str]:
        """按显式换行拆字幕行，并清理每行内部空白"""
        return [
            self.clean_subtitle_text_for_output(WHITESPACE_RE.sub(" ", line))
            for line in str(text or "").replace("\\N", "\n").splitlines()
            if self.clean_subtitle_text_for_output(WHITESPACE_RE.sub(" ", line))
            and not self.is_meaningless_subtitle_text(self.clean_subtitle_text_for_output(WHITESPACE_RE.sub(" ", line)))
        ]

    def _double_line_text(self, lines: list[str], max_chars: int) -> tuple[str, str]:
        """生成双行字幕文本：显式两行优先，否则把长句切成上下两行"""
        if len(lines) >= 2:
            return lines[0], " ".join(lines[1:])

        parts = self._split_subtitle_text(lines[0], max_chars)
        if len(parts) == 1:
            return parts[0], ""
        return parts[0], " ".join(parts[1:])

    def _max_display_chars(self, preset: dict) -> int:
        """根据字号估算单行可读字符数"""
        font_size = int(preset.get("font_size") or 48)
        return max(12, min(30, int(1150 / max(font_size, 1))))

    def _split_subtitle_text(self, text: str, max_chars: int) -> list[str]:
        """按显示宽度拆字幕文本，优先在标点或空格处断开"""
        cleaned = WHITESPACE_RE.sub(" ", self.clean_subtitle_text_for_output(text)).strip()
        if not cleaned:
            return []
        if self.is_meaningless_subtitle_text(cleaned):
            return []
        if len(cleaned) <= max_chars:
            return [cleaned]

        lines: list[str] = []
        remaining = cleaned
        break_chars = "，。、！？；：,.!?;:… "
        while len(remaining) > max_chars:
            split_at = self._preferred_subtitle_split_at(remaining, max_chars, break_chars)
            line = remaining[:split_at].strip()
            if line:
                lines.append(line)
            remaining = remaining[split_at:].strip()
        if remaining:
            lines.append(remaining)
        return self._merge_invalid_subtitle_parts(lines)

    def _preferred_subtitle_split_at(self, text: str, max_chars: int, break_chars: str) -> int:
        """优先在靠近目标宽度的标点或空格处分句，减少硬切导致的断句问题"""
        search_start = max(1, max_chars // 2)
        for index in range(max_chars, search_start - 1, -1):
            char = text[index - 1]
            if char not in break_chars:
                continue
            return index - 1 if char == " " else index
        return adjust_cjk_split_boundary(text, max_chars, min_index=search_start, max_index=max_chars)

    def _merge_invalid_subtitle_parts(self, parts: list[str]) -> list[str]:
        """合并纯标点碎片和单字尾巴，避免生成无意义字幕条目"""
        merged: list[str] = []
        for raw_part in parts:
            part = raw_part.strip()
            if not part:
                continue

            leading = LEADING_PUNCTUATION_RE.match(part)
            if merged and leading:
                punctuation = leading.group(0)
                merged[-1] = f"{merged[-1]}{punctuation}"
                part = part[len(punctuation):].strip()
                if not part:
                    continue

            if merged and self._should_merge_fragment(part):
                merged[-1] = self._join_subtitle_fragments(merged[-1], part)
                continue
            merged.append(part)

        if len(merged) >= 2 and self._should_merge_fragment(merged[0]):
            merged[1] = self._join_subtitle_fragments(merged[0], merged[1])
            merged = merged[1:]
        return [part for part in merged if not self.is_meaningless_subtitle_text(part)]

    def is_meaningless_subtitle_text(self, text: str) -> bool:
        """判断字幕是否只有逗号、句号、省略号等无信息标点"""
        return bool(PUNCTUATION_ONLY_RE.fullmatch(str(text or "").strip()))

    def clean_subtitle_text_for_output(self, text: str) -> str:
        """输出字幕前移除用户不需要的逗号、句号和省略号"""
        normalized = self._normalize_disallowed_punctuation(str(text or "").replace("\\N", "\n"))
        lines = [
            self._normalize_cjk_digit_spacing(WHITESPACE_RE.sub(" ", line).strip())
            for line in normalized.splitlines()
            if self._normalize_cjk_digit_spacing(WHITESPACE_RE.sub(" ", line).strip())
        ]
        return "\n".join(lines).strip()

    def _normalize_disallowed_punctuation(self, text: str) -> str:
        """把禁用标点转成分隔空格，避免句子被硬删后粘在一起"""
        return DISALLOWED_SUBTITLE_SEPARATOR_RE.sub(" ", text)

    def _normalize_cjk_digit_spacing(self, text: str) -> str:
        """去掉中文、日文、韩文和数字之间误插入的空格"""
        return CJK_DIGIT_SPACE_RE.sub("", text)

    def _should_merge_fragment(self, text: str) -> bool:
        """判断碎片是否需要并回前后文，重点处理纯标点和单个有效字"""
        cleaned = str(text or "").strip()
        if not cleaned:
            return True
        if PUNCTUATION_ONLY_RE.fullmatch(cleaned):
            return True
        return self._meaningful_char_count(cleaned) <= 1

    def _meaningful_char_count(self, text: str) -> int:
        """统计真正承载信息的字符数，用来识别无意义碎片"""
        return len(MEANINGFUL_CHAR_RE.findall(str(text or "")))

    def _join_subtitle_fragments(self, left: str, right: str) -> str:
        """把被拆开的字幕碎片重新拼回一句，英文单词之间保留必要空格"""
        left = str(left or "").strip()
        right = str(right or "").strip()
        if not left:
            return right
        if not right:
            return left
        separator = " " if self._needs_word_separator(left[-1], right[0]) else ""
        return f"{left}{separator}{right}".strip()

    def _milliseconds_to_srt_time(self, milliseconds: int) -> str:
        """把毫秒转换成 SRT 时间码"""
        milliseconds = max(0, int(milliseconds))
        hours = milliseconds // 3600000
        minutes = (milliseconds % 3600000) // 60000
        seconds = (milliseconds % 60000) // 1000
        millis = milliseconds % 1000
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

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
