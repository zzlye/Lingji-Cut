# backend/core/audio_transcriber.py
# Gemini 音频字幕识别 - 用多模态大模型直接转写音频，召回率和准确度高于本地 Whisper
# 复用 LocalSpeechRecognizer 的音频预处理、VAD 语音区间和时间码工具，只替换"识别"这一环

import asyncio
import base64
import difflib
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

# Gemini 时间戳偶尔会把多句话挤在同一个瞬间；补漏字幕必须有可读显示时长。
MIN_MISSING_GAP_SECONDS = 0.75
MIN_MISSING_SUBTITLE_SECONDS = 0.85

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

        self._emit_progress(progress_callback, 2)
        logger.info(f"Gemini 识别音频预处理开始: model={self.model}")
        audio_path, temp_audio = self._asr._prepare_audio(video_path)
        try:
            self._emit_progress(progress_callback, 8)
            logger.info("Gemini 识别音频预处理完成，开始解码")
            audio = self._asr._decode_audio_array(audio_path)
            if audio is None:
                raise RuntimeError("Gemini 识别无法解码音频")
            duration = len(audio) / 16000.0
            self._emit_progress(progress_callback, 12)
            logger.info(f"Gemini 识别音频解码完成: duration={duration:.1f}s")
            regions = self._asr._compute_vad_regions(audio)
            self._emit_progress(progress_callback, 16)
            logger.info(f"Gemini 识别 VAD 完成: {len(regions)} 个语音区间")
            segments = self._plan_segments(regions, duration, self._segment_seconds())
            if not segments:
                segments = [(0.0, duration)]
            self._emit_progress(progress_callback, 20)
            logger.info(f"Gemini 识别音频分段规划完成: {len(segments)} 段")
            seg_files = self._export_segment_files(audio_path, segments, progress_callback)
            if not seg_files:
                raise RuntimeError("Gemini 识别音频分段失败")
            self._emit_progress(progress_callback, 30)
            logger.info(f"Gemini 识别音频分段导出完成: {len(seg_files)} 段")
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

    def _emit_progress(self, progress_callback: Optional[Callable[[float], None]], value: float) -> None:
        """安全发送 Gemini 识别进度，避免进度回调异常打断识别主流程"""
        if not progress_callback:
            return
        progress_callback(max(0.0, min(100.0, float(value))))

    def _plan_segments(self, regions: list[tuple[float, float]], duration: float, max_len: float) -> list[tuple[float, float]]:
        """把 VAD 语音区间贪心合并成不超过 max_len 的分段，切点落在静音间隙避免切断句子"""
        if self._full_coverage_segments_enabled():
            return self._plan_full_coverage_segments(regions, duration, max_len)
        return self._plan_vad_segments(regions, duration, max_len)

    def _plan_vad_segments(self, regions: list[tuple[float, float]], duration: float, max_len: float) -> list[tuple[float, float]]:
        """旧版 VAD 分段：只发送检测到的人声区间，可用环境变量切回"""
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

    def _plan_full_coverage_segments(self, regions: list[tuple[float, float]], duration: float, max_len: float) -> list[tuple[float, float]]:
        """默认覆盖整段音频切片，避免 VAD 漏掉细声细语后 Gemini 根本听不到"""
        safe_duration = max(0.0, float(duration))
        safe_max_len = max(20.0, float(max_len))
        if safe_duration <= 0.0:
            return []
        if safe_duration <= safe_max_len:
            return [(0.0, safe_duration)]

        cut_candidates: list[float] = []
        ordered_regions = sorted((max(0.0, start), min(safe_duration, end)) for start, end in regions if end > start)
        for left, right in zip(ordered_regions, ordered_regions[1:]):
            gap_start = left[1]
            gap_end = right[0]
            if gap_end - gap_start >= 0.3:
                cut_candidates.append((gap_start + gap_end) / 2.0)

        segments: list[tuple[float, float]] = []
        start = 0.0
        overlap = min(1.0, max(0.0, self._env_float("YTV_GEMINI_ASR_SEGMENT_OVERLAP_S", 0.3, 0.0, 3.0)))
        min_piece = min(20.0, safe_max_len * 0.4)
        while start < safe_duration - 0.05:
            max_end = min(safe_duration, start + safe_max_len)
            if max_end >= safe_duration:
                end = safe_duration
            else:
                usable_cuts = [cut for cut in cut_candidates if start + min_piece <= cut <= max_end]
                end = max(usable_cuts) if usable_cuts else max_end
            if end <= start + 0.2:
                end = min(safe_duration, start + safe_max_len)
            segments.append((start, end))
            if end >= safe_duration:
                break
            next_start = max(0.0, end - overlap)
            start = end if next_start <= start + 0.1 else next_start
        return segments

    def _export_segment_files(
        self,
        audio_path: str,
        segments: list[tuple[float, float]],
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> list[tuple[int, float, str]]:
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
            self._emit_progress(progress_callback, min(29.0, 20.0 + (index + 1) / max(1, len(segments)) * 9.0))
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
            failures: list[str] = []

            async def run_one(slot: int, seg_start: float, mp3_path: str) -> None:
                nonlocal done
                async with semaphore:
                    try:
                        results[slot] = await self._transcribe_one_segment(client, seg_start, mp3_path, language)
                    except Exception as exc:
                        logger.warning(f"Gemini 识别分段(起点 {seg_start:.1f}s)失败: {exc}")
                        failures.append(f"{seg_start:.1f}s: {exc}")
                        results[slot] = []
                    finally:
                        done += 1
                        if progress_callback:
                            progress_callback(min(95.0, 30.0 + done / max(1, total) * 65.0))

            await asyncio.gather(*(run_one(slot, seg_start, path) for slot, (_, seg_start, path) in enumerate(seg_files)))

        if failures:
            sample = "；".join(failures[:3])
            suffix = f"；另有 {len(failures) - 3} 段失败" if len(failures) > 3 else ""
            raise RuntimeError(f"Gemini 识别有 {len(failures)} 个音频分段失败，已停止避免漏字幕: {sample}{suffix}")

        entries: list[dict] = []
        for chunk in results:
            entries.extend(chunk)
        # 相邻分段为防切断句子各留了 0.3 秒重叠，边界处同一句话会被两段都转写出来，
        # 必须在对齐前去重，否则对齐时一份算"替换"一份算"补漏"，仍会落进相邻字幕造成重复。
        return self._dedupe_boundary_repeats(entries)

    def _dedupe_boundary_repeats(self, entries: list[dict]) -> list[dict]:
        """裁掉相邻分段在重叠边界重复转写的同一段话，避免对齐后出现重复字幕"""
        if len(entries) < 2:
            return entries
        ordered = sorted(
            entries,
            key=lambda e: self._asr._srt_time_to_seconds(str(e.get("start") or "00:00:00,000")),
        )
        result: list[dict] = []
        for entry in ordered:
            drop = False
            # 重复只发生在分段重叠边界，只和时间相近的前几条比较即可
            for prev in reversed(result[-3:]):
                prev_start = self._asr._srt_time_to_seconds(str(prev.get("start") or "00:00:00,000"))
                cur_start = self._asr._srt_time_to_seconds(str(entry.get("start") or "00:00:00,000"))
                if cur_start - prev_start > 6.0:
                    continue
                outcome = self._strip_boundary_overlap(prev, entry)
                if outcome is None:
                    drop = True
                    break
                entry = outcome
            if not drop and str(entry.get("text") or "").strip():
                result.append(entry)
        return result

    def _strip_boundary_overlap(self, prev: dict, entry: dict) -> Optional[dict]:
        """比较相邻两条字幕，去掉边界重复词；返回 None 表示整条都是重复应丢弃，原样返回表示无重复"""
        prev_units = _sentence_units(" ".join(str(prev.get("text") or "").split()))
        cur_units = _sentence_units(" ".join(str(entry.get("text") or "").split()))
        # 要求至少 4 个连续词重合才判定为重复，避免误删 "No No" 这类正常短重复
        if len(prev_units) < 4 or len(cur_units) < 1:
            return entry
        prev_norm = [unit.lower() for unit in prev_units]
        cur_norm = [unit.lower() for unit in cur_units]
        # entry 整句就是 prev 的一段连续子串（含完全相同）→ 整条丢弃
        if len(cur_norm) >= 4 and len(cur_norm) <= len(prev_norm) and self._contains_run(prev_norm, cur_norm):
            return None
        # prev 尾部和 entry 头部的最长重叠（边界缝合处的重复词）
        max_overlap = min(len(prev_norm), len(cur_norm))
        for length in range(max_overlap, 3, -1):
            if prev_norm[-length:] == cur_norm[:length]:
                kept_units = cur_units[length:]
                if not kept_units:
                    return None
                new_entry = dict(entry)
                new_entry["text"] = _join_sentence_units(kept_units)
                return new_entry
        return entry

    def _contains_run(self, haystack: list[str], needle: list[str]) -> bool:
        """判断 needle 是否作为连续子序列出现在 haystack 中"""
        n, m = len(haystack), len(needle)
        if m == 0 or m > n:
            return False
        for start in range(n - m + 1):
            if haystack[start:start + m] == needle:
                return True
        return False

    async def _transcribe_one_segment(self, client: Any, seg_start: float, mp3_path: str, language: Optional[str]) -> list[dict]:
        """转写单个分段，带失败重试，返回平移回完整时间轴的字幕条目"""
        with open(mp3_path, "rb") as audio_file:
            audio_b64 = base64.b64encode(audio_file.read()).decode("ascii")
        retry = max(0, self._int(self.settings.get("retry_count"), 1))
        last_error: Optional[Exception] = None
        for attempt in range(retry + 1):
            try:
                text = await self._call_audio_once(client, audio_b64, language)
                entries = self._parse_segment_response(text, seg_start)
                if not entries and not self._is_explicit_empty_response(text):
                    raise RuntimeError("模型没有返回可解析的字幕 JSON")
                return entries
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

    def _is_explicit_empty_response(self, text: str) -> bool:
        """判断模型是否明确返回空数组，区别于返回乱码导致解析为空"""
        cleaned = str(text or "").strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned).strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return False
        return data == [] or (isinstance(data, dict) and data.get("items") == [])

    def _segment_seconds(self) -> float:
        """单段目标时长，太长大模型时间戳易漂移，太短拖慢且丢上下文"""
        return self._asr._env_float("YTV_GEMINI_ASR_SEGMENT_S", 90.0, 20.0, 300.0)

    def _full_coverage_segments_enabled(self) -> bool:
        """Gemini 内容识别默认覆盖整段音频，避免低音量人声被 VAD 过滤掉"""
        configured = str(os.environ.get("YTV_GEMINI_ASR_FULL_COVERAGE") or "true").strip().lower()
        return configured not in {"0", "false", "off", "no"}

    def _env_float(self, key: str, default: float, minimum: float, maximum: float) -> float:
        """读取浮点环境变量并限制范围"""
        try:
            value = float(os.environ.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

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
    """方案3：以 Whisper 精确时间轴为骨架，用 Gemini 的准确内容按"文本序列对齐"分配

    Whisper 和 Gemini 是同一段语音的两次转写，文本高度相似：Whisper 时间准但词可能错，
    Gemini 词准但时间粗。旧做法按"时间重叠 + 字数比例硬切长句"分配，会把一句话切成
    0.2 秒的碎片、相邻条目时间错位、内容重复。这里改成用 difflib 把 Gemini 的每个词
    按"和 Whisper 词的对应关系"落到对应条目的精确时间槽，从根本上消除碎片和错位。

    - equal/replace：Gemini 词按文本对齐落到对应 Whisper 条目，时间沿用该条目精确边界；
    - insert（Whisper 漏的词）：落在某条目时间区间内就并入该条目，否则收集为补漏；
    - delete（Whisper 多出/听错的词）：直接忽略；
    - 分不到任何 Gemini 词的条目保留原 Whisper 文本兜底，不留空。
    """
    if not gemini_entries:
        return whisper_entries
    if not whisper_entries:
        return gemini_entries

    # 构造带"所属条目下标"的 Whisper 词序列（difflib 的 a 序列，用归一化小写词比对）
    w_words: list[tuple[str, int]] = []
    for entry_index, entry in enumerate(whisper_entries):
        for unit in _sentence_units(" ".join(str(entry.get("text") or "").split())):
            w_words.append((unit.lower(), entry_index))

    # 构造带"插值时间 + 所属句起止"的 Gemini 词序列（difflib 的 b 序列）
    # 插值时间用于判断漏词落在哪条字幕；句起止用于补漏时还原原句时间边界
    g_words: list[tuple[str, str, float, float, float]] = []
    for g in gemini_entries:
        units = _sentence_units(" ".join(str(g.get("text") or "").split()))
        if not units:
            continue
        g_start = srt_to_seconds(str(g.get("start") or "00:00:00,000"))
        g_end = srt_to_seconds(str(g.get("end") or "00:00:00,000"))
        span = max(0.0, g_end - g_start)
        count = len(units)
        for offset, unit in enumerate(units):
            # 词中心时间 = 句起点 + 句时长 * (词序+0.5)/词数
            word_time = g_start + span * (offset + 0.5) / count
            g_words.append((unit.lower(), unit, word_time, g_start, g_end))

    if not g_words:
        return whisper_entries
    if not w_words:
        # Whisper 没有可用文本时无法对齐，退回 Gemini 自身内容和时间
        return gemini_entries

    whisper_spans = [
        (
            srt_to_seconds(str(entry.get("start") or "00:00:00,000")),
            srt_to_seconds(str(entry.get("end") or "00:00:00,000")),
            entry,
        )
        for entry in whisper_entries
    ]
    # 每个 Whisper 条目收集分到的 (Gemini 词全局序号, 原始词)，按序号排序即还原 Gemini 语序
    assigned: list[list[tuple[int, str]]] = [[] for _ in whisper_entries]
    g_assigned = [False] * len(g_words)

    # autojunk=False：关闭"高频词当噪声"启发式，避免 the/a 等常见词被忽略破坏对齐
    matcher = difflib.SequenceMatcher(None, [w[0] for w in w_words], [g[0] for g in g_words], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            # 完全对齐：Gemini 词逐一落到对齐位置的 Whisper 词所属条目
            for offset in range(i2 - i1):
                entry_index = w_words[i1 + offset][1]
                assigned[entry_index].append((j1 + offset, g_words[j1 + offset][1]))
                g_assigned[j1 + offset] = True
        elif tag == "replace":
            # 词不同但同段：Gemini 词按比例映射到这段 Whisper 词，落到对应条目
            # 用 Whisper 词位置（时间准）做基准，更符合真实说话节奏
            span_w = i2 - i1
            span_g = j2 - j1
            for offset in range(span_g):
                local = min(span_w - 1, int(offset * span_w / span_g))
                entry_index = w_words[i1 + local][1]
                assigned[entry_index].append((j1 + offset, g_words[j1 + offset][1]))
                g_assigned[j1 + offset] = True
        elif tag == "insert":
            # Whisper 漏掉的词：能落进某条字幕时间区间就并入，否则留给补漏
            for index in range(j1, j2):
                word_time = g_words[index][2]
                entry_index = _entry_index_containing_time(whisper_spans, word_time)
                if entry_index is not None:
                    assigned[entry_index].append((index, g_words[index][1]))
                    g_assigned[index] = True
        # delete：Whisper 多出来的词（多为听错或幻觉），不影响 Gemini 内容，忽略

    aligned: list[dict] = []
    for entry_index, entry in enumerate(whisper_entries):
        next_entry = dict(entry)
        words = assigned[entry_index]
        if words:
            words.sort(key=lambda item: item[0])
            next_entry["text"] = _join_sentence_units([word for _index, word in words])
        # 没分到任何 Gemini 词的条目保留原 Whisper 文本兜底，避免留空
        aligned.append(next_entry)

    # 把没分配出去的 Gemini 词按连续段还原成补漏句（用原句时间边界，不用插值时间）
    missing = _collect_missing_runs(g_words, g_assigned)
    aligned.extend(_build_missing_gemini_entries(missing, whisper_spans, seconds_to_srt))

    aligned.sort(key=lambda item: srt_to_seconds(str(item.get("start") or "00:00:00,000")))
    # 收尾平滑：消除从 Whisper 时间轴继承来的极短闪现碎片和时间重叠/倒退
    aligned = _smooth_aligned_entries(aligned, srt_to_seconds, seconds_to_srt)
    for index, entry in enumerate(aligned, 1):
        entry["index"] = index
    return aligned


def _smooth_aligned_entries(
    entries: list[dict],
    srt_to_seconds: Callable[[str], float],
    seconds_to_srt: Callable[[float], str],
) -> list[dict]:
    """对齐结果收尾平滑：合并继承自时间轴的极短碎片、修正相邻条目时间重叠或倒退

    对齐沿用 Whisper 的精确时间，但 Whisper 时间轴本身可能带 0.2 秒的单词碎片和
    边界重叠（来自补漏合并），原样输出会闪现/音画错位。这里做两步收尾：
    1) 时长过短且与相邻条目几乎连续的碎片，并入相邻条目；
    2) 保证相邻条目时间不重叠、不倒退（前一条结束不晚于后一条开始）。
    """
    if len(entries) < 2:
        return entries

    # 时长低于该值视为闪现碎片，与相邻条目间隔小于 MAX_GAP 时合并
    min_duration = 0.35
    max_gap = 0.12
    merged = [dict(entry) for entry in entries]
    index = 0
    while index < len(merged):
        current = merged[index]
        start = srt_to_seconds(str(current.get("start") or "00:00:00,000"))
        end = srt_to_seconds(str(current.get("end") or "00:00:00,000"))
        if end - start >= min_duration:
            index += 1
            continue
        prev_entry = merged[index - 1] if index > 0 else None
        next_entry = merged[index + 1] if index + 1 < len(merged) else None
        prev_gap = (start - srt_to_seconds(str(prev_entry.get("end") or "00:00:00,000"))) if prev_entry else 1e9
        next_gap = (srt_to_seconds(str(next_entry.get("start") or "00:00:00,000")) - end) if next_entry else 1e9
        # 优先并回前一条（短碎片多是前句没收完的尾音），其次并入后一条
        if prev_entry is not None and prev_gap <= max_gap and (next_gap > max_gap or prev_gap <= next_gap):
            prev_entry["end"] = current["end"]
            prev_entry["text"] = _join_sentences([str(prev_entry.get("text") or ""), str(current.get("text") or "")])
            merged.pop(index)
            continue
        if next_entry is not None and next_gap <= max_gap:
            next_entry["start"] = current["start"]
            next_entry["text"] = _join_sentences([str(current.get("text") or ""), str(next_entry.get("text") or "")])
            merged.pop(index)
            continue
        index += 1

    # 拉伸仍然过短的孤立碎片：被停顿隔开、无法合并的短句（如 0.2 秒的 "man army"），
    # 向后借用空闲间隙延长显示时长到可读，必要时再向前借，避免一闪而过来不及看
    readable_min = 0.85
    min_gap_between = 0.04
    for idx, entry in enumerate(merged):
        start = srt_to_seconds(str(entry.get("start") or "00:00:00,000"))
        end = srt_to_seconds(str(entry.get("end") or "00:00:00,000"))
        if end - start >= readable_min:
            continue
        # 向后延长：吃掉到下一条起点前的空闲（留一点间隙），但不超过可读上限
        next_start = srt_to_seconds(str(merged[idx + 1].get("start") or "00:00:00,000")) if idx + 1 < len(merged) else end + readable_min
        new_end = min(start + readable_min, max(end, next_start - min_gap_between))
        if new_end > end:
            end = new_end
            entry["end"] = seconds_to_srt(end)
        if end - start >= readable_min:
            continue
        # 仍不够则向前借：把起点提前到上一条结束之后一点
        prev_end = srt_to_seconds(str(merged[idx - 1].get("end") or "00:00:00,000")) if idx > 0 else 0.0
        new_start = max(prev_end + min_gap_between, end - readable_min)
        if new_start < start:
            entry["start"] = seconds_to_srt(new_start)

    # 修正时间重叠/倒退：前一条结束时间不晚于后一条开始时间
    for idx in range(len(merged) - 1):
        cur_start = srt_to_seconds(str(merged[idx].get("start") or "00:00:00,000"))
        cur_end = srt_to_seconds(str(merged[idx].get("end") or "00:00:00,000"))
        next_start = srt_to_seconds(str(merged[idx + 1].get("start") or "00:00:00,000"))
        if cur_end > next_start:
            # 收回到后一条起点，但不短于自身起点，避免负时长
            merged[idx]["end"] = seconds_to_srt(max(cur_start, next_start))
    return merged


def _entry_index_containing_time(whisper_spans: list[tuple[float, float, dict]], moment: float) -> Optional[int]:
    """找出时间点落在哪个 Whisper 条目区间内，找不到返回 None"""
    for index, (w_start, w_end, _entry) in enumerate(whisper_spans):
        if w_start <= moment <= w_end:
            return index
    return None


def _collect_missing_runs(
    g_words: list[tuple[str, str, float, float, float]],
    g_assigned: list[bool],
) -> list[tuple[float, float, str]]:
    """把连续未分配的 Gemini 词合并成补漏句，时间用原句起止边界（避免逐词碎片）"""
    runs: list[tuple[float, float, str]] = []
    index = 0
    total = len(g_words)
    while index < total:
        if g_assigned[index]:
            index += 1
            continue
        end = index
        while end < total and not g_assigned[end]:
            end += 1
        run = g_words[index:end]
        # 这段词可能跨多个原句，起止取其中最早的句起点和最晚的句终点
        run_start = min(word[3] for word in run)
        run_end = max(word[4] for word in run)
        text = _join_sentence_units([word[1] for word in run])
        if text:
            runs.append((run_start, max(run_end, run_start), text))
        index = end
    return runs


def _build_missing_gemini_entries(
    missing: list[tuple[float, float, str]],
    whisper_spans: list[tuple[float, float, dict]],
    seconds_to_srt: Callable[[float], str],
) -> list[dict]:
    """把 Whisper 漏掉的 Gemini 内容分配到真实空档，避免 0.2 秒密集闪字幕"""
    if not missing:
        return []

    grouped: dict[tuple[float, float], list[tuple[float, float, str]]] = {}
    for g_start, g_end, g_text in sorted(missing, key=lambda item: (item[0], item[1])):
        gap = _find_missing_gap(g_start, g_end, whisper_spans)
        if not gap:
            continue
        gap_start, gap_end = gap
        if gap_end - gap_start < MIN_MISSING_GAP_SECONDS:
            continue
        grouped.setdefault((round(gap_start, 3), round(gap_end, 3)), []).append((g_start, g_end, g_text))

    entries: list[dict] = []
    for (gap_start, gap_end), items in sorted(grouped.items(), key=lambda item: item[0][0]):
        gap_duration = max(0.0, gap_end - gap_start)
        max_chunks = max(1, int(gap_duration // MIN_MISSING_SUBTITLE_SECONDS))
        chunks = _merge_missing_texts([text for _start, _end, text in items], max_chunks)
        entries.extend(_timed_missing_chunks(chunks, gap_start, gap_end, seconds_to_srt))
    return entries


def _find_missing_gap(
    g_start: float,
    g_end: float,
    whisper_spans: list[tuple[float, float, dict]],
) -> Optional[tuple[float, float]]:
    """找到 Gemini 补漏内容所在的本地识别空档"""
    if not whisper_spans:
        end = max(g_end, g_start + MIN_MISSING_SUBTITLE_SECONDS)
        return max(0.0, g_start), end

    previous_end = 0.0
    for w_start, w_end, _entry in whisper_spans:
        if w_end <= g_start:
            previous_end = max(previous_end, w_end)
            continue
        if w_start >= g_start:
            return previous_end, w_start
        # 理论上有重叠时不会进 missing；这里兜底跳过，避免和本地时间轴打架。
        return None

    end = max(g_end, g_start + MIN_MISSING_SUBTITLE_SECONDS)
    return max(previous_end, g_start), end


def _merge_missing_texts(texts: list[str], max_chunks: int) -> list[str]:
    """把同一空档里过多的 Gemini 补漏句合并成可读数量"""
    cleaned = [" ".join(str(text or "").split()) for text in texts if str(text or "").strip()]
    if not cleaned:
        return []
    safe_max = max(1, max_chunks)
    if len(cleaned) <= safe_max:
        return cleaned

    chunks: list[str] = []
    start = 0
    for index in range(safe_max):
        remaining_slots = safe_max - index - 1
        target = round(len(cleaned) * (index + 1) / safe_max)
        end = len(cleaned) if index == safe_max - 1 else min(len(cleaned) - remaining_slots, max(start + 1, target))
        piece = _join_sentences(cleaned[start:end])
        if piece:
            chunks.append(piece)
        start = end
    return chunks


def _timed_missing_chunks(
    chunks: list[str],
    gap_start: float,
    gap_end: float,
    seconds_to_srt: Callable[[float], str],
) -> list[dict]:
    """按文本长度把补漏字幕均匀放进本地识别空档"""
    if not chunks:
        return []
    duration = max(0.0, gap_end - gap_start)
    if duration < MIN_MISSING_GAP_SECONDS:
        return []

    weights = [max(1, len(chunk)) for chunk in chunks]
    total_weight = max(1, sum(weights))
    entries: list[dict] = []
    cursor = gap_start
    elapsed = 0
    for index, chunk in enumerate(chunks):
        elapsed += weights[index]
        if index == len(chunks) - 1:
            end = gap_end
        else:
            end = gap_start + duration * elapsed / total_weight
            min_end = cursor + MIN_MISSING_SUBTITLE_SECONDS
            max_end = gap_end - MIN_MISSING_SUBTITLE_SECONDS * (len(chunks) - index - 1)
            end = max(min_end, min(max_end, end))
        if end - cursor < 0.2:
            continue
        entries.append({
            "index": 0,
            "start": seconds_to_srt(cursor),
            "end": seconds_to_srt(end),
            "text": chunk,
        })
        cursor = end
    return entries


def _sentence_units(text: str) -> list[str]:
    """生成可切分文本单元：英文按词，连续中日韩文本按字"""
    if re.search(r"[A-Za-z0-9]", text) and re.search(r"\s", text):
        return [unit for unit in text.split(" ") if unit]
    units = re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*|[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]|[^\s]", text)
    return units or [text]


def _join_sentence_units(units: list[str]) -> str:
    """把切分单元拼回字幕文本，中文不加空格，英文单词之间保留空格"""
    text = ""
    for unit in [str(item or "").strip() for item in units if str(item or "").strip()]:
        if not text:
            text = unit
            continue
        separator = " " if re.search(r"[A-Za-z0-9]$", text) and re.match(r"^[A-Za-z0-9]", unit) else ""
        text = f"{text}{separator}{unit}"
    return text


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
