# backend/core/text_engine.py
# 文本模型引擎 - 调用 OpenAI、Gemini、Anthropic 和兼容渠道处理字幕文本

from typing import Any

from ..utils import get_logger


logger = get_logger("text")


class TextEngine:
    """文本模型调用封装"""

    async def process_text(
        self,
        text: str,
        provider_type: str,
        api_key: str,
        base_url: str,
        model: str,
        settings: dict[str, Any] | None = None,
        operation: str = "polish",
        target_language: str = "",
    ) -> str:
        """根据渠道类型处理字幕文本"""
        if not text.strip():
            raise ValueError("文本不能为空")

        options = settings or {}
        prompt = self._build_prompt(text, operation, target_language, options)
        logger.info(f"调用文本模型处理字幕: {provider_type}, operation={operation}")

        if provider_type in {"openai", "openai_compatible", "custom", "minimax", "xiaomi_mimo"}:
            return await self._call_openai_compatible(prompt, provider_type, api_key, base_url, model, options)
        if provider_type in {"gemini", "gemini_compatible"}:
            return await self._call_gemini(prompt, api_key, base_url, model, options)
        if provider_type == "anthropic":
            return await self._call_anthropic(prompt, api_key, base_url, model, options)

        raise ValueError(f"不支持的文本 API 渠道: {provider_type}")

    def _build_prompt(self, text: str, operation: str, target_language: str, settings: dict[str, Any]) -> str:
        """生成字幕处理提示词"""
        system_prompt = settings.get("system_prompt") or "你是专业短视频字幕处理助手，请保持含义准确、语言自然、适合口播。"
        operation_map = {
            "generate": "请根据以下视频字幕或文本生成一份适合短视频硬字幕和配音的字幕文案。",
            "translate": f"请将以下字幕翻译成{target_language or '目标语言'}，保留原意，表达自然。",
            "polish": "请润色以下字幕，使其更适合短视频观看和口播，不要添加无关内容。",
        }
        instruction = operation_map.get(operation, operation_map["polish"])
        return f"{system_prompt}\n\n{instruction}\n\n要求：只输出处理后的字幕正文，不要输出解释。\n\n原文：\n{text}"

    async def _call_openai_compatible(
        self,
        prompt: str,
        provider_type: str,
        api_key: str,
        base_url: str,
        model: str,
        settings: dict[str, Any],
    ) -> str:
        """调用 OpenAI Chat Completions 兼容接口"""
        import httpx

        if not base_url:
            base_url = "https://api.openai.com/v1"
        selected_model = model or settings.get("model") or "gpt-4.1-mini"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload: dict[str, Any] = {
            "model": selected_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self._float(settings.get("temperature"), 0.7),
            "top_p": self._float(settings.get("top_p"), 1.0),
            "max_tokens": self._int(settings.get("max_tokens"), 2048),
            "stream": False,
        }
        if provider_type == "xiaomi_mimo":
            payload["messages"] = [{"role": "user", "content": prompt}]

        async with httpx.AsyncClient(timeout=self._timeout(settings)) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
            )
            if response.status_code != 200:
                raise RuntimeError(f"文本 API 调用失败: HTTP {response.status_code}")
            data = response.json()
            return self._extract_openai_text(data)

    async def _call_gemini(
        self,
        prompt: str,
        api_key: str,
        base_url: str,
        model: str,
        settings: dict[str, Any],
    ) -> str:
        """调用 Gemini generateContent 接口"""
        import httpx

        if not base_url:
            base_url = "https://generativelanguage.googleapis.com/v1beta"
        selected_model = model or settings.get("model") or "gemini-2.5-flash"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": self._float(settings.get("temperature"), 0.7),
                "topP": self._float(settings.get("top_p"), 1.0),
                "topK": self._int(settings.get("top_k"), 40),
                "maxOutputTokens": self._int(settings.get("max_tokens"), 2048),
            },
        }

        async with httpx.AsyncClient(timeout=self._timeout(settings)) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/models/{selected_model}:generateContent",
                params={"key": api_key} if api_key else None,
                headers={"Content-Type": "application/json"},
                json=payload,
            )
            if response.status_code != 200:
                raise RuntimeError(f"Gemini 文本 API 调用失败: HTTP {response.status_code}")
            data = response.json()
            return self._extract_gemini_text(data)

    async def _call_anthropic(
        self,
        prompt: str,
        api_key: str,
        base_url: str,
        model: str,
        settings: dict[str, Any],
    ) -> str:
        """调用 Anthropic Messages 接口"""
        import httpx

        if not base_url:
            base_url = "https://api.anthropic.com/v1"
        selected_model = model or settings.get("model") or "claude-sonnet-4-5"
        payload = {
            "model": selected_model,
            "max_tokens": self._int(settings.get("max_tokens"), 2048),
            "temperature": self._float(settings.get("temperature"), 0.7),
            "messages": [{"role": "user", "content": prompt}],
        }

        async with httpx.AsyncClient(timeout=self._timeout(settings)) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/messages",
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                json=payload,
            )
            if response.status_code != 200:
                raise RuntimeError(f"Anthropic 文本 API 调用失败: HTTP {response.status_code}")
            data = response.json()
            return self._extract_anthropic_text(data)

    def _extract_openai_text(self, data: dict[str, Any]) -> str:
        """解析 OpenAI 兼容响应文本"""
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("文本 API 未返回 choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            return "".join(str(item.get("text", "")) for item in content if isinstance(item, dict)).strip()
        if isinstance(content, str):
            return content.strip()
        raise RuntimeError("文本 API 未返回正文")

    def _extract_gemini_text(self, data: dict[str, Any]) -> str:
        """解析 Gemini 响应文本"""
        candidates = data.get("candidates") or []
        if not candidates:
            raise RuntimeError("Gemini 未返回 candidates")
        parts = ((candidates[0].get("content") or {}).get("parts") or [])
        text = "".join(str(part.get("text", "")) for part in parts if isinstance(part, dict)).strip()
        if not text:
            raise RuntimeError("Gemini 未返回正文")
        return text

    def _extract_anthropic_text(self, data: dict[str, Any]) -> str:
        """解析 Anthropic 响应文本"""
        content = data.get("content") or []
        text = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict)).strip()
        if not text:
            raise RuntimeError("Anthropic 未返回正文")
        return text

    def _timeout(self, settings: dict[str, Any]) -> float:
        """读取超时时间"""
        return float(settings.get("timeout_seconds") or 120)

    def _float(self, value: Any, default: float) -> float:
        """安全转换浮点参数"""
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _int(self, value: Any, default: int) -> int:
        """安全转换整数参数"""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
