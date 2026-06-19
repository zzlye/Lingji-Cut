# backend/api/subtitles.py
# 字幕 API 路由 - 提供字幕处理接口

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
import json
import os
import ctypes
import platform
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from urllib.parse import unquote, urlparse

from ..core import Downloader, FFmpegProcessor, GeminiAudioTranscriber, LocalSpeechRecognizer, SubtitleEngine, TextEngine
from ..core.paths import detect_video_workspace, ensure_project_dirs, ensure_video_workspace
from ..core.process_control import TaskControlRequested
from ..models import DownloadTask, SubtitlePreset, TextProviderProfile, VideoSource, get_db
from ..utils import decrypt_api_key

# 创建路由器
router = APIRouter(prefix="/subtitles", tags=["subtitles"])


class SubtitlePresetCreate(BaseModel):
    """创建字幕预设请求"""
    name: str
    is_default: bool = False
    line_mode: str = "single"
    language: str = "auto"
    font_name: str = "Microsoft YaHei"
    font_size: int = 48
    secondary_font_size: int = 42
    font_color: str = "#FFFFFF"
    secondary_color: str = "#FDE68A"
    outline_color: str = "#000000"
    outline_width: int = 2
    shadow_enabled: bool = True
    shadow_color: str = "#80000000"
    shadow_x: int = 2
    shadow_y: int = 2
    background_alpha: int = 0
    position: str = "bottom"
    margin_v: int = 30


class SubtitlePresetRename(BaseModel):
    """修改字幕预设名称请求"""
    name: str


class SubtitlePresetResponse(BaseModel):
    """字幕预设响应"""
    id: int
    name: str
    is_default: bool
    line_mode: str
    language: str
    font_name: str
    font_size: int
    secondary_font_size: int
    font_color: str
    secondary_color: str
    outline_color: str
    outline_width: int
    shadow_enabled: bool
    shadow_color: str
    shadow_x: int
    shadow_y: int
    background_alpha: int
    position: str
    margin_v: int

    class Config:
        from_attributes = True


class SubtitleRenderRequest(BaseModel):
    """字幕渲染请求"""
    video_id: int
    video_path: str
    preset_id: Optional[int] = None
    language: Optional[str] = None
    sub_type: str = "auto"
    burn_in: bool = True
    subtitle_path: Optional[str] = None
    output_path: Optional[str] = None


class SubtitleRenderResponse(BaseModel):
    """字幕渲染响应"""
    message: str
    task_id: int
    subtitle_path: str
    ass_path: str
    output_path: Optional[str] = None
    plain_text: str = ""


class SubtitleTextProcessRequest(BaseModel):
    """字幕文本处理请求"""
    text: str
    profile_id: int
    operation: str = "polish"
    target_language: Optional[str] = None
    custom_instruction: Optional[str] = None
    system_prompt: Optional[str] = None


class SubtitleTextProcessResponse(BaseModel):
    """字幕文本处理响应"""
    message: str
    text: str
    operation: str


class SubtitleEntryPayload(BaseModel):
    """字幕校对条目"""
    index: int = 1
    start: str
    end: str
    text: str


class SubtitleEntriesProcessRequest(BaseModel):
    """按字幕条目执行 AI 润色/翻译"""
    entries: list[SubtitleEntryPayload] = Field(default_factory=list)
    profile_id: int
    operation: str = "polish"
    target_language: Optional[str] = None
    custom_instruction: Optional[str] = None
    system_prompt: Optional[str] = None


class SubtitleEntriesProcessResponse(BaseModel):
    """字幕条目 AI 处理响应"""
    message: str
    entries: list[SubtitleEntryPayload] = Field(default_factory=list)
    plain_text: str = ""
    operation: str


class SubtitleCorrectionParseFileRequest(BaseModel):
    """读取本地字幕文件请求"""
    subtitle_path: str


class SubtitleCorrectionParseTextRequest(BaseModel):
    """解析粘贴字幕文本请求"""
    content: str
    format: str = "srt"


class SubtitleSegmentRecognizeRequest(BaseModel):
    """重新识别指定视频时间段请求"""
    video_path: str
    start: str
    end: str
    language: Optional[str] = None


class SubtitleSegmentOrganizeRequest(BaseModel):
    """用 API 多模态模型直接听音频并整理字幕"""
    video_path: str
    start: str
    end: str
    entries: list[SubtitleEntryPayload] = Field(default_factory=list)
    profile_id: int
    custom_instruction: Optional[str] = None
    system_prompt: Optional[str] = None


class SubtitleSegmentOrganizeResponse(BaseModel):
    """AI 整理字幕响应"""
    message: str
    entries: list[SubtitleEntryPayload] = Field(default_factory=list)
    plain_text: str = ""
    video_path: str
    start: str
    end: str


class SubtitleCorrectionSaveRequest(BaseModel):
    """保存校对后字幕请求"""
    entries: list[SubtitleEntryPayload] = Field(default_factory=list)
    output_path: Optional[str] = None
    file_name: Optional[str] = None
    format: str = "srt"
    source_path: Optional[str] = None


class SubtitleCorrectionSaveAssRequest(BaseModel):
    """保存校对后 ASS 字幕请求"""
    entries: list[SubtitleEntryPayload] = Field(default_factory=list)
    output_path: Optional[str] = None
    file_name: Optional[str] = None
    preset_id: Optional[int] = None
    source_path: Optional[str] = None


