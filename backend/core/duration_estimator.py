# backend/core/duration_estimator.py
# 字幕朗读时长预估模块 - 参考 VideoLingo 的 estimate_duration.py
# 根据文本的音节数和标点停顿，预估 TTS 朗读所需时间

import re
from typing import Optional

from ..utils import get_logger

# 日志记录器
logger = get_logger("duration_estimator")

# 各语言每音节平均时长（秒）
LANGUAGE_DURATION_PARAMS = {
    "zh": 0.21,    # 中文：每个汉字约 0.21 秒
    "ja": 0.21,    # 日文
    "en": 0.225,   # 英文
    "fr": 0.22,    # 法文
    "es": 0.22,    # 西班牙文
    "ko": 0.21,    # 韩文
    "default": 0.22,
}

# 中文标点停顿时长（秒）
PUNCTUATION_PAUSE_MID = 0.1    # 逗号、分号等中间标点
PUNCTUATION_PAUSE_END = 0.15   # 句号、问号等结尾标点
PUNCTUATION_PAUSE_SPACE = 0.08 # 空格停顿

# 中文正则
CJK_CHAR_RE = re.compile(r"[一-鿿]")
JAPANESE_CHAR_RE = re.compile(r"[぀-ゟ゠-ヿ一-鿿]")
KOREAN_CHAR_RE = re.compile(r"[가-힯]")
MID_PUNCTUATION_RE = re.compile(r"[，；：,;、]+")
END_PUNCTUATION_RE = re.compile(r"[。！？.!?]+")

# 英文元音
ENGLISH_VOWELS = set("aeiouyAEIOUY")


def estimate_text_duration(text: str, lang: Optional[str] = None) -> float:
    """
    预估文本的朗读时长（秒）
    支持中文、英文、日文、韩文等混合文本
    """
    if not text or not isinstance(text, str):
        return 0.0

    normalized = text.strip()
    if not normalized:
        return 0.0

    detected_lang = lang or _detect_language(normalized)

    # 按标点和空格分段计算
    segments = re.split(r"(\s+|[，；：,;、]+|[。！？.!?]+)", normalized)
    total_duration = 0.0

    for segment in segments:
        if not segment:
            continue

        # 空格停顿
        if re.match(r"\s+", segment):
            total_duration += PUNCTUATION_PAUSE_SPACE
        # 中间标点
        elif MID_PUNCTUATION_RE.match(segment):
            total_duration += PUNCTUATION_PAUSE_MID
        # 结尾标点
        elif END_PUNCTUATION_RE.match(segment):
            total_duration += PUNCTUATION_PAUSE_END
        # 文本内容
        else:
            seg_lang = _detect_language(segment) if lang is None else lang
            syllable_count = _count_syllables(segment, seg_lang)
            duration_per_syllable = LANGUAGE_DURATION_PARAMS.get(
                seg_lang, LANGUAGE_DURATION_PARAMS["default"]
            )
            total_duration += syllable_count * duration_per_syllable

    return max(0.1, total_duration)


def _detect_language(text: str) -> str:
    """检测文本的主要语言"""
    if CJK_CHAR_RE.search(text):
        return "zh"
    if JAPANESE_CHAR_RE.search(text):
        return "ja"
    if KOREAN_CHAR_RE.search(text):
        return "ko"
    if re.search(r"[àâçéèêëîïôùûüÿœæ]", text):
        return "fr"
    if re.search(r"[áéíóúñ¿¡]", text):
        return "es"
    return "en"


def _count_syllables(text: str, lang: str) -> int:
    """统计文本的音节数"""
    if not text:
        return 0

    if lang == "zh":
        # 中文：每个汉字算一个音节
        return len(CJK_CHAR_RE.findall(text))

    if lang == "ja":
        # 日文：假名和汉字各算一个音节
        return len(JAPANESE_CHAR_RE.findall(text))

    if lang == "ko":
        # 韩文：每个韩字算一个音节
        return len(KOREAN_CHAR_RE.findall(text))

    if lang in ("fr", "es"):
        # 法文/西班牙文：按元音群统计
        vowels = "aeiouyàâéèêëîïôùûüÿœæ" if lang == "fr" else "aeiouáéíóúü"
        count = len(re.findall(f"[{vowels}]+", text.lower()))
        return max(1, count)

    # 英文：按单词统计，每个单词至少一个音节
    return _count_english_syllables(text)


def _count_english_syllables(text: str) -> int:
    """统计英文文本的音节数（简化版，不依赖外部库）"""
    total = 0
    for word in text.strip().split():
        # 移除标点
        cleaned = re.sub(r"[^\w]", "", word)
        if not cleaned:
            continue
        # 简化的音节统计：按元音群计数
        # 至少一个音节
        count = len(re.findall(r"[aeiouyAEIOUY]+", cleaned))
        total += max(1, count)
    return max(1, total) if text.strip() else 0
