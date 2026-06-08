# backend/core/text_engine.py
# 文本模型引擎 - 调用 OpenAI、Gemini、Anthropic 和兼容渠道处理字幕文本

import asyncio
import json
import re
import time
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
        custom_instruction: str = "",
    ) -> str:
        """根据渠道类型处理字幕文本"""
        if not text.strip():
            raise ValueError("文本不能为空")

        options = settings or {}
        prompt = self._build_prompt(text, operation, target_language, options, custom_instruction)
        logger.info(f"调用文本模型处理字幕: {provider_type}, operation={operation}")

        return await self._call_prompt_with_retry(
            prompt=prompt,
            provider_type=provider_type,
            api_key=api_key,
            base_url=base_url,
            model=model,
            settings=options,
        )

    async def process_subtitle_entries(
        self,
        entries: list[dict[str, Any]],
        provider_type: str,
        api_key: str,
        base_url: str,
        model: str,
        settings: dict[str, Any] | None = None,
        operation: str = "polish",
        target_language: str = "",
        custom_instruction: str = "",
        progress_callback: Any | None = None,
    ) -> list[dict[str, Any]]:
        """
        按字幕条目分批处理文本，并保持每条字幕原有时间轴。
        返回 entries 结构，text 被替换为处理后的内容。
        """
        if not entries:
            return []

        options = settings or {}
        chunks = self._chunk_subtitle_entries(
            entries,
            batch_size=self._int(options.get("subtitle_batch_size"), 12),
            max_chars=self._int(options.get("subtitle_batch_chars"), 2800),
        )
        if not chunks:
            return [dict(entry) for entry in entries]

        concurrency = max(1, min(16, self._int(options.get("concurrency"), 2)))
        semaphore = asyncio.Semaphore(concurrency)
        limiter = _AsyncRateLimiter(self._int(options.get("rate_limit_rpm"), 0))
        results: list[list[dict[str, Any]] | None] = [None] * len(chunks)

        async def run_chunk(index: int, chunk: list[dict[str, Any]]) -> None:
            """执行单个字幕批次，并保存原始顺序"""
            async with semaphore:
                await limiter.wait()
                results[index] = await self._process_subtitle_chunk(
                    chunk=chunk,
                    provider_type=provider_type,
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    settings=options,
                    operation=operation,
                    target_language=target_language,
                    custom_instruction=custom_instruction,
                )
                if progress_callback:
                    progress_callback((index + 1) / len(chunks) * 100)

        await asyncio.gather(*(run_chunk(index, chunk) for index, chunk in enumerate(chunks)))

        processed: list[dict[str, Any]] = []
        for index, chunk in enumerate(chunks):
            processed.extend(results[index] or [dict(entry) for entry in chunk])
        return processed

    async def _call_prompt_with_retry(
        self,
        prompt: str,
        provider_type: str,
        api_key: str,
        base_url: str,
        model: str,
        settings: dict[str, Any],
    ) -> str:
        """按配置执行文本模型请求，支持失败重试"""
        retry_count = max(0, self._int(settings.get("retry_count"), 0))
        retry_interval_ms = max(0, self._int(settings.get("retry_interval_ms"), 1000))
        last_error: Exception | None = None

        for attempt in range(retry_count + 1):
            try:
                return await self._call_prompt_once(prompt, provider_type, api_key, base_url, model, settings)
            except Exception as exc:
                last_error = exc
                if attempt >= retry_count:
                    break
                await asyncio.sleep(retry_interval_ms / 1000)

        raise RuntimeError(str(last_error) if last_error else "文本 API 调用失败")

    async def _call_prompt_once(
        self,
        prompt: str,
        provider_type: str,
        api_key: str,
        base_url: str,
        model: str,
        settings: dict[str, Any],
    ) -> str:
        """根据渠道类型执行一次文本模型请求"""
        if provider_type in {"openai", "openai_compatible", "custom", "minimax", "xiaomi_mimo"}:
            return await self._call_openai_compatible(prompt, provider_type, api_key, base_url, model, settings)
        if provider_type in {"gemini", "gemini_compatible"}:
            return await self._call_gemini(prompt, api_key, base_url, model, settings)
        if provider_type == "anthropic":
            return await self._call_anthropic(prompt, api_key, base_url, model, settings)

        raise ValueError(f"不支持的文本 API 渠道: {provider_type}")

    async def _process_subtitle_chunk(
        self,
        chunk: list[dict[str, Any]],
        provider_type: str,
        api_key: str,
        base_url: str,
        model: str,
        settings: dict[str, Any],
        operation: str,
        target_language: str,
        custom_instruction: str = "",
    ) -> list[dict[str, Any]]:
        """处理单个字幕批次，模型输出解析失败时退回原条目"""
        prompt = self._build_subtitle_entries_prompt(chunk, operation, target_language, settings, custom_instruction)
        response_text = await self._call_prompt_with_retry(
            prompt=prompt,
            provider_type=provider_type,
            api_key=api_key,
            base_url=base_url,
            model=model,
            settings=settings,
        )
        return self._merge_processed_entries(chunk, response_text, require_all=operation == "translate")

    def _build_prompt(self, text: str, operation: str, target_language: str, settings: dict[str, Any], custom_instruction: str = "") -> str:
        """生成字幕处理提示词"""
        system_prompt = settings.get("system_prompt") or "你是专业短视频字幕处理助手，请保持含义准确、语言自然、适合口播。"
        language = self._target_language_label(target_language)
        operation_map = {
            "generate": "请根据以下视频字幕或文本生成一份适合短视频硬字幕和配音的字幕文案。",
            "translate": f"请将以下字幕翻译成{language}，保留原意，表达自然。",
            "polish": "请润色以下字幕，使其更适合短视频观看和口播，不要添加无关内容。",
        }
        instruction = operation_map.get(operation, operation_map["polish"])
        custom_block = self._custom_instruction_block(custom_instruction)
        return f"{system_prompt}\n\n{instruction}{custom_block}\n\n要求：只输出处理后的字幕正文，不要输出解释。\n\n原文：\n{text}"

    def _build_subtitle_entries_prompt(self, entries: list[dict[str, Any]], operation: str, target_language: str, settings: dict[str, Any], custom_instruction: str = "") -> str:
        """生成保留字幕编号的批处理提示词"""
        system_prompt = settings.get("system_prompt") or "你是专业短视频字幕处理助手，请保持含义准确、语言自然、适合口播。"
        language = self._target_language_label(target_language)
        operation_map = {
            "generate": "请基于每条原字幕生成更适合短视频硬字幕和配音的字幕文案。",
            "translate": f"请将每条字幕翻译成{language}，保留原意，表达自然。",
            "polish": "请逐条润色字幕，使其更适合短视频观看和口播，不要添加无关信息。",
        }
        instruction = operation_map.get(operation, operation_map["polish"])
        custom_block = self._custom_instruction_block(custom_instruction)
        payload = [
            {
                "id": index + 1,
                "text": str(entry.get("text", "")).replace("\\N", "\n"),
            }
            for index, entry in enumerate(entries)
        ]
        return (
            f"{system_prompt}\n\n{instruction}{custom_block}\n\n"
            "必须保持条目数量和 id 不变。只返回 JSON 数组，不要 Markdown，不要解释。\n"
            "JSON 格式示例：[{\"id\":1,\"text\":\"处理后的字幕\"}]\n\n"
            f"原字幕 JSON：\n{json.dumps(payload, ensure_ascii=False)}"
        )

    def _custom_instruction_block(self, custom_instruction: str) -> str:
        """把用户在弹窗里填写的处理要求拼进提示词"""
        instruction = str(custom_instruction or "").strip()
        return f"\n用户额外要求：{instruction}" if instruction else ""

    def _merge_processed_entries(self, original_entries: list[dict[str, Any]], response_text: str, require_all: bool = False) -> list[dict[str, Any]]:
        """将模型返回内容按 id 合并回原字幕时间轴"""
        processed_map = self._parse_processed_subtitle_response(response_text)
        if not processed_map:
            lines = [line.strip() for line in response_text.splitlines() if line.strip()]
            if len(lines) == len(original_entries):
                processed_map = {index + 1: line for index, line in enumerate(lines)}

        if require_all and len(processed_map) < len(original_entries):
            raise RuntimeError("文本 API 返回字幕条数不完整，已触发整段翻译兜底")

        merged: list[dict[str, Any]] = []
        for index, entry in enumerate(original_entries, 1):
            next_entry = dict(entry)
            text = processed_map.get(index)
            if text:
                next_entry["text"] = text
            merged.append(next_entry)
        return merged

    def _parse_processed_subtitle_response(self, response_text: str) -> dict[int, str]:
        """解析模型返回的 JSON 或编号列表"""
        cleaned = self._strip_markdown_fence(response_text.strip())
        try:
            data = json.loads(cleaned)
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                data = data["items"]
            if isinstance(data, list):
                parsed: dict[int, str] = {}
                for index, item in enumerate(data, 1):
                    if isinstance(item, dict):
                        item_id = self._int(item.get("id"), index)
                        text = str(item.get("text") or "").strip()
                    else:
                        item_id = index
                        text = str(item).strip()
                    if text:
                        parsed[item_id] = text
                return parsed
        except json.JSONDecodeError:
            pass

        parsed: dict[int, str] = {}
        pattern = re.compile(r"^\s*(?:\[?(\d+)\]?[\.:：、\)\-]\s*)(.+?)\s*$")
        for line in cleaned.splitlines():
            match = pattern.match(line)
            if match:
                parsed[int(match.group(1))] = match.group(2).strip()
        return parsed

    def _strip_markdown_fence(self, text: str) -> str:
        """去掉模型可能包裹的 Markdown 代码块"""
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
        return text.strip()

    def _chunk_subtitle_entries(self, entries: list[dict[str, Any]], batch_size: int, max_chars: int) -> list[list[dict[str, Any]]]:
        """按条数和字符数切分字幕批次"""
        chunks: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        current_chars = 0
        safe_batch_size = max(1, batch_size)
        safe_max_chars = max(200, max_chars)

        for entry in entries:
            text_len = len(str(entry.get("text", "")))
            if current and (len(current) >= safe_batch_size or current_chars + text_len > safe_max_chars):
                chunks.append(current)
                current = []
                current_chars = 0
            current.append(entry)
            current_chars += text_len
        if current:
            chunks.append(current)
        return chunks

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

    def _target_language_label(self, target_language: str) -> str:
        """把前端语言代码转换成清晰的目标语言名称"""
        value = str(target_language or "").strip()
        language_map = {
            "zh": "简体中文",
            "zh-cn": "简体中文",
            "zh-hans": "简体中文",
            "cn": "简体中文",
            "中文": "简体中文",
            "chinese": "简体中文",
            "en": "英文",
            "english": "英文",
            "ja": "日文",
            "jp": "日文",
            "japanese": "日文",
            "ko": "韩文",
            "korean": "韩文",
            "es": "西班牙语",
            "spanish": "西班牙语",
        }
        return language_map.get(value.lower(), value or "简体中文")

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


class _AsyncRateLimiter:
    """简单异步限速器，用于控制文本 API 的每分钟请求数"""

    def __init__(self, rpm: int):
        """初始化限速器，rpm 小于等于 0 表示不限速"""
        self.interval = 60 / rpm if rpm and rpm > 0 else 0
        self._lock = asyncio.Lock()
        self._next_time = 0.0

    async def wait(self) -> None:
        """等待到下一次允许请求的时间"""
        if self.interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            if self._next_time > now:
                await asyncio.sleep(self._next_time - now)
            self._next_time = time.monotonic() + self.interval
