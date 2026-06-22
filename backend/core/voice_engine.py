# backend/core/voice_engine.py
# 配音引擎 - 调用多家 TTS API 生成配音音频

import base64
import asyncio
import wave
import os
import shutil
import subprocess
import tempfile
from typing import Any, Callable, List, Optional

from ..utils import get_logger
from .paths import ensure_project_dirs
from .tooling import get_ffmpeg_command

# 日志记录器
logger = get_logger("voice")


def resolve_voice_provider_type(provider_type: str, model: str = "") -> str:
    """按用户选择的渠道决定真实调用协议，模型名只作为请求参数传递"""
    return str(provider_type or "").strip()


def provider_audio_format(provider_type: str, settings: dict[str, Any], model: str = "") -> str:
    """按渠道读取音频格式；OpenAI 兼容渠道不根据模型名改协议或格式"""
    normalized_provider = resolve_voice_provider_type(provider_type, model)
    if normalized_provider == "gemini_tts":
        return "wav"
    if normalized_provider == "xiaomi_mimo_tts":
        value = str((settings or {}).get("format") or "wav").lower()
        return value if value in {"wav", "mp3", "pcm", "pcm16"} else "wav"
    value = str((settings or {}).get("format") or "mp3").lower()
    return value if value in {"mp3", "wav", "flac", "pcm", "opus"} else "mp3"


