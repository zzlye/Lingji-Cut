# backend/core/audio_transcriber.py
# Gemini 音频字幕识别 - 用多模态大模型直接转写音频，召回率和准确度高于本地 Whisper
# 复用 LocalSpeechRecognizer 的音频预处理、VAD 语音区间和时间码工具，只替换"识别"这一环

import asyncio
import base64
import json
import os
import re
import subprocess
import time
from typing import Any, Callable, Optional

from .local_asr import LocalSpeechRecognizer
from .tooling import get_ffmpeg_command
from ..utils import get_logger


logger = get_logger("audio_transcriber")

# 要求模型逐句输出带段内相对秒数的 JSON，便于拼回完整时间轴
_TRANSCRIBE_PROMPT = (
    "这是一段视频音频。请把里面所有说话内容逐句转写成原始语言的文字，保持原文不要翻译，"
    "包括轻声、语气词、笑声里的话也要尽量转写出来。\n"
    "按时间顺序输出一个 JSON 数组，每个元素形如 {\"start\": 起始秒, \"end\": 结束秒, \"text\": \"该句原文\"}，"
    "start/end 是该句在这段音频内的相对秒数（从 0 开始，可带小数）。\n"
    "只输出 JSON 数组本身，不要 markdown 代码块，不要任何解释。确实没有任何说话内容时才输出 []。"
)