class SubtitleCorrectionResponse(BaseModel):
    """字幕校对响应"""
    message: str
    entries: list[SubtitleEntryPayload] = Field(default_factory=list)
    plain_text: str = ""
    output_path: Optional[str] = None
    format: Optional[str] = None


class SubtitleSegmentRecognizeResponse(SubtitleCorrectionResponse):
    """重新识别指定时间段响应"""
    video_path: str
    start: str
    end: str
    language: str = "auto"


class FontInstallRequest(BaseModel):
    """字体安装请求"""
    font_name: str


class FontInstallResponse(BaseModel):
    """字体安装响应"""
    message: str
    font_name: str
    font_dir: str
    installed_files: list[str] = Field(default_factory=list)


# 免费字体下载源；只放可公开下载的字体文件，不处理商业字体授权。
FREE_FONT_SOURCES: dict[str, dict] = {
    "source han sans sc": {
        "display_name": "Source Han Sans SC",
        "urls": ["https://raw.githubusercontent.com/adobe-fonts/source-han-sans/release/OTF/SimplifiedChinese/SourceHanSansSC-Regular.otf"],
    },
    "source han serif sc": {
        "display_name": "Source Han Serif SC",
        "urls": ["https://raw.githubusercontent.com/adobe-fonts/source-han-serif/release/OTF/SimplifiedChinese/SourceHanSerifSC-Regular.otf"],
    },
    "noto sans sc": {
        "display_name": "Noto Sans SC",
        "urls": ["https://github.com/google/fonts/raw/main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf"],
    },
    "noto serif sc": {
        "display_name": "Noto Serif SC",
        "urls": ["https://github.com/google/fonts/raw/main/ofl/notoserifsc/NotoSerifSC%5Bwght%5D.ttf"],
    },
    "noto sans cjk sc": {
        "display_name": "Noto Sans CJK SC",
        "urls": ["https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf"],
    },
    "lxgw wenkai": {
        "display_name": "LXGW WenKai",
        "urls": ["https://raw.githubusercontent.com/lxgw/LxgwWenKai/main/fonts/TTF/LXGWWenKai-Regular.ttf"],
    },
    "zcool qingke huangyou": {
        "display_name": "ZCOOL QingKe HuangYou",
        "urls": ["https://github.com/google/fonts/raw/main/ofl/zcoolqingkehuangyou/ZCOOLQingKeHuangYou-Regular.ttf"],
    },
    "zcool kuaile": {
        "display_name": "ZCOOL KuaiLe",
        "urls": ["https://github.com/google/fonts/raw/main/ofl/zcoolkuaile/ZCOOLKuaiLe-Regular.ttf"],
    },
    "zcool xiaowei": {
        "display_name": "ZCOOL XiaoWei",
        "urls": ["https://github.com/google/fonts/raw/main/ofl/zcoolxiaowei/ZCOOLXiaoWei-Regular.ttf"],
    },
    "ma shan zheng": {
        "display_name": "Ma Shan Zheng",
        "urls": ["https://github.com/google/fonts/raw/main/ofl/mashanzheng/MaShanZheng-Regular.ttf"],
    },
    "long cang": {
        "display_name": "Long Cang",
        "urls": ["https://github.com/google/fonts/raw/main/ofl/longcang/LongCang-Regular.ttf"],
    },
    "zhi mang xing": {
        "display_name": "Zhi Mang Xing",
        "urls": ["https://github.com/google/fonts/raw/main/ofl/zhimangxing/ZhiMangXing-Regular.ttf"],
    },
    "m plus rounded 1c": {
        "display_name": "M PLUS Rounded 1c",
        "urls": ["https://github.com/google/fonts/raw/main/ofl/mplusrounded1c/MPLUSRounded1c-Regular.ttf"],
    },
    "zen maru gothic": {
        "display_name": "Zen Maru Gothic",
        "urls": ["https://github.com/google/fonts/raw/main/ofl/zenmarugothic/ZenMaruGothic-Regular.ttf"],
    },
}


def _preset_to_dict(preset: SubtitlePreset | None) -> dict:
    """将字幕预设模型转换为渲染配置"""
    if not preset:
        return {}
    return {
        "name": preset.name,
        "line_mode": preset.line_mode,
        "language": preset.language,
        "font_name": preset.font_name,
        "font_size": preset.font_size,
        "secondary_font_size": preset.secondary_font_size,
        "font_color": preset.font_color,
        "secondary_color": preset.secondary_color,
        "outline_color": preset.outline_color,
        "outline_width": preset.outline_width,
        "shadow_enabled": preset.shadow_enabled,
        "shadow_color": preset.shadow_color,
        "shadow_x": preset.shadow_x,
        "shadow_y": preset.shadow_y,
        "background_alpha": preset.background_alpha,
        "position": preset.position,
        "margin_v": preset.margin_v,
    }


def _pick_subtitle_preset(db: Session, preset_id: Optional[int]) -> SubtitlePreset | None:
    """选择指定或默认字幕预设"""
    if preset_id:
        preset = db.query(SubtitlePreset).filter(SubtitlePreset.id == preset_id).first()
        if not preset:
            raise HTTPException(status_code=404, detail="字幕预设不存在")
        return preset
    return db.query(SubtitlePreset).filter(SubtitlePreset.is_default == True).first() or db.query(SubtitlePreset).first()


