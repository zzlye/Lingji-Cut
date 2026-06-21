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
LEADING_CJK_PHRASE_RE = re.compile(r"^([\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]{1,4})\s+(.{2,})$")
NEW_CJK_CLAUSE_PREFIXES = ("我", "你", "他", "她", "它", "我们", "你们", "他们", "她们", "它们", "有人", "这", "那")
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
        for index, entry in enumerate(self.dedupe_entries_by_text(entries), 1):
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
        preset: Optional[dict] = None,
        video_size: Optional[tuple[int, int]] = None,
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
        play_res_x, play_res_y = self._ass_play_resolution(preset, video_size)

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
PlayResX: {play_res_x}
PlayResY: {play_res_y}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},{font_color},{secondary_color},{outline_color},{shadow_color},0,0,0,0,100,100,0,0,1,{outline_width},1,{alignment},10,10,{margin_v},1
Style: Secondary,{font_name},{secondary_font_size},{secondary_color},{secondary_color},{outline_color},{shadow_color},0,0,0,0,100,100,0,0,1,{outline_width},1,{alignment},10,10,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

        # 添加字幕条目，生成硬字幕前再次去重，避免重复文本进入最终画面。
        for entry in self.dedupe_entries_by_text(entries):
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
        """清理字幕显示条目，避免烧录阶段二次硬切正常字幕"""
        preset = preset or {}
        if str(preset.get("line_mode") or "single").lower() == "double":
            return self.dedupe_entries_by_text(self._normalize_double_line_entries(entries))

        normalized_entries: list[dict] = []
        for entry in self._merge_adjacent_display_fragments(entries):
            text = self.clean_subtitle_text_for_output(str(entry.get("text") or ""))
            text = WHITESPACE_RE.sub(" ", text.replace("\\N", " ").replace("\n", " ")).strip()
            if not text or self.is_meaningless_subtitle_text(text):
                continue
            start_ms = self._time_to_milliseconds(str(entry.get("start") or "00:00:00,000"))
            end_ms = self._time_to_milliseconds(str(entry.get("end") or "00:00:00,000"))
            if end_ms <= start_ms:
                end_ms = start_ms + 1000
            end_ms = self._cap_short_display_end_ms(start_ms, end_ms, text)
            normalized_entries.append({
                **entry,
                "index": len(normalized_entries) + 1,
                "start": self._milliseconds_to_srt_time(start_ms),
                "end": self._milliseconds_to_srt_time(max(start_ms + 1, end_ms)),
                "text": text,
            })
        return self.dedupe_entries_by_text(normalized_entries)

    def dedupe_entries_by_text(self, entries: List[dict]) -> List[dict]:
        """去掉时间上重叠或紧邻的重复字幕，保留正常复读台词"""
        deduped: list[dict] = []
        last_by_key: dict[str, dict] = {}
        for entry in entries:
            cleaned_text = self.clean_subtitle_text_for_output(str(entry.get("text") or ""))
            key = self._dedupe_text_key(cleaned_text)
            if not key:
                continue
            previous = last_by_key.get(key)
            if previous and self._is_timing_duplicate(previous, entry):
                continue
            next_entry = dict(entry)
            next_entry["index"] = len(deduped) + 1
            next_entry["text"] = cleaned_text
            deduped.append(next_entry)
            last_by_key[key] = next_entry
        return deduped

    def duplicate_text_count(self, entries: List[dict]) -> int:
        """统计时间上重叠或紧邻的重复条目，正常复读不算错误"""
        last_by_key: dict[str, dict] = {}
        duplicate_count = 0
        for entry in entries:
            key = self._dedupe_text_key(str(entry.get("text") or ""))
            if not key:
                continue
            previous = last_by_key.get(key)
            if previous and self._is_timing_duplicate(previous, entry):
                duplicate_count += 1
                continue
            last_by_key[key] = entry
        return duplicate_count

    def _is_timing_duplicate(self, previous: dict, current: dict) -> bool:
        """判断同文本字幕是否属于滚动字幕残留，而不是后面正常重复说了一遍"""
        previous_start = self._time_to_milliseconds(str(previous.get("start") or "00:00:00,000"))
        previous_end = self._time_to_milliseconds(str(previous.get("end") or previous.get("start") or "00:00:00,000"))
        current_start = self._time_to_milliseconds(str(current.get("start") or "00:00:00,000"))
        current_end = self._time_to_milliseconds(str(current.get("end") or current.get("start") or "00:00:00,000"))
        if previous_start == current_start and previous_end == current_end:
            return True
        # YouTube 滚动字幕或模型重试残留通常会重叠，或在极短间隔内重复同一文本。
        return current_start <= previous_end + 300

    def _dedupe_text_key(self, text: str) -> str:
        """生成重复判断 key，双语字幕优先按第一行主字幕判断重复"""
        normalized = self.clean_subtitle_text_for_output(str(text or "").replace("\\N", "\n"))
        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        normalized = lines[0] if lines else ""
        normalized = WHITESPACE_RE.sub(" ", normalized).strip().casefold()
        return normalized

    def _ass_play_resolution(self, preset: dict, video_size: Optional[tuple[int, int]]) -> tuple[int, int]:
        """确定 ASS 渲染画布，优先跟随实际视频分辨率，避免字幕位置在不同视频上漂移"""
        width = int(video_size[0]) if video_size and video_size[0] else int(preset.get("play_res_x") or preset.get("video_width") or 1920)
        height = int(video_size[1]) if video_size and video_size[1] else int(preset.get("play_res_y") or preset.get("video_height") or 1080)
        return max(320, width), max(180, height)

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

    def _merge_adjacent_display_fragments(self, entries: List[dict]) -> List[dict]:
        """合并相邻字幕里的明显断词，避免“最好的 / 基地”这种机械切分进入硬字幕"""
        merged_entries: list[dict] = []
        for raw_entry in entries:
            entry = dict(raw_entry)
            text = self.clean_subtitle_text_for_output(str(entry.get("text") or ""))
            text = WHITESPACE_RE.sub(" ", text.replace("\\N", " ").replace("\n", " ")).strip()
            if not text or self.is_meaningless_subtitle_text(text):
                continue
            entry["text"] = text

            if merged_entries and self._should_merge_whole_entry(merged_entries[-1], entry):
                merged_entries[-1]["text"] = self._join_subtitle_fragments(str(merged_entries[-1].get("text") or ""), text)
                merged_entries[-1]["end"] = entry.get("end") or merged_entries[-1].get("end")
                continue

            if merged_entries:
                adjusted = self._pull_leading_phrase_to_previous(merged_entries[-1], entry)
                if adjusted is None:
                    continue
                entry = adjusted

            merged_entries.append(entry)

        for index, entry in enumerate(merged_entries, 1):
            entry["index"] = index
        return merged_entries

    def _should_merge_whole_entry(self, previous: dict, current: dict) -> bool:
        """短字幕整体承接上一条时直接并回上一条"""
        previous_text = str(previous.get("text") or "").strip()
        current_text = str(current.get("text") or "").strip()
        if not previous_text or not current_text:
            return False
        if current_text.startswith(NEW_CJK_CLAUSE_PREFIXES):
            return False
        if not self._previous_text_looks_incomplete(previous_text):
            return False
        if self._meaningful_char_count(current_text) > 4:
            return False
        previous_start = self._time_to_milliseconds(str(previous.get("start") or "00:00:00,000"))
        current_end = self._time_to_milliseconds(str(current.get("end") or "00:00:00,000"))
        return 0 < current_end - previous_start <= 6000

    def _pull_leading_phrase_to_previous(self, previous: dict, current: dict) -> Optional[dict]:
        """把当前条开头的短中文词并回上一条，并按比例移动时间边界"""
        previous_text = str(previous.get("text") or "").strip()
        current_text = str(current.get("text") or "").strip()
        match = LEADING_CJK_PHRASE_RE.match(current_text)
        if not previous_text or not match:
            return current
        leading, remainder = match.group(1).strip(), match.group(2).strip()
        if not leading or not remainder or not self._previous_text_looks_incomplete(previous_text):
            return current

        start_ms = self._time_to_milliseconds(str(current.get("start") or "00:00:00,000"))
        end_ms = self._time_to_milliseconds(str(current.get("end") or "00:00:00,000"))
        duration = end_ms - start_ms
        if duration <= 300:
            return current

        leading_weight = max(1, self._meaningful_char_count(leading))
        total_weight = leading_weight + max(1, self._meaningful_char_count(remainder))
        split_ms = start_ms + max(180, min(duration - 120, int(duration * leading_weight / total_weight)))
        if split_ms <= start_ms or split_ms >= end_ms:
            return current

        previous["text"] = self._join_subtitle_fragments(previous_text, leading)
        previous["end"] = self._milliseconds_to_srt_time(split_ms)
        next_entry = dict(current)
        next_entry["start"] = self._milliseconds_to_srt_time(split_ms)
        next_entry["text"] = remainder
        return next_entry

    def _previous_text_looks_incomplete(self, text: str) -> bool:
        """判断上一条字幕是否像未说完整，主要用于合并被 ASR 切开的中文短词"""
        value = str(text or "").strip()
        if not value:
            return False
        incomplete_suffixes = (
            "的", "被", "把", "让", "将", "对", "从", "给", "为", "用", "在",
            "一个", "这个", "那个", "这些", "那些", "一种", "一些", "一座", "一栋",
            "最好的", "最快的", "最重要的", "精美的", "高效的", "需要的", "想要的",
        )
        return value.endswith(incomplete_suffixes)

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
        """生成双行字幕文本：显式两行优先，否则把长句均衡切成上下两行"""
        if len(lines) >= 2:
            return lines[0], " ".join(lines[1:])

        text = lines[0].strip() if lines else ""
        if not text:
            return "", ""
        if len(text) <= max_chars:
            return text, ""
        # 均衡切分：切点取句子中点附近，避免第二行只剩一两个字
        split_at = self._balanced_split_at(text, max_chars)
        first = text[:split_at].strip()
        second = text[split_at:].strip()
        if first and second:
            return first, second
        # 兜底：切点异常时退回原逐段切分
        parts = self._split_subtitle_text(text, max_chars)
        if len(parts) == 1:
            return parts[0], ""
        return parts[0], " ".join(parts[1:])

    def _balanced_split_at(self, text: str, max_chars: int) -> int:
        """为双行字幕找均衡切点，让上下两行字数尽量接近，避免第二行只剩一两个字"""
        length = len(text)
        # 目标切在中点，同时保证每行都不超过单行上限
        target = (length + 1) // 2
        target = max(target, length - max_chars)
        target = min(target, max_chars)
        if target < 1:
            target = max(1, min(max_chars, length // 2))
        break_chars = "，。、！？；：,.!?;:… "
        window = max(2, min(6, max_chars // 3))
        # 在目标点附近由近及远找标点或空格断开
        for offset in range(0, window + 1):
            for idx in (target + offset, target - offset):
                if 1 <= idx < length and text[idx - 1] in break_chars:
                    return idx - 1 if text[idx - 1] == " " else idx
        # 没有自然断点就退回 CJK 安全边界，避免把词切坏
        lower = max(1, target - window)
        upper = min(length - 1, target + window)
        return adjust_cjk_split_boundary(text, target, min_index=lower, max_index=upper)

    def _max_display_chars(self, preset: dict) -> int:
        """根据字号估算单行可读字符数"""
        font_size = int(preset.get("font_size") or 48)
        return max(12, min(30, int(1150 / max(font_size, 1))))

    def _cap_short_display_end_ms(self, start_ms: int, end_ms: int, text: str) -> int:
        """短字幕识别段过长时收紧显示时间，避免话已结束字幕还挂在画面上"""
        duration = max(1, end_ms - start_ms)
        meaningful_count = self._meaningful_char_count(text)
        if meaningful_count <= 0:
            return end_ms
        if meaningful_count > 18:
            return end_ms
        cap_ms = max(1200, min(4200, 900 + meaningful_count * 150))
        if duration <= cap_ms + 500:
            return end_ms
        return max(start_ms + 700, min(end_ms, start_ms + cap_ms))

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
        forward_split = self._nearby_forward_subtitle_split_at(text, max_chars, break_chars)
        if forward_split:
            return forward_split
        return adjust_cjk_split_boundary(text, max_chars, min_index=search_start, max_index=max_chars)

    def _nearby_forward_subtitle_split_at(self, text: str, max_chars: int, break_chars: str) -> Optional[int]:
        """向后少量寻找自然断点，避免把“基地”这类短核心词顶到下一行开头"""
        upper_bound = min(len(text), max_chars + max(2, min(6, max_chars // 3)))
        for index in range(max_chars + 1, upper_bound + 1):
            char = text[index - 1]
            if char not in break_chars:
                continue
            return index - 1 if char == " " else index
        return None

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