class GeminiAudioTranscriber:
    """用 Gemini 多模态接口转写音频生成带时间轴字幕，复用本地识别器的音频/VAD 工具"""

    def __init__(
        self,
        provider_type: str,
        api_key: str,
        base_url: str,
        model: str,
        settings: Optional[dict[str, Any]] = None,
    ):
        """记录 API 渠道配置，识别专用模型可用环境变量覆盖"""
        self.provider_type = (provider_type or "openai_compatible").strip().lower()
        self.api_key = api_key or ""
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.model = (os.environ.get("YTV_GEMINI_ASR_MODEL") or model or "gemini-2.5-pro").strip()
        self.settings = settings or {}
        # 复用本地识别器的音频预处理、VAD 区间、时间码换算和边界校准，避免重复实现
        self._asr = LocalSpeechRecognizer()

    def transcribe_video(
        self,
        video_path: str,
        language: Optional[str] = None,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> tuple[list[dict], str]:
        """分段调用 Gemini 转写整段音频，返回字幕条目和语言（与本地识别器结构一致）"""
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Gemini 识别视频不存在: {video_path}")

        audio_path, temp_audio = self._asr._prepare_audio(video_path)
        try:
            if progress_callback:
                progress_callback(5)
            audio = self._asr._decode_audio_array(audio_path)
            if audio is None:
                raise RuntimeError("Gemini 识别无法解码音频")
            duration = len(audio) / 16000.0
            regions = self._asr._compute_vad_regions(audio)
            segments = self._plan_segments(regions, duration, self._segment_seconds())
            if not segments:
                segments = [(0.0, duration)]
            seg_files = self._export_segment_files(audio_path, segments)
            if not seg_files:
                raise RuntimeError("Gemini 识别音频分段失败")
            try:
                entries = asyncio.run(self._transcribe_segments(seg_files, language, progress_callback))
            finally:
                for _, _, path in seg_files:
                    self._safe_remove(path)
            if not entries:
                raise RuntimeError("Gemini 识别没有返回字幕内容")
            entries.sort(key=lambda item: self._asr._srt_time_to_seconds(str(item.get("start") or "00:00:00,000")))
            # 用 VAD 语音边界把秒级时间戳吸附到精确边界，弥补大模型时间戳偏粗
            self._asr._calibrate_entries_with_vad(entries, regions)
            for index, entry in enumerate(entries, 1):
                entry["index"] = index
            if progress_callback:
                progress_callback(100)
            detected_language = str(language or "auto")
            logger.info(f"Gemini 识别完成: {len(entries)} 条字幕, model={self.model}, 分段 {len(seg_files)}")
            return entries, detected_language
        finally:
            if temp_audio:
                self._safe_remove(temp_audio)

    def _plan_segments(self, regions: list[tuple[float, float]], duration: float, max_len: float) -> list[tuple[float, float]]:
        """把 VAD 语音区间贪心合并成不超过 max_len 的分段，切点落在静音间隙避免切断句子"""
        if not regions:
            return []
        segments: list[tuple[float, float]] = []
        seg_start, seg_end = regions[0]
        for region_start, region_end in regions[1:]:
            # 合并后跨度仍在上限内就继续并入当前段，否则在静音处断开
            if region_end - seg_start <= max_len:
                seg_end = region_end
            else:
                segments.append((seg_start, seg_end))
                seg_start, seg_end = region_start, region_end
        segments.append((seg_start, seg_end))
        # 段首尾各留 0.3 秒余量，避免边界把首字尾字切掉
        padded: list[tuple[float, float]] = []
        for start, end in segments:
            padded.append((max(0.0, start - 0.3), min(duration, end + 0.3)))
        return padded

    def _export_segment_files(self, audio_path: str, segments: list[tuple[float, float]]) -> list[tuple[int, float, str]]:
        """用 ffmpeg 把每个分段从预处理音频切成 mp3，返回(序号, 段起点秒, 文件路径)"""
        seg_files: list[tuple[int, float, str]] = []
        for index, (start, end) in enumerate(segments):
            if end - start < 0.2:
                continue
            out_path = os.path.join(
                os.path.dirname(audio_path) or ".",
                f"ytv_gemini_seg_{os.getpid()}_{index}.mp3",
            )
            result = subprocess.run(
                [
                    get_ffmpeg_command(), "-y",
                    "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
                    "-i", audio_path,
                    "-ac", "1", "-ar", "16000",
                    "-c:a", "libmp3lame", "-b:a", "64k",
                    out_path,
                ],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=300, check=False,
            )
            if result.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 64:
                seg_files.append((index, start, out_path))
            else:
                logger.warning(f"Gemini 识别分段 {start:.1f}-{end:.1f}s 切割失败，已跳过")
                self._safe_remove(out_path)
        return seg_files

    async def _transcribe_segments(
        self,
        seg_files: list[tuple[int, float, str]],
        language: Optional[str],
        progress_callback: Optional[Callable[[float], None]],
    ) -> list[dict]:
        """并发把各分段发给 Gemini 转写，保持时间顺序合并"""
        import httpx

        concurrency = self._concurrency()
        semaphore = asyncio.Semaphore(concurrency)
        results: list[list[dict]] = [[] for _ in seg_files]
        done = 0
        total = len(seg_files)

        async with httpx.AsyncClient(timeout=self._timeout()) as client:
            async def run_one(slot: int, seg_start: float, mp3_path: str) -> None:
                nonlocal done
                async with semaphore:
                    try:
                        results[slot] = await self._transcribe_one_segment(client, seg_start, mp3_path, language)
                    except Exception as exc:
                        logger.warning(f"Gemini 识别分段(起点 {seg_start:.1f}s)失败: {exc}")
                        results[slot] = []
                    finally:
                        done += 1
                        if progress_callback:
                            progress_callback(min(95.0, 5.0 + done / max(1, total) * 90.0))

            await asyncio.gather(*(run_one(slot, seg_start, path) for slot, (_, seg_start, path) in enumerate(seg_files)))

        entries: list[dict] = []
        for chunk in results:
            entries.extend(chunk)
        return entries

    async def _transcribe_one_segment(self, client: Any, seg_start: float, mp3_path: str, language: Optional[str]) -> list[dict]:
        """转写单个分段，带失败重试，返回平移回完整时间轴的字幕条目"""
        with open(mp3_path, "rb") as audio_file:
            audio_b64 = base64.b64encode(audio_file.read()).decode("ascii")
        retry = max(0, self._int(self.settings.get("retry_count"), 1))
        last_error: Optional[Exception] = None
        for attempt in range(retry + 1):
            try:
                text = await self._call_audio_once(client, audio_b64, language)
                return self._parse_segment_response(text, seg_start)
            except Exception as exc:
                last_error = exc
                if attempt < retry:
                    await asyncio.sleep(1.0)
        # 带上异常类型名，避免 httpx 超时等异常 str 为空导致错误信息看不出原因
        detail = f"{type(last_error).__name__}: {last_error}" if last_error else "未知错误"
        raise RuntimeError(f"Gemini 音频识别失败({detail})")

    async def _call_audio_once(self, client: Any, audio_b64: str, language: Optional[str]) -> str:
        """根据渠道类型发一次音频转写请求，返回模型输出文本"""
        prompt = _TRANSCRIBE_PROMPT
        if language:
            prompt = f"音频语言是 {language}。{prompt}"
        return await self._call_audio_prompt(client, audio_b64, prompt)

    async def _call_audio_prompt(self, client: Any, audio_b64: str, prompt: str) -> str:
        """用指定提示词调用音频多模态模型，供字幕整理等非固定转写任务复用"""
        if self.provider_type in {"gemini", "gemini_compatible"}:
            return await self._call_gemini_native(client, audio_b64, prompt)
        return await self._call_openai_audio(client, audio_b64, prompt)

    async def organize_audio_file(
        self,
        audio_or_video_path: str,
        prompt: str,
        start_offset: float = 0.0,
        max_end_seconds: Optional[float] = None,
    ) -> list[dict]:
        """把一段音频交给 API 模型全权整理字幕，返回已平移到原视频时间轴的条目"""
        if not os.path.exists(audio_or_video_path):
            raise FileNotFoundError(f"AI 整理音频不存在: {audio_or_video_path}")
        audio_path, temp_audio = self._asr._prepare_audio(audio_or_video_path)
        seg_files: list[tuple[int, float, str]] = []
        try:
            audio = self._asr._decode_audio_array(audio_path)
            if audio is None:
                raise RuntimeError("AI 整理无法解码音频")
            duration = len(audio) / 16000.0
            seg_files = self._export_segment_files(audio_path, [(0.0, duration)])
            if not seg_files:
                raise RuntimeError("AI 整理音频分段失败")
            _, _, mp3_path = seg_files[0]
            with open(mp3_path, "rb") as audio_file:
                audio_b64 = base64.b64encode(audio_file.read()).decode("ascii")
            import httpx

            async with httpx.AsyncClient(timeout=self._timeout()) as client:
                response_text = await self._call_audio_prompt(client, audio_b64, prompt)
            entries = self._parse_segment_response(response_text, start_offset)
            if max_end_seconds is not None:
                entries = self._clamp_entries(entries, max_end_seconds)
            if not entries:
                raise RuntimeError("AI 模型没有返回可用字幕")
            entries.sort(key=lambda item: self._asr._srt_time_to_seconds(str(item.get("start") or "00:00:00,000")))
            for index, entry in enumerate(entries, 1):
                entry["index"] = index
            return entries
        finally:
            for _, _, path in seg_files:
                self._safe_remove(path)
            if temp_audio:
                self._safe_remove(temp_audio)

    async def _call_openai_audio(self, client: Any, audio_b64: str, prompt: str) -> str:
        """OpenAI 兼容 chat/completions 多模态音频请求（中转转发 Gemini 走这条）"""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        # 实测该类中转只认 image_url 形式的多模态 data URI，input_audio 字段会被静默丢弃导致模型编造，
        # 因此音频也走 image_url + data:audio/mpeg 传入。
        audio_part = self._audio_content_part(audio_b64)
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                audio_part,
            ]}],
            "temperature": 0,
            "stream": False,
        }
        response = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
        if response.status_code != 200:
            raise RuntimeError(f"Gemini 音频识别失败: HTTP {response.status_code} {response.text[:200]}")
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("Gemini 音频识别未返回 choices")
        content = (choices[0].get("message") or {}).get("content")
        if isinstance(content, list):
            return "".join(str(item.get("text", "")) for item in content if isinstance(item, dict)).strip()
        return str(content or "").strip()

    def _audio_content_part(self, audio_b64: str) -> dict:
        """构造音频多模态片段；默认走 image_url data URI（实测此类中转只认这种，input_audio 字段会被静默丢弃导致模型编造），可用环境变量切回 input_audio"""
        fmt = (os.environ.get("YTV_GEMINI_ASR_AUDIO_FORMAT") or "image_url").strip().lower()
        if fmt == "input_audio":
            return {"type": "input_audio", "input_audio": {"data": audio_b64, "format": "mp3"}}
        return {"type": "image_url", "image_url": {"url": f"data:audio/mpeg;base64,{audio_b64}"}}

    async def _call_gemini_native(self, client: Any, audio_b64: str, prompt: str) -> str:
        """Gemini 原生 generateContent 音频请求"""
        payload = {
            "contents": [{"parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "audio/mp3", "data": audio_b64}},
            ]}],
            "generationConfig": {"temperature": 0},
        }
        response = await client.post(
            f"{self.base_url}/models/{self.model}:generateContent",
            params={"key": self.api_key} if self.api_key else None,
            headers={"Content-Type": "application/json"},
            json=payload,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Gemini 音频识别失败: HTTP {response.status_code} {response.text[:200]}")
        data = response.json()
        candidates = data.get("candidates") or []
        if not candidates:
            raise RuntimeError("Gemini 音频识别未返回 candidates")
        parts = ((candidates[0].get("content") or {}).get("parts") or [])
        return "".join(str(part.get("text", "")) for part in parts if isinstance(part, dict)).strip()

    def _parse_segment_response(self, text: str, seg_start: float) -> list[dict]:
        """解析模型返回的 JSON 句子数组，加段偏移转成完整时间轴字幕条目"""
        items = self._extract_json_items(text)
        entries: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            sentence = self._normalize_subtitle_text(item.get("text"))
            if not sentence:
                continue
            rel_start = self._float(item.get("start"), 0.0)
            rel_end = self._float(item.get("end"), rel_start)
            abs_start = max(0.0, seg_start + rel_start)
            abs_end = max(abs_start + 0.2, seg_start + rel_end)
            entries.append({
                "index": 0,
                "start": self._asr._seconds_to_srt_time(abs_start),
                "end": self._asr._seconds_to_srt_time(abs_end),
                "text": sentence,
            })
        return entries

    def _normalize_subtitle_text(self, value: Any) -> str:
        """清理模型返回字幕文本，保留双语字幕可能需要的换行"""
        lines = [line.strip() for line in str(value or "").replace("\\N", "\n").splitlines() if line.strip()]
        if lines:
            return "\n".join(lines)
        return " ".join(str(value or "").split())

    def _clamp_entries(self, entries: list[dict], max_end_seconds: float) -> list[dict]:
        """把模型时间轴限制在当前片段内，避免模型输出越界时间码"""
        clamped: list[dict] = []
        for entry in entries:
            start = self._asr._srt_time_to_seconds(str(entry.get("start") or "00:00:00,000"))
            end = self._asr._srt_time_to_seconds(str(entry.get("end") or entry.get("start") or "00:00:00,000"))
            if start >= max_end_seconds:
                continue
            next_end = min(max_end_seconds, max(start + 0.2, end))
            next_entry = dict(entry)
            next_entry["start"] = self._asr._seconds_to_srt_time(start)
            next_entry["end"] = self._asr._seconds_to_srt_time(next_end)
            clamped.append(next_entry)
        return clamped

    def _extract_json_items(self, text: str) -> list[Any]:
        """从模型输出里抽出 JSON 数组，容忍 markdown 代码块和前后多余文字"""
        cleaned = str(text or "").strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned).strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\[.*\]", cleaned, re.DOTALL)
            if not match:
                return []
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return []
        if isinstance(data, dict):
            data = data.get("items") or data.get("segments") or data.get("result") or []
        return data if isinstance(data, list) else []

    def _segment_seconds(self) -> float:
        """单段目标时长，太长大模型时间戳易漂移，太短拖慢且丢上下文"""
        return self._asr._env_float("YTV_GEMINI_ASR_SEGMENT_S", 90.0, 20.0, 300.0)

    def _concurrency(self) -> int:
        """并发分段数，中转转发音频较慢且并发大易超时，默认保守"""
        configured = self._int(os.environ.get("YTV_GEMINI_ASR_CONCURRENCY"), self._int(self.settings.get("concurrency"), 2))
        return max(1, min(8, configured))

    def _timeout(self) -> float:
        """单段请求超时；中转转发 Gemini 音频实测约 1.5 倍实时，给足余量"""
        return float(os.environ.get("YTV_GEMINI_ASR_TIMEOUT") or self.settings.get("audio_timeout_seconds") or 300)

    def _safe_remove(self, path: Optional[str]) -> None:
        """静默删除临时文件"""
        if not path:
            return
        try:
            os.remove(path)
        except OSError:
            pass

    def _float(self, value: Any, default: float) -> float:
        """安全转换浮点"""
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _int(self, value: Any, default: int) -> int:
        """安全转换整数"""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default