def _parse_subtitle_entries(engine: SubtitleEngine, subtitle_path: str) -> list[dict]:
    """按字幕文件格式解析字幕条目"""
    ext = os.path.splitext(subtitle_path)[1].lower()
    if ext == ".srt":
        return engine.parse_srt(subtitle_path)
    if ext == ".vtt":
        return engine.parse_vtt(subtitle_path)
    if ext == ".ass":
        return engine.parse_ass(subtitle_path)
    raise HTTPException(status_code=400, detail=f"暂不支持的字幕格式: {ext}")


def _parse_subtitle_text(engine: SubtitleEngine, content: str, subtitle_format: str) -> list[dict]:
    """按格式解析粘贴的字幕文本"""
    normalized_format = subtitle_format.lower().lstrip(".")
    if normalized_format == "srt":
        return engine.parse_srt_content(content)
    if normalized_format == "vtt":
        return engine.parse_vtt_content(content)
    raise HTTPException(status_code=400, detail=f"暂不支持的字幕文本格式: {subtitle_format}")


def _normalize_correction_entries(engine: SubtitleEngine, entries: list[SubtitleEntryPayload]) -> list[dict]:
    """清理前端提交的字幕条目，保证时间码和序号稳定"""
    normalized: list[dict] = []
    for item in entries:
        text = engine.clean_subtitle_text_for_output(item.text)
        if not text or engine.is_meaningless_subtitle_text(text):
            continue
        start = engine.normalize_srt_time(item.start)
        end = engine.normalize_srt_time(item.end)
        normalized.append({
            "index": len(normalized) + 1,
            "start": start,
            "end": end,
            "text": text,
        })
    if not normalized:
        raise HTTPException(status_code=400, detail="字幕条目不能为空")
    return normalized


def _entry_payloads(entries: list[dict]) -> list[SubtitleEntryPayload]:
    """把字幕字典转换成 API 响应模型"""
    return [
        SubtitleEntryPayload(
            index=int(entry.get("index") or index),
            start=str(entry.get("start") or "00:00:00,000"),
            end=str(entry.get("end") or "00:00:00,000"),
            text=str(entry.get("text") or ""),
        )
        for index, entry in enumerate(entries, 1)
    ]


def _safe_subtitle_output_path(
    output_path: Optional[str],
    file_name: Optional[str],
    extension: str,
    source_path: Optional[str] = None,
) -> str:
    """生成字幕输出路径，优先复用当前字幕或视频所属目录，避免不同视频的素材混在一起"""
    if output_path and output_path.strip():
        path = os.path.abspath(os.path.expanduser(output_path.strip()))
    else:
        base_dir = _subtitle_output_base_dir(source_path)
        raw_name = (file_name or _subtitle_default_file_name(source_path, extension)).strip()
        safe_name = _sanitize_subtitle_file_name(raw_name, extension)
        path = os.path.join(base_dir, safe_name)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _subtitle_output_base_dir(source_path: Optional[str]) -> str:
    """根据当前字幕或视频路径推导默认输出目录，优先落到该视频的工作目录 output 下"""
    raw_source_path = str(source_path or "").strip()
    if raw_source_path:
        normalized = os.path.abspath(os.path.expanduser(raw_source_path))
        workspace_paths = detect_video_workspace(normalized)
        if workspace_paths:
            return workspace_paths["output_dir"]
        parent_dir = normalized if os.path.isdir(normalized) else os.path.dirname(normalized)
        if parent_dir:
            return parent_dir
    return ensure_project_dirs()["output_dir"]


def _subtitle_default_file_name(source_path: Optional[str], extension: str) -> str:
    """没有显式文件名时，优先沿用源文件名，避免同一视频下生成难以辨认的通用文件名"""
    raw_source_path = str(source_path or "").strip()
    if raw_source_path:
        base_name = os.path.splitext(os.path.basename(raw_source_path))[0].strip()
        if base_name:
            return f"{base_name}.{extension}"
    return f"manual_subtitle_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{extension}"


def _sanitize_subtitle_file_name(file_name: str, extension: str) -> str:
    """把用户输入的文件名整理成安全路径片段，并补齐目标扩展名"""
    safe_name = "".join(char if char.isalnum() or char in ("-", "_", ".") else "_" for char in str(file_name or "").strip())
    safe_name = safe_name.strip("._") or "manual_subtitle"
    if not safe_name.lower().endswith(f".{extension}"):
        safe_name = f"{os.path.splitext(safe_name)[0]}.{extension}"
    return safe_name


def entries_to_plain_text(entries: list[dict], max_chars: int = 6000) -> str:
    """将字幕条目转换成适合配音或文本 API 处理的纯文本"""
    lines: list[str] = []
    previous = ""
    for entry in entries:
        text = str(entry.get("text", "")).replace("\\N", "\n")
        text = " ".join(line.strip() for line in text.splitlines() if line.strip())
        if not text or text == previous:
            continue
        lines.append(text)
        previous = text
        if sum(len(line) for line in lines) >= max_chars:
            break

    return "\n".join(lines)[:max_chars]