class VoiceEngine:
    """配音引擎"""

    def __init__(self):
        """初始化配音引擎"""
        pass

    async def generate_voice(
        self,
        text: str,
        output_path: Optional[str] = None,
        provider_type: str = "openai_tts",
        voice: str = "alloy",
        voice_selector: Optional[Callable[[dict[str, Any]], str]] = None,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
        settings: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        生成配音音频
        返回输出文件路径
        """
        if not text.strip():
            raise ValueError("文本不能为空")

        options = settings or {}
        effective_provider_type = self.resolve_provider_type(provider_type, model)
        audio_format = self._provider_audio_format(effective_provider_type, options, model)
        if output_path is None:
            output_path = os.path.join(ensure_project_dirs()["output_dir"], f"voice_output.{audio_format}")

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        logger.info(f"生成配音: {effective_provider_type}, 语音: {voice}")
        return await self._generate_voice_with_retry(
            text=text,
            output_path=output_path,
            provider_type=effective_provider_type,
            voice=voice,
            api_key=api_key,
            base_url=base_url,
            model=model,
            settings=options,
        )

    async def _generate_voice_with_retry(
        self,
        text: str,
        output_path: str,
        provider_type: str,
        voice: str,
        api_key: str,
        base_url: str,
        model: str,
        settings: dict[str, Any],
    ) -> str:
        """按配置执行配音请求，网络波动或限流时自动重试"""
        retry_count = max(0, self._int(settings.get("retry_count"), 2))
        retry_interval_ms = max(0, self._int(settings.get("retry_interval_ms"), 1200))
        last_error: Exception | None = None

        for attempt in range(retry_count + 1):
            try:
                result_path = await self._generate_voice_once(text, output_path, provider_type, voice, api_key, base_url, model, settings)
                self._validate_voice_output(result_path)
                return result_path
            except Exception as exc:
                last_error = exc
                if attempt >= retry_count:
                    break
                logger.warning(f"配音生成失败，准备重试: attempt={attempt + 1}/{retry_count}, provider={provider_type}, reason={exc}")
                self._remove_partial_output(output_path)
                if retry_interval_ms > 0:
                    await asyncio.sleep(retry_interval_ms / 1000)

        if last_error:
            raise RuntimeError(f"配音 API 重试次数已用完: {self._exception_message(last_error)}")
        raise RuntimeError("配音 API 调用失败")

    async def _generate_voice_once(
        self,
        text: str,
        output_path: str,
        provider_type: str,
        voice: str,
        api_key: str,
        base_url: str,
        model: str,
        settings: dict[str, Any],
    ) -> str:
        """根据渠道类型执行一次配音请求"""
        if provider_type == "openai_tts":
            return await self._generate_openai_tts(text, output_path, voice, api_key, base_url, model, settings, provider_type)
        if provider_type == "gemini_tts":
            return await self._generate_gemini_tts(text, output_path, voice, api_key, base_url, model, settings)
        if provider_type == "minimax_tts":
            return await self._generate_minimax_tts(text, output_path, voice, api_key, base_url, model, settings)
        if provider_type == "xiaomi_mimo_tts":
            return await self._generate_xiaomi_mimo_tts(text, output_path, voice, api_key, base_url, model, settings)
        if provider_type == "custom_tts":
            return await self._generate_openai_tts(text, output_path, voice, api_key, base_url, model, settings, provider_type)

        raise ValueError(f"不支持的 TTS 提供商: {provider_type}")

    @staticmethod
    def resolve_provider_type(provider_type: str, model: str = "") -> str:
        """按用户选择的渠道决定真实调用协议，模型名只作为请求参数传递"""
        return resolve_voice_provider_type(provider_type, model)

    async def generate_batched_timed_voice_track(
        self,
        segments: list[dict[str, Any]],
        output_path: str,
        provider_type: str = "openai_tts",
        voice: str = "alloy",
        # 多人对话配音时按批次首段的说话人挑选音色；批次会避免跨音色合并。
        voice_selector: Optional[Callable[[dict[str, Any]], str]] = None,
        style_selector: Optional[Callable[[dict[str, Any]], str]] = None,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
        settings: Optional[dict[str, Any]] = None,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> str:
        """并发生成逐条字幕配音，再按字幕时间轴混合成完整音轨"""
        normalized_segments = self._normalize_timed_segments(segments)
        if not normalized_segments:
            raise ValueError("没有可生成配音的字幕分段")

        options = settings or {}
        audio_format = self._provider_audio_format(self.resolve_provider_type(provider_type, model), options, model)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        batch_size = max(1, min(80, self._int(options.get("voice_batch_size"), 16)))
        max_chars = max(100, min(12000, self._int(options.get("voice_batch_chars"), 1800)))
        concurrency = max(1, min(8, self._int(options.get("voice_concurrency"), 2)))
        semaphore = asyncio.Semaphore(concurrency)
        temp_dir = tempfile.mkdtemp(prefix="voice_batches_", dir=os.path.dirname(output_path) or ensure_project_dirs()["output_dir"])
        completed = 0
        timed_audio_paths: list[dict[str, Any]] = []

        async def generate_segment(index: int, segment: dict[str, Any]) -> dict[str, Any]:
            """生成单条字幕音频，保留字幕原始起止时间"""
            async with semaphore:
                segment_path = os.path.join(temp_dir, f"segment_{index:04d}.{audio_format}")
                segment_voice = voice_selector(segment) if voice_selector else voice
                segment_style = style_selector(segment) if style_selector else ""
                await self.generate_voice(
                    text=str(segment["text"]),
                    output_path=segment_path,
                    provider_type=provider_type,
                    voice=str(segment_voice or voice),
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    settings=self._settings_with_style_prompt(options, str(segment_style or "")),
                )
                return {
                    "path": segment_path,
                    "start_ms": int(segment["start_ms"]),
                    "duration_ms": max(1, int(segment["end_ms"]) - int(segment["start_ms"])),
                }

        try:
            index_offset = 0
            for chunk in self._chunk_timed_segments(normalized_segments, batch_size, max_chars):
                tasks = [
                    asyncio.create_task(generate_segment(index_offset + index, segment))
                    for index, segment in enumerate(chunk, 1)
                ]
                for task in asyncio.as_completed(tasks):
                    timed_audio_paths.append(await task)
                    completed += 1
                    if progress_callback:
                        progress_callback(10 + completed / max(len(normalized_segments), 1) * 75)
                index_offset += len(chunk)

            timed_audio_paths.sort(key=lambda item: int(item["start_ms"]))
            result = self.mix_timed_audio_files(timed_audio_paths, output_path)
            if progress_callback:
                progress_callback(95)
            return result
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def generate_grouped_timed_voice_track(
        self,
        segments: list[dict[str, Any]],
        output_path: str,
        provider_type: str = "openai_tts",
        voice: str = "alloy",
        voice_selector: Optional[Callable[[dict[str, Any]], str]] = None,
        style_selector: Optional[Callable[[dict[str, Any]], str]] = None,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
        settings: Optional[dict[str, Any]] = None,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> str:
        """按短时间轴分组合成配音，自动提速或拆分超长分组"""
        normalized_segments = self._normalize_timed_segments(segments)
        if not normalized_segments:
            raise ValueError("没有可生成配音的字幕分段")

        options = settings or {}
        audio_format = self._provider_audio_format(self.resolve_provider_type(provider_type, model), options, model)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        group_size = max(1, min(12, self._int(options.get("voice_group_size"), 6)))
        group_chars = max(80, min(2000, self._int(options.get("voice_group_chars"), 500)))
        group_window_ms = max(1000, min(30000, int(self._float(options.get("voice_group_max_seconds"), 12.0) * 1000)))
        group_gap_ms = max(0, min(5000, self._int(options.get("voice_group_gap_ms"), 800)))
        concurrency = max(1, min(8, self._int(options.get("voice_concurrency"), 2)))
        semaphore = asyncio.Semaphore(concurrency)
        groups = self._group_timed_segments(
            normalized_segments,
            group_size=group_size,
            max_chars=group_chars,
            max_window_ms=group_window_ms,
            max_gap_ms=group_gap_ms,
            voice_selector=voice_selector,
            style_selector=style_selector,
            default_voice=voice,
        )
        if not groups:
            raise ValueError("没有可生成配音的字幕分组")

        temp_dir = tempfile.mkdtemp(prefix="voice_groups_", dir=os.path.dirname(output_path) or ensure_project_dirs()["output_dir"])
        completed = 0
        timed_audio_paths: list[dict[str, Any]] = []

        async def generate_group(index: int, group: dict[str, Any]) -> list[dict[str, Any]]:
            """生成单个分组，必要时在组内自动拆分"""
            async with semaphore:
                return await self._generate_grouped_voice_items(
                    group=group,
                    temp_dir=temp_dir,
                    file_stem=f"group_{index:04d}",
                    audio_format=audio_format,
                    provider_type=provider_type,
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    settings=options,
                )

        try:
            tasks = [asyncio.create_task(generate_group(index, group)) for index, group in enumerate(groups, 1)]
            for task in asyncio.as_completed(tasks):
                timed_audio_paths.extend(await task)
                completed += 1
                if progress_callback:
                    progress_callback(10 + completed / max(len(groups), 1) * 75)

            timed_audio_paths.sort(key=lambda item: int(item["start_ms"]))
            result = self.mix_timed_audio_files(timed_audio_paths, output_path)
            if progress_callback:
                progress_callback(95)
            return result
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def generate_timed_voice_track(
        self,
        segments: list[dict[str, Any]],
        output_path: str,
        provider_type: str = "openai_tts",
        voice: str = "alloy",
        # 多人对话配音时按字幕分段的说话人挑选音色；为 None 时所有分段使用默认音色。
        voice_selector: Optional[Callable[[dict[str, Any]], str]] = None,
        style_selector: Optional[Callable[[dict[str, Any]], str]] = None,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
        settings: Optional[dict[str, Any]] = None,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> str:
        """
        按字幕时间轴生成分段配音，并混合成一个对齐后的完整音轨。
        segments: [{start_ms, end_ms, text}, ...]
        """
        normalized_segments = self._normalize_timed_segments(segments)
        if not normalized_segments:
            raise ValueError("没有可生成配音的字幕分段")

        options = settings or {}
        audio_format = self._provider_audio_format(self.resolve_provider_type(provider_type, model), options, model)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # 分段音频临时目录跟最终输出放在同一视频目录，避免又落回全局 output 里。
        temp_dir = tempfile.mkdtemp(prefix="voice_segments_", dir=os.path.dirname(output_path) or ensure_project_dirs()["output_dir"])
        timed_audio_paths: list[dict[str, Any]] = []
        try:
            total = len(normalized_segments)
            for index, segment in enumerate(normalized_segments, 1):
                segment_path = os.path.join(temp_dir, f"segment_{index:04d}.{audio_format}")
                # 多人对话时按字幕分段里的说话人选择音色；未匹配则使用默认音色。
                segment_voice = voice_selector(segment) if voice_selector else voice
                segment_style = style_selector(segment) if style_selector else ""
                await self.generate_voice(
                    text=str(segment["text"]),
                    output_path=segment_path,
                    provider_type=provider_type,
                    voice=segment_voice or voice,
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    settings=self._settings_with_style_prompt(options, segment_style),
                )
                timed_audio_paths.append({
                    "path": segment_path,
                    "start_ms": int(segment["start_ms"]),
                    "duration_ms": max(1, int(segment["end_ms"]) - int(segment["start_ms"])),
                })
                if progress_callback:
                    progress_callback(10 + index / max(total, 1) * 75)

            result = self.mix_timed_audio_files(timed_audio_paths, output_path)
            if progress_callback:
                progress_callback(95)
            return result
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def mix_timed_audio_files(
        self,
        timed_audio_paths: list[dict[str, Any]],
        output_path: str,
        max_inputs: int = 32,
        fit_to_subtitle_window: Optional[bool] = None,
    ) -> str:
        """把多个带起始时间的音频片段混合成完整时间轴音轨"""
        items = self._normalize_timed_audio_paths(timed_audio_paths)
        if not items:
            raise ValueError("没有音频文件可混合")

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Windows 命令行长度有限，输入过多时先分批混合，再合成总音轨。
        if len(items) > max_inputs:
            chunk_dir = tempfile.mkdtemp(prefix="voice_mix_chunks_", dir=os.path.dirname(output_path))
            try:
                chunk_outputs: list[dict[str, Any]] = []
                for index in range(0, len(items), max_inputs):
                    chunk = items[index:index + max_inputs]
                    chunk_path = os.path.join(chunk_dir, f"chunk_{index // max_inputs:03d}.wav")
                    self.mix_timed_audio_files(chunk, chunk_path, max_inputs=max_inputs, fit_to_subtitle_window=fit_to_subtitle_window)
                    chunk_outputs.append({"path": chunk_path, "start_ms": 0})
                return self.mix_timed_audio_files(chunk_outputs, output_path, max_inputs=max_inputs, fit_to_subtitle_window=fit_to_subtitle_window)
            finally:
                shutil.rmtree(chunk_dir, ignore_errors=True)

        should_fit_window = self._bool_env("YTV_VOICE_TIMELINE_FIT", False) if fit_to_subtitle_window is None else fit_to_subtitle_window
        cmd = [self._ffmpeg_cmd()]
        for item in items:
            cmd.extend(["-i", item["path"]])

        filters: list[str] = []
        labels: list[str] = []
        for index, item in enumerate(items):
            label = f"a{index}"
            delay = max(0, int(item["start_ms"]))
            duration_ms = int(item.get("duration_ms") or 0)
            source_duration_ms = int(item.get("source_duration_ms") or 0)
            filters_for_item = [f"[{index}:a]aresample=44100,asetpts=PTS-STARTPTS"]
            if should_fit_window and duration_ms > 0:
                if source_duration_ms > 0:
                    tempo = source_duration_ms / duration_ms
                    if 0.35 <= tempo <= 2.8 and abs(tempo - 1.0) > 0.03:
                        filters_for_item.append(self._atempo_chain(tempo))
                # 允许一点尾音，避免直接砍掉最后一个字；仍限制到字幕窗口附近，防止长句压住后续字幕。
                duration_seconds = max(0.001, (duration_ms + 160) / 1000)
                filters_for_item.append(f"atrim=0:{duration_seconds:.3f}")
                filters_for_item.append("asetpts=PTS-STARTPTS")
            filters_for_item.append(f"adelay={delay}:all=1")
            filters.append(",".join(part for part in filters_for_item if part) + f"[{label}]")
            labels.append(f"[{label}]")

        if len(labels) == 1:
            filters.append(f"{labels[0]}anull[out]")
        else:
            filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:normalize=0[out]")

        cmd.extend([
            "-filter_complex", ";".join(filters),
            "-map", "[out]",
            "-ac", "2",
            "-ar", "44100",
        ])
        cmd.extend(self._audio_encoder_args(output_path))
        cmd.extend(["-y", output_path])

        logger.info(f"混合分段配音: {len(items)} 段 -> {output_path}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
        )
        if result.returncode != 0:
            raise RuntimeError(f"分段配音混合失败: {result.stderr}")

        logger.info(f"分段配音混合完成: {output_path}")
        return output_path

    async def _generate_openai_tts(
        self,
        text: str,
        output_path: str,
        voice: str,
        api_key: str,
        base_url: str,
        model: str,
        settings: dict[str, Any],
        provider_type: str = "openai_tts",
    ) -> str:
        """使用 OpenAI 兼容 TTS API 生成配音"""
        import httpx

        if not base_url:
            base_url = "https://api.openai.com/v1"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        resolved_model = str(model or "").strip()
        if provider_type == "custom_tts" and not resolved_model:
            raise ValueError("自定义 OpenAI 兼容配音必须填写模型，请先选择或手动输入 NewAPI 中可用的 TTS 模型")

        payload: dict[str, Any] = {
            "model": resolved_model or "gpt-4o-mini-tts",
            "input": text,
            "voice": voice,
            "response_format": self._provider_audio_format(provider_type, settings, model),
            "speed": self._float(settings.get("speed"), 1.0),
        }
        instructions = settings.get("style_prompt")
        if instructions:
            payload["instructions"] = instructions

        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/audio/speech",
                headers=headers,
                json=payload,
            )

            if response.status_code != 200:
                raise RuntimeError(f"OpenAI TTS 调用失败: {self._response_error_message(response)}")
            content_type = response.headers.get("content-type", "")
            if "json" in content_type.lower() or "text/" in content_type.lower():
                raise RuntimeError(f"OpenAI TTS 未返回音频数据: {self._response_error_message(response)}")

            with open(output_path, "wb") as file:
                file.write(response.content)

        logger.info(f"OpenAI TTS 生成完成: {output_path}")
        return output_path

    async def _generate_gemini_tts(
        self,
        text: str,
        output_path: str,
        voice: str,
        api_key: str,
        base_url: str,
        model: str,
        settings: dict[str, Any],
    ) -> str:
        """使用 Gemini TTS API 生成配音"""
        import httpx

        if not base_url:
            base_url = "https://generativelanguage.googleapis.com/v1beta"

        style_prompt = settings.get("style_prompt")
        prompt_text = f"{style_prompt}\n\n{text}" if style_prompt else text
        payload = {
            "contents": [{
                "parts": [{"text": prompt_text}]
            }],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {
                            "voiceName": voice
                        }
                    }
                },
            },
        }

        gemini_model = model or "gemini-2.5-flash-preview-tts"
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/models/{gemini_model}:generateContent?key={api_key}",
                headers={"Content-Type": "application/json"},
                json=payload,
            )

            if response.status_code != 200:
                raise RuntimeError(f"Gemini TTS 调用失败: {self._response_error_message(response)}")

            data = response.json()
            inline_data = data["candidates"][0]["content"]["parts"][0]["inlineData"]
            audio_bytes = base64.b64decode(inline_data["data"])
            self._write_gemini_audio(output_path, audio_bytes, inline_data.get("mimeType"))

        logger.info(f"Gemini TTS 生成完成: {output_path}")
        return output_path

    async def _generate_minimax_tts(
        self,
        text: str,
        output_path: str,
        voice: str,
        api_key: str,
        base_url: str,
        model: str,
        settings: dict[str, Any],
    ) -> str:
        """使用 MiniMax T2A API 生成配音"""
        import httpx

        if not base_url:
            base_url = "https://api.minimax.io/v1"

        payload = {
            "model": model or "speech-2.8-hd",
            "text": text,
            "stream": False,
            "language_boost": settings.get("language_boost") or "auto",
            "output_format": "hex",
            "voice_setting": {
                "voice_id": voice,
                "speed": self._float(settings.get("speed"), 1.0),
                "vol": self._float(settings.get("volume"), 1.0),
                "pitch": self._int(settings.get("pitch"), 0),
                "emotion": settings.get("emotion") or None,
            },
            "audio_setting": {
                "sample_rate": self._int(settings.get("sample_rate"), 32000),
                "bitrate": self._int(settings.get("bitrate"), 128000),
                "format": self._audio_format(settings),
                "channel": self._int(settings.get("channel"), 1),
            },
            "voice_modify": {
                "pitch": self._int(settings.get("voice_pitch", settings.get("pitch")), 0),
                "intensity": self._int(settings.get("intensity"), 0),
                "timbre": self._int(settings.get("timbre"), 0),
            },
        }

        sound_effects = settings.get("sound_effects")
        if sound_effects:
            payload["voice_modify"]["sound_effects"] = sound_effects

        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/t2a_v2",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

            if response.status_code != 200:
                raise RuntimeError(f"MiniMax TTS 调用失败: {self._response_error_message(response)}")

            data = response.json()
            base_resp = data.get("base_resp") or {}
            if base_resp.get("status_code") not in (None, 0):
                raise RuntimeError(f"MiniMax TTS 调用失败: {base_resp.get('status_msg', '未知错误')}")

            audio_hex = (data.get("data") or {}).get("audio")
            if not audio_hex:
                raise RuntimeError("MiniMax TTS 未返回音频数据")

            with open(output_path, "wb") as file:
                file.write(bytes.fromhex(audio_hex))

        logger.info(f"MiniMax TTS 生成完成: {output_path}")
        return output_path

    async def _generate_xiaomi_mimo_tts(
        self,
        text: str,
        output_path: str,
        voice: str,
        api_key: str,
        base_url: str,
        model: str,
        settings: dict[str, Any],
    ) -> str:
        """使用小米 MiMo 语音合成 API 生成配音"""
        import httpx

        if not base_url:
            base_url = "https://api.xiaomimimo.com/v1"

        resolved_model = str(model or "mimo-v2.5-tts").strip()
        style_prompt = str(settings.get("style_prompt") or "请将文本自然地转换为配音音频。").strip()
        audio: dict[str, Any] = {
            "format": self._provider_audio_format("xiaomi_mimo_tts", settings),
        }
        messages = [
            {"role": "user", "content": style_prompt},
            {"role": "assistant", "content": text},
        ]
        if "voicedesign" in resolved_model.lower():
            design_prompt = self._xiaomi_voice_design_prompt(voice, settings)
            messages[0]["content"] = self._join_prompt_parts([design_prompt, style_prompt])
        elif "voiceclone" in resolved_model.lower():
            audio["voice"] = self._xiaomi_voice_clone_data_uri(voice, settings)
        else:
            audio["voice"] = voice or "mimo_default"

        payload = {
            "model": resolved_model,
            "messages": messages,
            "audio": audio,
        }

        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers=self._xiaomi_mimo_headers(api_key),
                json=payload,
            )

            if response.status_code != 200:
                raise RuntimeError(f"小米 MiMo TTS 调用失败: {self._response_error_message(response)}")

            data = response.json()
            message = data["choices"][0]["message"]
            audio_data = message.get("audio") or {}
            encoded = audio_data.get("data")
            if not encoded:
                raise RuntimeError("小米 MiMo TTS 未返回音频数据")

            with open(output_path, "wb") as file:
                file.write(base64.b64decode(encoded))

        logger.info(f"小米 MiMo TTS 生成完成: {output_path}")
        return output_path

    def _xiaomi_voice_design_prompt(self, voice: str, settings: dict[str, Any]) -> str:
        """读取小米文字定制音色提示，优先使用专用字段，其次解析音色预设"""
        normalized_voice = str(voice or "").strip()
        if normalized_voice.startswith("voice_design:"):
            prompt = normalized_voice.split(":", 1)[1].strip()
            if prompt:
                return prompt
        configured = str(settings.get("xiaomi_voice_design_prompt") or "").strip()
        if configured:
            return configured
        if normalized_voice and normalized_voice != "voice_design":
            return normalized_voice
        raise ValueError("小米 VoiceDesign 模型需要填写文字定制音色描述")

    def _xiaomi_voice_clone_data_uri(self, voice: str, settings: dict[str, Any]) -> str:
        """把小米音色克隆样本转换为官方要求的 data:audio/...;base64 格式"""
        data_uri = str(settings.get("xiaomi_voice_clone_audio_data") or "").strip()
        if data_uri.startswith("data:audio/"):
            return data_uri
        voice_value = str(voice or "").strip()
        if voice_value.startswith("data:audio/"):
            return voice_value
        sample_path = ""
        for prefix in ("voice_clone_path:", "voice_clone:"):
            if voice_value.startswith(prefix):
                sample_path = voice_value.split(":", 1)[1].strip()
                break
        if not sample_path and voice_value and os.path.exists(os.path.expanduser(voice_value)):
            sample_path = voice_value

        sample_path = sample_path or str(settings.get("xiaomi_voice_clone_audio_path") or "").strip()
        if not sample_path:
            raise ValueError("小米 VoiceClone 模型需要先上传 mp3 或 wav 参考音频")
        sample_path = os.path.abspath(os.path.expanduser(sample_path))
        if not os.path.exists(sample_path):
            raise FileNotFoundError(f"小米 VoiceClone 参考音频不存在: {sample_path}")

        extension = os.path.splitext(sample_path)[1].lower()
        mime_type = {
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
        }.get(extension)
        if not mime_type:
            raise ValueError("小米 VoiceClone 参考音频只支持 mp3 或 wav")
        size_bytes = os.path.getsize(sample_path)
        if size_bytes > 10 * 1024 * 1024:
            raise ValueError("小米 VoiceClone 参考音频不能超过 10MB")
        with open(sample_path, "rb") as file:
            encoded = base64.b64encode(file.read()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    def _xiaomi_mimo_headers(self, api_key: str) -> dict[str, str]:
        """同时兼容 NewAPI Bearer 鉴权和小米原生 api-key 鉴权"""
        return {
            "Authorization": f"Bearer {api_key}",
            "api-key": api_key,
            "x-api-key": api_key,
            "Content-Type": "application/json",
        }

    def _exception_message(self, exc: Exception) -> str:
        """把空异常转换成可读文本，避免前端只显示空错误"""
        message = str(exc).strip()
        return message or exc.__class__.__name__

    def _response_error_message(self, response: Any) -> str:
        """提取 OpenAI/NewAPI 风格错误信息，减少前端展示整段 JSON"""
        text = str(getattr(response, "text", "") or "").strip()
        try:
            data = response.json()
        except Exception:
            data = None
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or "").strip()
                code = str(error.get("code") or "").strip()
                if message and code:
                    return f"{message}（{code}）"
                if message:
                    return message
            for key in ("message", "detail"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return text[:500] if text else f"HTTP {getattr(response, 'status_code', '未知状态')}"

    def merge_segments(self, audio_paths: List[str], output_path: str) -> str:
        """合并多个音频片段"""
        if not audio_paths:
            raise ValueError("没有音频文件可合并")

        # 创建文件列表
        list_file = output_path + ".list.txt"
        with open(list_file, "w", encoding="utf-8") as file:
            for path in audio_paths:
                file.write(f"file '{path}'\n")

        # 使用 ffmpeg 合并
        cmd = [
            self._ffmpeg_cmd(),
            "-f", "concat",
            "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            "-y",
            output_path,
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )

            if result.returncode != 0:
                raise RuntimeError(f"音频合并失败: {result.stderr}")

            logger.info(f"音频合并完成: {output_path}")
            return output_path

        finally:
            # 清理临时文件
            if os.path.exists(list_file):
                os.remove(list_file)

    def _audio_format(self, settings: dict[str, Any]) -> str:
        """读取音频格式，默认 mp3"""
        value = str(settings.get("format") or "mp3").lower()
        return value if value in {"mp3", "wav", "flac", "pcm", "opus"} else "mp3"

    def _provider_audio_format(self, provider_type: str, settings: dict[str, Any], model: str = "") -> str:
        """按渠道读取音频格式；OpenAI 兼容渠道不根据模型名改协议或格式"""
        return provider_audio_format(provider_type, settings, model)

    def _write_gemini_audio(self, output_path: str, audio_bytes: bytes, mime_type: Optional[str]) -> None:
        """写入 Gemini TTS 音频，裸 PCM 自动封装为 WAV 方便浏览器播放"""
        normalized = (mime_type or "").lower()
        if "pcm" not in normalized and not output_path.lower().endswith(".wav"):
            with open(output_path, "wb") as file:
                file.write(audio_bytes)
            return

        with wave.open(output_path, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(24000)
            wav_file.writeframes(audio_bytes)

    def _normalize_timed_segments(self, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """清理字幕分段，过滤空文本和非法时间"""
        normalized: list[dict[str, Any]] = []
        for segment in segments:
            text = " ".join(str(segment.get("text") or "").replace("\\N", " ").split())
            if not text:
                continue
            start_ms = self._int(segment.get("start_ms"), 0)
            end_ms = self._int(segment.get("end_ms"), start_ms + 1000)
            normalized.append({
                "text": text,
                "speaker": str(segment.get("speaker") or "").strip(),
                "start_ms": max(0, start_ms),
                "end_ms": max(start_ms + 1, end_ms),
            })
        return normalized

    def _chunk_timed_segments(self, segments: list[dict[str, Any]], batch_size: int, max_chars: int) -> list[list[dict[str, Any]]]:
        """把字幕拆成并发调度批次，但不合并文本，避免批次内部时间轴漂移"""
        chunks: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        current_chars = 0
        safe_batch_size = max(1, batch_size)
        safe_max_chars = max(100, max_chars)

        for segment in segments:
            text_len = len(str(segment.get("text") or ""))
            if current and (len(current) >= safe_batch_size or current_chars + text_len > safe_max_chars):
                chunks.append(current)
                current = []
                current_chars = 0
            current.append(segment)
            current_chars += text_len

        if current:
            chunks.append(current)
        return chunks

    def _batch_timed_segments(
        self,
        segments: list[dict[str, Any]],
        batch_size: int,
        max_chars: int,
        voice_selector: Optional[Callable[[dict[str, Any]], str]],
        style_selector: Optional[Callable[[dict[str, Any]], str]],
        default_voice: str,
    ) -> list[dict[str, Any]]:
        """把相邻字幕合并成批次；不同音色或风格不跨批，避免多人对话串音"""
        batches: list[dict[str, Any]] = []
        current: list[dict[str, Any]] = []
        current_voice = ""
        current_style = ""
        current_chars = 0
        safe_batch_size = max(1, batch_size)
        safe_max_chars = max(100, max_chars)

        def flush() -> None:
            """提交当前批次"""
            nonlocal current, current_voice, current_style, current_chars
            if not current:
                return
            batches.append({
                "text": "\n".join(str(item["text"]) for item in current if str(item.get("text") or "").strip()),
                "voice": current_voice or default_voice,
                "style_prompt": current_style,
                "start_ms": int(current[0]["start_ms"]),
                "end_ms": int(current[-1]["end_ms"]),
                "count": len(current),
            })
            current = []
            current_voice = ""
            current_style = ""
            current_chars = 0

        for segment in segments:
            segment_voice = voice_selector(segment) if voice_selector else default_voice
            segment_voice = str(segment_voice or default_voice)
            segment_style = str(style_selector(segment) if style_selector else "").strip()
            text_len = len(str(segment.get("text") or ""))
            should_flush = bool(current) and (
                segment_voice != current_voice
                or segment_style != current_style
                or len(current) >= safe_batch_size
                or current_chars + text_len > safe_max_chars
            )
            if should_flush:
                flush()
            current.append(segment)
            current_voice = segment_voice
            current_style = segment_style
            current_chars += text_len

        flush()
        return [batch for batch in batches if str(batch.get("text") or "").strip()]

    def _group_timed_segments(
        self,
        segments: list[dict[str, Any]],
        group_size: int,
        max_chars: int,
        max_window_ms: int,
        max_gap_ms: int,
        voice_selector: Optional[Callable[[dict[str, Any]], str]],
        style_selector: Optional[Callable[[dict[str, Any]], str]],
        default_voice: str,
    ) -> list[dict[str, Any]]:
        """按时间窗口合并字幕；不同音色、长停顿或过长窗口不合并"""
        groups: list[dict[str, Any]] = []
        current: list[dict[str, Any]] = []
        current_voice = ""
        current_style = ""
        current_chars = 0
        safe_group_size = max(1, group_size)
        safe_max_chars = max(1, max_chars)
        safe_max_window_ms = max(1, max_window_ms)
        safe_max_gap_ms = max(0, max_gap_ms)

        def flush() -> None:
            """提交当前分组"""
            nonlocal current, current_voice, current_style, current_chars
            if not current:
                return
            groups.append(self._build_voice_group(current, current_voice or default_voice, current_style))
            current = []
            current_voice = ""
            current_style = ""
            current_chars = 0

        for segment in segments:
            selected_voice = voice_selector(segment) if voice_selector else default_voice
            segment_voice = str(selected_voice or default_voice).strip() or default_voice
            segment_style = str(style_selector(segment) if style_selector else "").strip()
            text_len = len(str(segment.get("text") or ""))
            next_window_ms = int(segment["end_ms"]) - int(current[0]["start_ms"]) if current else int(segment["end_ms"]) - int(segment["start_ms"])
            gap_ms = int(segment["start_ms"]) - int(current[-1]["end_ms"]) if current else 0
            should_flush = bool(current) and (
                segment_voice != current_voice
                or segment_style != current_style
                or len(current) >= safe_group_size
                or current_chars + text_len > safe_max_chars
                or next_window_ms > safe_max_window_ms
                or gap_ms > safe_max_gap_ms
            )
            if should_flush:
                flush()
            current.append(segment)
            current_voice = segment_voice
            current_style = segment_style
            current_chars += text_len

        flush()
        return [group for group in groups if str(group.get("text") or "").strip()]

    def _build_voice_group(self, segments: list[dict[str, Any]], voice: str, style_prompt: str) -> dict[str, Any]:
        """把字幕分段包装成一次 TTS 分组请求"""
        return {
            "segments": [dict(segment) for segment in segments],
            "text": self._join_voice_group_text(segments),
            "voice": voice,
            "style_prompt": style_prompt,
            "start_ms": int(segments[0]["start_ms"]),
            "end_ms": int(segments[-1]["end_ms"]),
        }

    async def _generate_grouped_voice_items(
        self,
        group: dict[str, Any],
        temp_dir: str,
        file_stem: str,
        audio_format: str,
        provider_type: str,
        api_key: str,
        base_url: str,
        model: str,
        settings: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """生成分组音频；超出窗口时自动提速，仍失败则递归拆分"""
        segments = list(group.get("segments") or [])
        if not segments:
            return []

        max_speed = max(1.0, min(2.0, self._float(settings.get("voice_group_max_speed"), 1.25)))
        retry_margin = max(1.0, min(1.5, self._float(settings.get("voice_group_duration_margin"), 1.12)))
        window_ms = max(1, int(group["end_ms"]) - int(group["start_ms"]))
        speed_values = [1.0]
        if max_speed > 1.0 and self._provider_supports_speed(provider_type):
            speed_values.extend([min(max_speed, 1.12), min(max_speed, 1.25), max_speed])
        speed_values = self._unique_speeds(speed_values)
        last_path = ""
        last_duration_ms = 0

        for attempt, speed in enumerate(speed_values, 1):
            group_path = os.path.join(temp_dir, f"{file_stem}_try{attempt}.{audio_format}")
            group_settings = self._settings_with_group_prompt(
                settings,
                str(group.get("style_prompt") or ""),
                window_ms,
            )
            group_settings = dict(group_settings)
            group_settings["speed"] = speed
            await self.generate_voice(
                text=str(group["text"]),
                output_path=group_path,
                provider_type=provider_type,
                voice=str(group.get("voice") or ""),
                api_key=api_key,
                base_url=base_url,
                model=model,
                settings=group_settings,
            )
            duration_seconds = self._audio_duration_seconds(group_path)
            duration_ms = int((duration_seconds or 0) * 1000)
            last_path = group_path
            last_duration_ms = duration_ms
            if duration_ms <= max(1, int(window_ms * retry_margin)):
                return [{
                    "path": group_path,
                    "start_ms": int(group["start_ms"]),
                    "duration_ms": window_ms,
                    "source_duration_ms": duration_ms,
                }]

        if len(segments) <= 1:
            return [{
                "path": last_path,
                "start_ms": int(group["start_ms"]),
                "duration_ms": window_ms,
                "source_duration_ms": last_duration_ms,
            }]

        middle = max(1, len(segments) // 2)
        left = self._build_voice_group(segments[:middle], str(group.get("voice") or ""), str(group.get("style_prompt") or ""))
        right = self._build_voice_group(segments[middle:], str(group.get("voice") or ""), str(group.get("style_prompt") or ""))
        left_items = await self._generate_grouped_voice_items(
            group=left,
            temp_dir=temp_dir,
            file_stem=f"{file_stem}_a",
            audio_format=audio_format,
            provider_type=provider_type,
            api_key=api_key,
            base_url=base_url,
            model=model,
            settings=settings,
        )
        right_items = await self._generate_grouped_voice_items(
            group=right,
            temp_dir=temp_dir,
            file_stem=f"{file_stem}_b",
            audio_format=audio_format,
            provider_type=provider_type,
            api_key=api_key,
            base_url=base_url,
            model=model,
            settings=settings,
        )
        return left_items + right_items

    def _unique_speeds(self, speeds: list[float]) -> list[float]:
        """清理重复语速，避免同一速度重复请求接口"""
        unique: list[float] = []
        for speed in speeds:
            normalized = round(max(0.5, min(2.0, float(speed))), 3)
            if all(abs(normalized - existing) > 0.01 for existing in unique):
                unique.append(normalized)
        return unique

    def _provider_supports_speed(self, provider_type: str) -> bool:
        """判断渠道是否支持请求级语速；不支持时直接拆分，减少无效重试"""
        normalized = self.resolve_provider_type(provider_type)
        return normalized in {"openai_tts", "custom_tts", "minimax_tts"}

    def _settings_with_style_prompt(self, settings: dict[str, Any], style_prompt: str) -> dict[str, Any]:
        """按分段覆盖风格提示；没有角色风格时复用全局设置"""
        normalized_style = str(style_prompt or "").strip()
        if not normalized_style:
            return settings
        merged = dict(settings)
        merged["style_prompt"] = normalized_style
        return merged

    def _settings_with_group_prompt(self, settings: dict[str, Any], style_prompt: str, window_ms: int) -> dict[str, Any]:
        """给分组配音追加自然连贯朗读提示，减少逐行换气和机械停顿"""
        target_seconds = max(0.1, window_ms / 1000.0)
        timing_prompt = (
            f"请自然连贯朗读，不要在每句字幕之间刻意停顿、换气或拖长尾音。"
            f"整段目标时长约 {target_seconds:.1f} 秒，语速由你自然判断，尽量贴合这个时长。"
        )
        base_style = str(style_prompt or settings.get("style_prompt") or "").strip()
        merged = dict(settings)
        merged["style_prompt"] = self._join_prompt_parts([base_style, timing_prompt])
        return merged

    def _join_prompt_parts(self, parts: list[str]) -> str:
        """合并多段提示词，过滤空值"""
        return "\n".join(part.strip() for part in parts if str(part or "").strip())

    def _join_voice_group_text(self, segments: list[dict[str, Any]]) -> str:
        """把同一时间窗内的字幕合成自然朗读文本，避免换行触发强停顿"""
        texts = [str(item.get("text") or "").strip() for item in segments]
        texts = [text for text in texts if text]
        if not texts:
            return ""
        result = texts[0]
        end_punctuation = set("。！？!?；;：:，,、")
        for text in texts[1:]:
            if result[-1] in end_punctuation:
                result += text
            else:
                result += "，" + text
        return result

    def _normalize_timed_audio_paths(self, timed_audio_paths: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """清理带时间轴的音频路径"""
        items: list[dict[str, Any]] = []
        for item in timed_audio_paths:
            path = str(item.get("path") or "")
            if not path or not os.path.exists(path):
                continue
            source_duration = self._float(item.get("source_duration_ms"), 0)
            if source_duration <= 0:
                duration_seconds = self._audio_duration_seconds(path)
                source_duration = duration_seconds * 1000 if duration_seconds else 0
            items.append({
                "path": path,
                "start_ms": max(0, self._int(item.get("start_ms"), 0)),
                "duration_ms": max(0, self._int(item.get("duration_ms"), 0)),
                "source_duration_ms": max(0, int(source_duration)),
            })
        return items

    def _atempo_chain(self, factor: float) -> str:
        """把变速倍率拆成 FFmpeg atempo 支持的稳定链路"""
        if factor <= 0:
            return ""
        parts: list[str] = []
        remaining = factor
        while remaining > 2.0:
            parts.append("atempo=2.000")
            remaining /= 2.0
        while remaining < 0.5:
            parts.append("atempo=0.500")
            remaining /= 0.5
        parts.append(f"atempo={remaining:.3f}")
        return ",".join(parts)

    def _audio_encoder_args(self, output_path: str) -> list[str]:
        """根据输出扩展名选择稳定的音频编码参数"""
        ext = os.path.splitext(output_path)[1].lower().lstrip(".")
        if ext == "mp3":
            return ["-c:a", "libmp3lame", "-q:a", "2"]
        if ext in {"wav", "pcm"}:
            return ["-c:a", "pcm_s16le"]
        if ext == "flac":
            return ["-c:a", "flac"]
        if ext == "opus":
            return ["-c:a", "libopus", "-b:a", "128k"]
        return ["-c:a", "aac", "-b:a", "192k"]

    def _validate_voice_output(self, output_path: str) -> None:
        """确认配音产物是真实可用音频，避免极短静音文件被误判为成功"""
        if not output_path or not os.path.isfile(output_path):
            raise RuntimeError("配音 API 未生成音频文件")
        size = os.path.getsize(output_path)
        if size < 1024:
            raise RuntimeError(f"配音音频文件过小: {size} bytes")
        if self._looks_like_json_or_text(output_path):
            raise RuntimeError("配音接口返回的不是可播放音频")
        duration = self._audio_duration_seconds(output_path)
        if duration is None:
            raise RuntimeError("配音音频无法读取时长，可能不是浏览器可播放格式")
        if duration < 0.12:
            raise RuntimeError(f"配音音频时长异常: {duration:.2f}s")
        if self._is_probably_silent_wav(output_path):
            raise RuntimeError("配音音频为空或接近静音，接口可能没有真正生成语音")

    def _looks_like_json_or_text(self, output_path: str) -> bool:
        """粗略识别 API 把错误 JSON/文本写成音频文件的情况"""
        try:
            with open(output_path, "rb") as file:
                head = file.read(64).lstrip()
        except OSError:
            return False
        return head.startswith((b"{", b"[", b"<")) or head[:16].lower().startswith((b"error", b"invalid"))

    def _audio_duration_seconds(self, output_path: str) -> Optional[float]:
        """优先用 ffprobe 读取音频时长，失败时对 WAV 做轻量兜底解析"""
        ffprobe = self._ffprobe_cmd()
        try:
            result = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    output_path,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
                return float(result.stdout.strip().splitlines()[0])
        except Exception:
            pass

        if os.path.splitext(output_path)[1].lower() != ".wav":
            return None
        try:
            with wave.open(output_path, "rb") as wav_file:
                rate = wav_file.getframerate()
                return wav_file.getnframes() / rate if rate else None
        except Exception:
            return None

    def _is_probably_silent_wav(self, output_path: str) -> bool:
        """检测 WAV 是否全静音，避免 0:00 或空白试听被误判为成功"""
        if os.path.splitext(output_path)[1].lower() != ".wav":
            return False
        try:
            with wave.open(output_path, "rb") as wav_file:
                sample_width = wav_file.getsampwidth()
                frame_rate = wav_file.getframerate() or 24000
                frame_count = min(wav_file.getnframes(), max(frame_rate * 3, 1))
                audio_data = wav_file.readframes(frame_count)
        except Exception:
            return False

        if not audio_data:
            return True
        if all(byte == 0 for byte in audio_data):
            return True
        if sample_width != 2:
            return False

        max_amplitude = 0
        usable_length = len(audio_data) - (len(audio_data) % 2)
        for index in range(0, usable_length, 2):
            sample = int.from_bytes(audio_data[index:index + 2], "little", signed=True)
            max_amplitude = max(max_amplitude, abs(sample))
            if max_amplitude > 8:
                return False
        return True

    def _remove_partial_output(self, output_path: str) -> None:
        """重试前清理上一次失败留下的半成品音频"""
        try:
            if output_path and os.path.exists(output_path):
                os.remove(output_path)
        except OSError:
            pass

    def _ffprobe_cmd(self) -> str:
        """获取 ffprobe 可执行文件路径"""
        ffmpeg = self._ffmpeg_cmd()
        exe_name = "ffprobe.exe" if os.name == "nt" else "ffprobe"
        bundled = os.path.join(os.path.dirname(ffmpeg), exe_name)
        if os.path.exists(bundled):
            return bundled
        return "ffprobe"

    def _ffmpeg_cmd(self) -> str:
        """获取 ffmpeg 可执行文件路径"""
        return get_ffmpeg_command()

    def _float(self, value: Any, default: float) -> float:
        """安全读取浮点数"""
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _int(self, value: Any, default: int) -> int:
        """安全读取整数"""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _bool_env(self, name: str, default: bool) -> bool:
        """读取布尔环境变量，用于保留少数兼容开关"""
        value = os.environ.get(name)
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
