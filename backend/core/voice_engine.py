# backend/core/voice_engine.py
# 配音引擎 - 调用 TTS API 生成配音音频

import os
from typing import Optional, List
from ..utils import get_logger

# 日志记录器
logger = get_logger("voice")

# 输出目录
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)


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
        api_key: str = "",
        base_url: str = "",
    ) -> str:
        """
        生成配音音频
        返回输出文件路径
        """
        if not text.strip():
            raise ValueError("文本不能为空")

        if output_path is None:
            output_path = os.path.join(OUTPUT_DIR, "voice_output.mp3")

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        logger.info(f"生成配音: {provider_type}, 语音: {voice}")

        if provider_type == "openai_tts":
            return await self._generate_openai_tts(text, output_path, voice, api_key, base_url)
        elif provider_type == "gemini_tts":
            return await self._generate_gemini_tts(text, output_path, voice, api_key, base_url)
        else:
            raise ValueError(f"不支持的 TTS 提供商: {provider_type}")

    async def _generate_openai_tts(
        self,
        text: str,
        output_path: str,
        voice: str,
        api_key: str,
        base_url: str
    ) -> str:
        """使用 OpenAI TTS API 生成配音"""
        import httpx

        if not base_url:
            base_url = "https://api.openai.com/v1"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": "tts-1",
            "input": text,
            "voice": voice,
            "response_format": "mp3"
        }

        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{base_url}/audio/speech",
                headers=headers,
                json=payload
            )

            if response.status_code != 200:
                raise RuntimeError(f"OpenAI TTS 调用失败: {response.text}")

            # 保存音频文件
            with open(output_path, "wb") as f:
                f.write(response.content)

        logger.info(f"OpenAI TTS 生成完成: {output_path}")
        return output_path

    async def _generate_gemini_tts(
        self,
        text: str,
        output_path: str,
        voice: str,
        api_key: str,
        base_url: str
    ) -> str:
        """使用 Gemini TTS API 生成配音"""
        import httpx

        if not base_url:
            base_url = "https://generativelanguage.googleapis.com/v1beta"

        headers = {
            "Content-Type": "application/json"
        }

        payload = {
            "contents": [{
                "parts": [{"text": text}]
            }],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {
                            "voiceName": voice
                        }
                    }
                }
            }
        }

        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{base_url}/models/gemini-2.5-flash-preview-tts:generateContent?key={api_key}",
                headers=headers,
                json=payload
            )

            if response.status_code != 200:
                raise RuntimeError(f"Gemini TTS 调用失败: {response.text}")

            data = response.json()
            # 解析音频数据
            audio_data = data["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]

            import base64
            audio_bytes = base64.b64decode(audio_data)

            with open(output_path, "wb") as f:
                f.write(audio_bytes)

        logger.info(f"Gemini TTS 生成完成: {output_path}")
        return output_path

    def merge_segments(self, audio_paths: List[str], output_path: str) -> str:
        """合并多个音频片段"""
        import subprocess

        if not audio_paths:
            raise ValueError("没有音频文件可合并")

        # 创建文件列表
        list_file = output_path + ".list.txt"
        with open(list_file, "w", encoding="utf-8") as f:
            for path in audio_paths:
                f.write(f"file '{path}'\n")

        # 使用 ffmpeg 合并
        cmd = [
            "ffmpeg",
            "-f", "concat",
            "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            "-y",
            output_path
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120
            )

            if result.returncode != 0:
                raise RuntimeError(f"音频合并失败: {result.stderr}")

            logger.info(f"音频合并完成: {output_path}")
            return output_path

        finally:
            # 清理临时文件
            if os.path.exists(list_file):
                os.remove(list_file)