def _build_ai_organize_segment_prompt(
    entries: list[dict],
    start_seconds: float,
    end_seconds: float,
    custom_instruction: str,
    system_prompt: str,
) -> str:
    """构造 AI 整理提示词，让 API 模型直接听音频并返回最终字幕时间轴"""
    reference_items = []
    for index, entry in enumerate(entries, 1):
        item_start = max(0.0, _srt_time_to_seconds(str(entry.get("start") or "00:00:00,000")) - start_seconds)
        item_end = max(item_start + 0.2, _srt_time_to_seconds(str(entry.get("end") or entry.get("start") or "00:00:00,000")) - start_seconds)
        reference_items.append({
            "id": index,
            "start": round(item_start, 3),
            "end": round(min(max(0.0, end_seconds - start_seconds), item_end), 3),
            "text": str(entry.get("text") or "").replace("\\N", "\n"),
        })

    instruction = str(custom_instruction or "").strip() or "在尽量保持当前字幕条数和断句的前提下，只修正明显错漏和不连贯表达。"
    preset = str(system_prompt or "").strip() or "你是专业视频字幕编辑助手，必须严格根据用户要求编辑当前字幕。"
    return "\n".join([
        preset,
        "",
        "你会收到一段从原视频截取的音频和当前字幕参考。这里是字幕编辑任务，不是重新转写任务。",
        "默认保持当前字幕的条数、顺序、断句和大部分时间轴；只有用户明确要求合并、拆分、移动时才改变对应部分。",
        "不要因为你觉得另一种断句更自然就强行重新分割；不要擅自新增或减少字幕条目。",
        "用户要求必须优先执行。用户只要求移动某个词时，只移动这个词以及它在另一种语言里的对应词，其余内容和条数尽量不动。",
        "例如用户说“把但是放后面”，应把“但是”和对应原文转折词（如 tapi/but）一起放到后半句，不要把“但是/tapi”留在前一条。",
        "例如用户说“拆成两个：A / B”，才输出两条字幕；否则尽量保持输入条数。",
        "当前字幕参考是主要编辑对象；音频用于核对漏字、错字和词语真实位置。参考字幕和音频冲突时，只修正冲突处。",
        "输出语言和格式尽量沿用参考字幕；如果参考字幕是双语，每条 text 可以用换行保留双语。",
        "start/end 必须是这段音频内的相对秒数，从 0 开始。未被用户要求改变的条目，尽量沿用参考字幕的 start/end。",
        "只输出 JSON 数组，不要 Markdown，不要解释，不要 SRT 编号。",
        "JSON 格式：[{\"start\":0.12,\"end\":1.85,\"text\":\"字幕文本\"}]",
        "",
        f"片段时长：{max(0.0, end_seconds - start_seconds):.3f} 秒",
        f"用户要求：{instruction}",
        f"当前字幕参考 JSON：{json.dumps(reference_items, ensure_ascii=False)}",
    ])


def _srt_time_to_seconds(value: str) -> float:
    """把 SRT 时间码转换成秒，用于截取视频片段"""
    text = str(value or "").strip().replace(",", ".")
    parts = text.split(":")
    if len(parts) != 3:
        raise HTTPException(status_code=400, detail=f"时间格式不正确: {value}")
    try:
        hours = float(parts[0])
        minutes = float(parts[1])
        seconds = float(parts[2])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"时间格式不正确: {value}") from exc
    return max(0.0, hours * 3600 + minutes * 60 + seconds)


def _seconds_to_srt_time(seconds: float) -> str:
    """把秒转换成 SRT 时间码"""
    total_ms = max(0, int(round(seconds * 1000)))
    hours = total_ms // 3600000
    minutes = (total_ms % 3600000) // 60000
    secs = (total_ms % 60000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _export_video_segment(video_path: str, start_seconds: float, end_seconds: float) -> str:
    """用 ffmpeg 截取局部视频，交给本地识别器重新识别"""
    duration = max(0.0, end_seconds - start_seconds)
    if duration < 0.2:
        raise HTTPException(status_code=400, detail="重新识别的时间段太短")
    fd, output_path = tempfile.mkstemp(prefix="ytv_reasr_", suffix=".wav")
    os.close(fd)
    command = [
        "ffmpeg",
        "-y",
        "-ss", f"{start_seconds:.3f}",
        "-t", f"{duration:.3f}",
        "-i", video_path,
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-af", "highpass=f=70,loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:a", "pcm_s16le",
        output_path,
    ]
    try:
        from ..core.tooling import get_ffmpeg_command
        command[0] = get_ffmpeg_command()
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            check=False,
        )
    except Exception as exc:
        _safe_remove_file(output_path)
        raise HTTPException(status_code=500, detail=f"截取视频片段失败: {exc}") from exc
    if result.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) <= 64:
        detail = (result.stderr or result.stdout or "").strip()[:400]
        _safe_remove_file(output_path)
        raise HTTPException(status_code=500, detail=f"截取视频片段失败: {detail or 'ffmpeg 未生成片段'}")
    return output_path


def _shift_entries_to_absolute_time(entries: list[dict], offset_seconds: float, max_end_seconds: float) -> list[dict]:
    """把片段内相对时间轴平移回原视频绝对时间轴"""
    shifted: list[dict] = []
    for entry in entries:
        start = offset_seconds + _srt_time_to_seconds(str(entry.get("start") or "00:00:00,000"))
        end = offset_seconds + _srt_time_to_seconds(str(entry.get("end") or entry.get("start") or "00:00:00,000"))
        end = min(max_end_seconds, max(start + 0.2, end))
        text = str(entry.get("text") or "").strip()
        if not text:
            continue
        shifted.append({
            "index": len(shifted) + 1,
            "start": _seconds_to_srt_time(start),
            "end": _seconds_to_srt_time(end),
            "text": text,
        })
    return shifted