def align_gemini_content_to_whisper_timeline(
    whisper_entries: list[dict],
    gemini_entries: list[dict],
    srt_to_seconds: Callable[[str], float],
    seconds_to_srt: Callable[[float], str],
) -> list[dict]:
    """方案3：以 Whisper 精确时间轴为骨架，用 Gemini 的准确内容按时间重叠替换/补全

    - Whisper 每条字幕的时间区间内，取时间上重叠的 Gemini 句子文本拼接，替换该条文本；
    - Gemini 有、但没落进任何 Whisper 区间的句子（Whisper 漏掉的），按其时间戳作为新条目插入。
    """
    if not gemini_entries:
        return whisper_entries
    if not whisper_entries:
        return gemini_entries

    gemini_spans = [
        (
            srt_to_seconds(str(g.get("start") or "00:00:00,000")),
            srt_to_seconds(str(g.get("end") or "00:00:00,000")),
            " ".join(str(g.get("text") or "").split()),
        )
        for g in gemini_entries
    ]
    used = [False] * len(gemini_spans)
    aligned: list[dict] = []

    for entry in whisper_entries:
        w_start = srt_to_seconds(str(entry.get("start") or "00:00:00,000"))
        w_end = srt_to_seconds(str(entry.get("end") or "00:00:00,000"))
        picked: list[tuple[float, str]] = []
        for index, (g_start, g_end, g_text) in enumerate(gemini_spans):
            if not g_text:
                continue
            overlap = min(w_end, g_end) - max(w_start, g_start)
            # 句子中心落在该 Whisper 区间内，或与之有实质重叠，就归到这条
            center = (g_start + g_end) / 2
            if overlap > 0.05 or (w_start <= center <= w_end):
                picked.append((g_start, g_text))
                used[index] = True
        next_entry = dict(entry)
        if picked:
            picked.sort(key=lambda item: item[0])
            next_entry["text"] = _join_sentences(text for _, text in picked)
        aligned.append(next_entry)

    # Whisper 时间轴没覆盖到的 Gemini 句子作为补漏条目插入
    for index, (g_start, g_end, g_text) in enumerate(gemini_spans):
        if used[index] or not g_text:
            continue
        aligned.append({
            "index": 0,
            "start": seconds_to_srt(g_start),
            "end": seconds_to_srt(max(g_start + 0.2, g_end)),
            "text": g_text,
        })

    aligned.sort(key=lambda item: srt_to_seconds(str(item.get("start") or "00:00:00,000")))
    for index, entry in enumerate(aligned, 1):
        entry["index"] = index
    return aligned


def _join_sentences(sentences: Any) -> str:
    """拼接多句文本，中日韩之间不加空格，含拉丁字母时用空格分隔"""
    text = ""
    for raw in sentences:
        piece = " ".join(str(raw or "").split())
        if not piece:
            continue
        if not text:
            text = piece
            continue
        separator = " " if re.search(r"[A-Za-z0-9]$", text) and re.match(r"^[A-Za-z0-9]", piece) else ""
        text = f"{text}{separator}{piece}"
    return text
