# backend/core/voice_engine.py
# 配音引擎 - 调用多家 TTS API 生成配音音频

import base64
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
        audio_format = self._provider_audio_format(provider_type, options)
        if output_path is None:
            output_path = os.path.join(ensure_project_dirs()["output_dir"], f"voice_output.{audio_format}")

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        logger.info(f"生成配音: {provider_type}, 语音: {voice}")

        if provider_type == "openai_tts":
            return await self._generate_openai_tts(text, output_path, voice, api_key, base_url, model, options)
        if provider_type == "gemini_tts":
            return await self._generate_gemini_tts(text, output_path, voice, api_key, base_url, model, options)
        if provider_type == "minimax_tts":
            return await self._generate_minimax_tts(text, output_path, voice, api_key, base_url, model, options)
        if provider_type == "xiaomi_mimo_tts":
            return await self._generate_xiaomi_mimo_tts(text, output_path, voice, api_key, base_url, model, options)
        if provider_type == "custom_tts":
            return await self._generate_openai_tts(text, output_path, voice, api_key, base_url, model, options)

        raise ValueError(f"不支持的 TTS 提供商: {provider_type}")

    async def generate_timed_voice_track(
        self,
        segments: list[dict[str, Any]],
        output_path: str,
        provider_type: str = "openai_tts",
        voice: str = "alloy",
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
        audio_format = self._provider_audio_format(provider_type, options)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # 分段音频属于临时素材，最终会保留对齐后的完整音轨。
        temp_dir = tempfile.mkdtemp(prefix="voice_segments_", dir=ensure_project_dirs()["output_dir"])
        timed_audio_paths: list[dict[str, Any]] = []
        try:
            total = len(normalized_segments)
            for index, segment in enumerate(normalized_segments, 1):
                segment_path = os.path.join(temp_dir, f"segment_{index:04d}.{audio_format}")
                # 多人对话时按字幕分段里的说话人选择音色；未匹配则使用默认音色。
                segment_voice = voice_selector(segment) if voice_selector else voice
                await self.generate_voice(
                    text=str(segment["text"]),
                    output_path=segment_path,
                    provider_type=provider_type,
                    voice=segment_voice or voice,
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    settings=options,
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
                    self.mix_timed_audio_files(chunk, chunk_path, max_inputs=max_inputs)
                    chunk_outputs.append({"path": chunk_path, "start_ms": 0})
                return self.mix_timed_audio_files(chunk_outputs, output_path, max_inputs=max_inputs)
            finally:
                shutil.rmtree(chunk_dir, ignore_errors=True)

        cmd = [self._ffmpeg_cmd()]
        for item in items:
            cmd.extend(["-i", item["path"]])

        filters: list[str] = []
        labels: list[str] = []
        for index, item in enumerate(items):
            label = f"a{index}"
            delay = max(0, int(item["start_ms"]))
            duration_ms = int(item.get("duration_ms") or 0)
            if duration_ms > 0:
                duration_seconds = max(0.001, duration_ms / 1000)
                filters.append(f"[{index}:a]aresample=44100,atrim=0:{duration_seconds:.3f},asetpts=PTS-STARTPTS,adelay={delay}:all=1[{label}]")
            else:
                filters.append(f"[{index}:a]aresample=44100,adelay={delay}:all=1[{label}]")
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
    ) -> str:
        """使用 OpenAI 兼容 TTS API 生成配音"""
        import httpx

        if not base_url:
            base_url = "https://api.openai.com/v1"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        payload: dict[str, Any] = {
            "model": model or "gpt-4o-mini-tts",
            "input": text,
            "voice": voice,
            "response_format": self._audio_format(settings),
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
                raise RuntimeError(f"OpenAI TTS 调用失败: {response.text}")

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
                raise RuntimeError(f"Gemini TTS 调用失败: {response.text}")

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
                raise RuntimeError(f"MiniMax TTS 调用失败: {response.text}")

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

        style_prompt = settings.get("style_prompt") or "请将文本自然地转换为配音音频。"
        payload = {
            "model": model or "mimo-v2-tts",
            "modalities": ["text", "audio"],
            "audio": {
                "voice": voice,
                "format": self._provider_audio_format("xiaomi_mimo_tts", settings),
            },
            "messages": [
                {"role": "user", "content": style_prompt},
                {"role": "assistant", "content": text},
            ],
        }

        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={
                    "api-key": api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
            )

            if response.status_code != 200:
                raise RuntimeError(f"小米 MiMo TTS 调用失败: {response.text}")

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

    def _provider_audio_format(self, provider_type: str, settings: dict[str, Any]) -> str:
        """按渠道读取音频格式"""
        if provider_type == "gemini_tts":
            return "wav"
        if provider_type == "xiaomi_mimo_tts":
            value = str(settings.get("format") or "wav").lower()
            return value if value in {"wav", "pcm16"} else "wav"
        return self._audio_format(settings)

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

    def _normalize_timed_audio_paths(self, timed_audio_paths: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """清理带时间轴的音频路径"""
        items: list[dict[str, Any]] = []
        for item in timed_audio_paths:
            path = str(item.get("path") or "")
            if not path or not os.path.exists(path):
                continue
            items.append({
                "path": path,
                "start_ms": max(0, self._int(item.get("start_ms"), 0)),
                "duration_ms": max(0, self._int(item.get("duration_ms"), 0)),
            })
        return items

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