def _safe_remove_file(path: str) -> None:
    """删除临时文件，失败时静默忽略"""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _load_text_settings(profile: TextProviderProfile) -> dict:
    """读取文本 API 配置中的生成参数"""
    if not profile.extra_params:
        return {}
    import json

    try:
        data = json.loads(profile.extra_params)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _text_settings_with_prompt(profile: TextProviderProfile, system_prompt: Optional[str]) -> dict:
    """读取文本 API 参数，并用独立提示词预设覆盖旧配置里的提示词"""
    settings = _load_text_settings(profile)
    prompt = str(system_prompt or "").strip()
    if prompt:
        settings["system_prompt"] = prompt
    return settings


def _default_subtitle_presets() -> list[SubtitlePresetCreate]:
    """内置字幕样式预设，保证字幕设置页和一键流程开箱即用"""
    return [
        # 默认：白字黑边、底部居中，适配绝大多数横屏短视频
        SubtitlePresetCreate(name="默认字幕", is_default=True, line_mode="single"),
        # 醒目大字：字号更大、描边更粗、上移边距，适合强调或竖屏
        SubtitlePresetCreate(name="醒目大字", line_mode="single", font_size=56, outline_width=3, margin_v=48),
    ]


def ensure_default_subtitle_presets(db: Session) -> None:
    """字幕预设为空时惰性创建内置默认预设。

    一键流程的 _pick_subtitle_preset 直接查库取 is_default 预设，
    因此后端启动时也会调用本函数，避免新用户未进设置页就一键完成时拿不到统一字幕样式。
    """
    if db.query(SubtitlePreset).first():
        return
    for item in _default_subtitle_presets():
        db.add(SubtitlePreset(**item.model_dump()))
    db.commit()


def _normalize_font_key(font_name: str) -> str:
    """归一化字体名称，用来匹配后端内置的免费字体下载源"""
    return str(font_name or "").strip().lower()


def _resolve_free_font_source(font_name: str) -> dict:
    """根据字体名读取免费字体来源，不允许下载未登记或商业字体"""
    key = _normalize_font_key(font_name)
    if not key:
        raise HTTPException(status_code=400, detail="字体名称不能为空")
    source = FREE_FONT_SOURCES.get(key)
    if not source:
        raise HTTPException(status_code=400, detail="当前字体没有内置下载源，请从字体官网安装后再使用")
    return source


def _download_font_sources(source: dict, temp_dir: str) -> list[str]:
    """下载字体源文件，支持直接字体文件和 zip 压缩包"""
    downloaded: list[str] = []
    for url in source.get("urls") or []:
        parsed = urlparse(str(url))
        raw_name = unquote(os.path.basename(parsed.path)).strip() or "font_download"
        target_path = os.path.join(temp_dir, raw_name)
        request = urllib.request.Request(str(url), headers={"User-Agent": "LingjianWorkshop/0.1"})
        with urllib.request.urlopen(request, timeout=120) as response:
            with open(target_path, "wb") as handle:
                shutil.copyfileobj(response, handle)
        downloaded.append(target_path)
    return downloaded


def _collect_font_files(paths: list[str], temp_dir: str) -> list[str]:
    """从下载产物中提取真正可安装的字体文件"""
    font_files: list[str] = []
    allowed_extensions = {".ttf", ".otf", ".ttc", ".otc"}
    extract_dir = os.path.join(temp_dir, "extracted")
    for path in paths:
        extension = os.path.splitext(path)[1].lower()
        if extension == ".zip":
            with zipfile.ZipFile(path) as archive:
                archive.extractall(extract_dir)
            for root, _dirs, files in os.walk(extract_dir):
                for file_name in files:
                    if os.path.splitext(file_name)[1].lower() in allowed_extensions:
                        font_files.append(os.path.join(root, file_name))
        elif extension in allowed_extensions:
            font_files.append(path)

    if not font_files:
        raise RuntimeError("下载完成，但没有找到可安装的 ttf/otf 字体文件")
    return font_files


def _font_install_target_dir() -> str:
    """按系统返回当前用户可写的字体安装目录"""
    system = platform.system().lower()
    if system == "windows":
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Microsoft", "Windows", "Fonts")
    if system == "darwin":
        return os.path.expanduser("~/Library/Fonts")
    return os.path.expanduser("~/.local/share/fonts")


def _registry_font_kind(path: str) -> str:
    """Windows 字体注册表显示类型"""
    return "TrueType" if os.path.splitext(path)[1].lower() in {".ttf", ".ttc"} else "OpenType"


def _notify_windows_font_changed() -> None:
    """通知 Windows 刷新字体缓存，失败不影响已经复制和注册的字体"""
    try:
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001D, 0, "Environment", 0x0002, 1000, None)
    except Exception:
        pass


def _install_font_files(font_files: list[str], display_name: str) -> tuple[str, list[str]]:
    """把字体安装到当前用户目录，避免要求管理员权限"""
    target_dir = _font_install_target_dir()
    os.makedirs(target_dir, exist_ok=True)
    installed_files: list[str] = []

    for index, source_path in enumerate(font_files, 1):
        file_name = os.path.basename(source_path)
        target_path = os.path.join(target_dir, file_name)
        shutil.copy2(source_path, target_path)
        installed_files.append(target_path)

        if platform.system().lower() == "windows":
            value_name = f"{display_name}{'' if len(font_files) == 1 else f' {index}'} ({_registry_font_kind(target_path)})"
            result = subprocess.run(
                ["reg", "add", r"HKCU\Software\Microsoft\Windows NT\CurrentVersion\Fonts", "/v", value_name, "/t", "REG_SZ", "/d", target_path, "/f"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "注册 Windows 字体失败").strip())

    if platform.system().lower() == "windows":
        _notify_windows_font_changed()
    elif platform.system().lower() == "linux":
        subprocess.run(["fc-cache", "-f", target_dir], capture_output=True, check=False)

    return target_dir, installed_files


