# backend/core/__init__.py
# 核心逻辑包初始化

from .downloader import Downloader
from .ffmpeg_processor import FFmpegProcessor
from .dedup import DedupChecker
from .subtitle_engine import SubtitleEngine
from .voice_engine import VoiceEngine
from .text_engine import TextEngine
from .local_asr import LocalSpeechRecognizer

__all__ = [
    "Downloader",
    "FFmpegProcessor",
    "DedupChecker",
    "SubtitleEngine",
    "VoiceEngine",
    "TextEngine",
    "LocalSpeechRecognizer",
]