@router.get("/presets", response_model=list[SubtitlePresetResponse])
async def get_presets(db: Session = Depends(get_db)):
    """获取所有字幕预设；表为空时惰性创建内置默认预设，避免新用户面对空列表"""
    ensure_default_subtitle_presets(db)
    return db.query(SubtitlePreset).order_by(SubtitlePreset.id.asc()).all()


@router.post("/presets", response_model=SubtitlePresetResponse)
async def create_preset(preset: SubtitlePresetCreate, db: Session = Depends(get_db)):
    """创建字幕预设"""
    db_preset = SubtitlePreset(**preset.model_dump())
    db.add(db_preset)
    db.commit()
    db.refresh(db_preset)
    return db_preset


@router.put("/presets/{preset_id}", response_model=SubtitlePresetResponse)
async def update_preset(preset_id: int, preset: SubtitlePresetCreate, db: Session = Depends(get_db)):
    """更新字幕预设"""
    db_preset = db.query(SubtitlePreset).filter(SubtitlePreset.id == preset_id).first()
    if not db_preset:
        raise HTTPException(status_code=404, detail="预设不存在")

    for key, value in preset.model_dump().items():
        setattr(db_preset, key, value)
    db.commit()
    db.refresh(db_preset)
    return db_preset


@router.patch("/presets/{preset_id}/name", response_model=SubtitlePresetResponse)
async def rename_preset(preset_id: int, request: SubtitlePresetRename, db: Session = Depends(get_db)):
    """只修改字幕预设名称，不影响字号、颜色、字体等样式参数"""
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="预设名称不能为空")
    db_preset = db.query(SubtitlePreset).filter(SubtitlePreset.id == preset_id).first()
    if not db_preset:
        raise HTTPException(status_code=404, detail="预设不存在")

    db_preset.name = name
    db.commit()
    db.refresh(db_preset)
    return db_preset


@router.delete("/presets/{preset_id}")
async def delete_preset(preset_id: int, db: Session = Depends(get_db)):
    """删除字幕预设"""
    preset = db.query(SubtitlePreset).filter(SubtitlePreset.id == preset_id).first()
    if not preset:
        raise HTTPException(status_code=404, detail="预设不存在")
    db.delete(preset)
    db.commit()
    return {"message": "预设已删除"}


@router.post("/fonts/install", response_model=FontInstallResponse)
async def install_free_font(request: FontInstallRequest):
    """下载并安装内置免费字体到当前用户字体目录"""
    source = _resolve_free_font_source(request.font_name)
    display_name = str(source.get("display_name") or request.font_name).strip()
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloaded = _download_font_sources(source, temp_dir)
            font_files = _collect_font_files(downloaded, temp_dir)
            font_dir, installed_files = _install_font_files(font_files, display_name)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"安装字体失败: {exc}") from exc

    return FontInstallResponse(
        message=f"已安装 {display_name}。如果预览没有立刻变化，请重启软件后再导出。",
        font_name=display_name,
        font_dir=font_dir,
        installed_files=installed_files,
    )


@router.post("/process-text", response_model=SubtitleTextProcessResponse)
async def process_subtitle_text(request: SubtitleTextProcessRequest, db: Session = Depends(get_db)):
    """使用已保存文本 API 生成、翻译或润色字幕文本"""
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="字幕文本不能为空")

    profile = db.query(TextProviderProfile).filter(TextProviderProfile.id == request.profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="文本 API 配置不存在")

    try:
        processed = await TextEngine().process_text(
            text=request.text,
            provider_type=profile.provider_type,
            api_key=decrypt_api_key(profile.api_key_encrypted),
            base_url=profile.base_url,
            model=profile.model or "",
            settings=_text_settings_with_prompt(profile, request.system_prompt),
            operation=request.operation,
            target_language=request.target_language or "",
            custom_instruction=request.custom_instruction or "",
        )
        return SubtitleTextProcessResponse(
            message="字幕文本处理完成",
            text=processed,
            operation=request.operation,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/process-entries", response_model=SubtitleEntriesProcessResponse)
async def process_subtitle_entries(request: SubtitleEntriesProcessRequest, db: Session = Depends(get_db)):
    """按字幕条目执行 AI 处理，并保持原有时间轴"""
    profile = db.query(TextProviderProfile).filter(TextProviderProfile.id == request.profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="文本 API 配置不存在")

    engine = SubtitleEngine()
    entries = _normalize_correction_entries(engine, request.entries)

    try:
        processed_entries = await TextEngine().process_subtitle_entries(
            entries=entries,
            provider_type=profile.provider_type,
            api_key=decrypt_api_key(profile.api_key_encrypted),
            base_url=profile.base_url,
            model=profile.model or "",
            settings=_text_settings_with_prompt(profile, request.system_prompt),
            operation=request.operation,
            target_language=request.target_language or "",
            custom_instruction=request.custom_instruction or "",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return SubtitleEntriesProcessResponse(
        message="字幕条目处理完成",
        entries=_entry_payloads(processed_entries),
        plain_text=entries_to_plain_text(processed_entries),
        operation=request.operation,
    )


@router.post("/recognize-segment", response_model=SubtitleSegmentRecognizeResponse)
async def recognize_subtitle_segment(request: SubtitleSegmentRecognizeRequest):
    """重新识别指定视频时间段，返回原视频绝对时间轴字幕"""
    video_path = os.path.abspath(os.path.expanduser(request.video_path.strip()))
    if not video_path or not os.path.exists(video_path):
        raise HTTPException(status_code=404, detail=f"视频文件不存在: {request.video_path}")

    start_seconds = _srt_time_to_seconds(request.start)
    end_seconds = _srt_time_to_seconds(request.end)
    if end_seconds <= start_seconds:
        raise HTTPException(status_code=400, detail="重新识别结束时间必须晚于开始时间")

    segment_path = _export_video_segment(video_path, start_seconds, end_seconds)
    try:
        language = (request.language or "").strip()
        language_arg = None if not language or language == "auto" else language
        entries, detected_language = LocalSpeechRecognizer().transcribe_video(segment_path, language=language_arg)
        shifted_entries = _shift_entries_to_absolute_time(entries, start_seconds, end_seconds)
        if not shifted_entries:
            raise RuntimeError("本地识别没有返回可用字幕")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"重新识别失败: {exc}") from exc
    finally:
        _safe_remove_file(segment_path)

    return SubtitleSegmentRecognizeResponse(
        message=f"已重新识别 {len(shifted_entries)} 条字幕",
        entries=_entry_payloads(shifted_entries),
        plain_text=entries_to_plain_text(shifted_entries),
        video_path=video_path,
        start=_seconds_to_srt_time(start_seconds),
        end=_seconds_to_srt_time(end_seconds),
        language=detected_language,
    )


@router.post("/organize-segment", response_model=SubtitleSegmentOrganizeResponse)
async def organize_subtitle_segment(request: SubtitleSegmentOrganizeRequest, db: Session = Depends(get_db)):
    """用文本 API 的多模态模型直接听视频片段，并按用户要求输出最终字幕"""
    video_path = os.path.abspath(os.path.expanduser(request.video_path.strip()))
    if not video_path or not os.path.exists(video_path):
        raise HTTPException(status_code=404, detail=f"视频文件不存在: {request.video_path}")

    profile = db.query(TextProviderProfile).filter(TextProviderProfile.id == request.profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="文本 API 配置不存在")

    start_seconds = _srt_time_to_seconds(request.start)
    end_seconds = _srt_time_to_seconds(request.end)
    if end_seconds <= start_seconds:
        raise HTTPException(status_code=400, detail="AI 整理结束时间必须晚于开始时间")

    engine = SubtitleEngine()
    current_entries = _normalize_correction_entries(engine, request.entries)
    prompt = _build_ai_organize_segment_prompt(
        current_entries,
        start_seconds,
        end_seconds,
        request.custom_instruction or "",
        request.system_prompt or "",
    )
    segment_path = _export_video_segment(video_path, start_seconds, end_seconds)
    try:
        settings = _text_settings_with_prompt(profile, request.system_prompt)
        organized_entries = await GeminiAudioTranscriber(
            provider_type=profile.provider_type,
            api_key=decrypt_api_key(profile.api_key_encrypted),
            base_url=profile.base_url,
            model=profile.model or "",
            settings=settings,
        ).organize_audio_file(
            segment_path,
            prompt=prompt,
            start_offset=start_seconds,
            max_end_seconds=end_seconds,
        )
        if not organized_entries:
            raise RuntimeError("AI 模型没有返回可用字幕")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AI 整理失败: {exc}") from exc
    finally:
        _safe_remove_file(segment_path)

    return SubtitleSegmentOrganizeResponse(
        message=f"AI 整理完成，生成 {len(organized_entries)} 条字幕",
        entries=_entry_payloads(organized_entries),
        plain_text=entries_to_plain_text(organized_entries),
        video_path=video_path,
        start=_seconds_to_srt_time(start_seconds),
        end=_seconds_to_srt_time(end_seconds),
    )


@router.post("/parse-file", response_model=SubtitleCorrectionResponse)
async def parse_subtitle_file(request: SubtitleCorrectionParseFileRequest):
    """读取本地字幕文件，返回可手动校对的条目"""
    raw_path = request.subtitle_path.strip()
    if not raw_path:
        raise HTTPException(status_code=400, detail="请填写字幕文件路径")
    subtitle_path = os.path.abspath(os.path.expanduser(raw_path))
    if not os.path.exists(subtitle_path):
        raise HTTPException(status_code=404, detail=f"字幕文件不存在: {subtitle_path}")

    engine = SubtitleEngine()
    entries = _parse_subtitle_entries(engine, subtitle_path)
    if not entries:
        raise HTTPException(status_code=400, detail="字幕文件为空或格式无法识别")

    return SubtitleCorrectionResponse(
        message=f"已读取 {len(entries)} 条字幕",
        entries=_entry_payloads(entries),
        plain_text=entries_to_plain_text(entries),
        output_path=subtitle_path,
        format=os.path.splitext(subtitle_path)[1].lower().lstrip("."),
    )


@router.post("/parse-text", response_model=SubtitleCorrectionResponse)
async def parse_subtitle_text(request: SubtitleCorrectionParseTextRequest):
    """解析粘贴的 SRT/VTT 文本，返回可编辑字幕条目"""
    if not request.content.strip():
        raise HTTPException(status_code=400, detail="字幕文本不能为空")

    engine = SubtitleEngine()
    entries = _parse_subtitle_text(engine, request.content, request.format)
    if not entries:
        raise HTTPException(status_code=400, detail="没有解析到有效字幕条目")

    return SubtitleCorrectionResponse(
        message=f"已解析 {len(entries)} 条字幕",
        entries=_entry_payloads(entries),
        plain_text=entries_to_plain_text(entries),
        format=request.format.lower().lstrip("."),
    )


@router.post("/save", response_model=SubtitleCorrectionResponse)
async def save_corrected_subtitle(request: SubtitleCorrectionSaveRequest):
    """保存手动校对后的 SRT 字幕文件"""
    normalized_format = request.format.lower().lstrip(".")
    if normalized_format != "srt":
        raise HTTPException(status_code=400, detail="当前只支持保存 SRT 字幕")

    engine = SubtitleEngine()
    entries = _normalize_correction_entries(engine, request.entries)
    output_path = _safe_subtitle_output_path(request.output_path, request.file_name, "srt", request.source_path)
    engine.save_srt(entries, output_path)

    return SubtitleCorrectionResponse(
        message=f"已保存 {len(entries)} 条字幕",
        entries=_entry_payloads(entries),
        plain_text=entries_to_plain_text(entries),
        output_path=output_path,
        format="srt",
    )


@router.post("/save-ass", response_model=SubtitleCorrectionResponse)
async def save_corrected_ass(request: SubtitleCorrectionSaveAssRequest, db: Session = Depends(get_db)):
    """按当前字幕预设生成 ASS 字幕文件"""
    engine = SubtitleEngine()
    entries = _normalize_correction_entries(engine, request.entries)
    preset = _pick_subtitle_preset(db, request.preset_id)
    preset_dict = _preset_to_dict(preset)
    display_entries = engine.normalize_entries_for_display(entries, preset_dict)
    output_path = _safe_subtitle_output_path(request.output_path, request.file_name, "ass", request.source_path)
    engine.generate_ass(display_entries, output_path, preset_dict)

    return SubtitleCorrectionResponse(
        message=f"已生成 ASS 字幕 {len(display_entries)} 条",
        entries=_entry_payloads(entries),
        plain_text=entries_to_plain_text(entries),
        output_path=output_path,
        format="ass",
    )


@router.post("/render", response_model=SubtitleRenderResponse)
def render_subtitles(request: SubtitleRenderRequest, db: Session = Depends(get_db)):
    """下载或读取字幕，生成 ASS，并可烧录成硬字幕视频"""
    video = db.query(VideoSource).filter(VideoSource.id == request.video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="视频记录不存在")
    if not os.path.exists(request.video_path):
        raise HTTPException(status_code=404, detail="视频文件不存在")

    preset = _pick_subtitle_preset(db, request.preset_id)
    preset_dict = _preset_to_dict(preset)
    language = request.language or preset_dict.get("language") or "en"
    if language == "auto":
        language = "en"

    task = DownloadTask(
        video_id=video.id,
        task_type="subtitle",
        status="processing",
        progress=0,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    try:
        paths = ensure_video_workspace(video.video_id or video.id, video.title or video.video_id)
        subtitle_path = request.subtitle_path
        if not subtitle_path:
            subtitle_path = Downloader().download_subtitle(
                url=video.url,
                language=language,
                output_dir=paths["output_dir"],
                sub_type=request.sub_type,
                control_keys=[f"task:{task.id}"],
            )
        if not subtitle_path or not os.path.exists(subtitle_path):
            raise FileNotFoundError("字幕文件不存在")

        engine = SubtitleEngine()
        entries = _parse_subtitle_entries(engine, subtitle_path)
        if not entries:
            raise RuntimeError("字幕文件为空，无法生成 ASS")

        base_name = os.path.splitext(os.path.basename(request.video_path))[0]
        ass_path = os.path.join(paths["output_dir"], f"{base_name}_{language}.ass")
        display_entries = engine.normalize_entries_for_display(entries, preset_dict)
        engine.generate_ass(display_entries, ass_path, preset_dict)

        output_path = None
        if request.burn_in:
            def on_burn_progress(progress: float) -> None:
                """同步字幕烧录进度"""
                task.progress = max(0.0, min(99.0, progress))
                db.commit()

            output_path = FFmpegProcessor().burn_subtitles(
                video_path=request.video_path,
                subtitle_path=ass_path,
                output_path=request.output_path,
                preset=preset_dict,
                control_keys=[f"task:{task.id}"],
                progress_callback=on_burn_progress,
            )

        plain_text = entries_to_plain_text(entries)
        task.status = "completed"
        task.progress = 100
        task.output_path = output_path or ass_path
        db.commit()
        return SubtitleRenderResponse(
            message="字幕处理完成",
            task_id=task.id,
            subtitle_path=subtitle_path,
            ass_path=ass_path,
            output_path=output_path,
            plain_text=plain_text,
        )
    except TaskControlRequested as exc:
        task.status = "paused" if exc.action == "pause" else "cancelled"
        task.error_message = "用户暂停，等待继续" if exc.action == "pause" else "用户取消"
        db.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        task.status = "failed"
        task.error_message = str(exc)
        db.commit()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
