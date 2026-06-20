# backend/core/text_engine.py
# 文本模型引擎 - 调用 OpenAI、Gemini、Anthropic 和兼容渠道处理字幕文本

import asyncio
import json
import re
import time
from typing import Any

from .subtitle_engine import adjust_cjk_unit_boundary
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
        for index, entry in enumerate(processed, 1):
            entry["index"] = index
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
            "优先遵守上面的长度、语义断句和词组保护要求；不要把长字幕硬塞进同一条。\n"
            "必须覆盖每个输入 id，不能漏翻；同一个 id 可以返回多条，用来把过长字幕按语义拆短。\n"
            "不要新增输入里不存在的 id，不要把单个字或单个词单独成条。只返回 JSON 数组，不要 Markdown，不要解释。\n"
            "JSON 格式示例：[{\"id\":1,\"text\":\"处理后的字幕\"},{\"id\":1,\"text\":\"同一条原字幕拆出的第二段\"}]\n\n"
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
            if self._looks_like_structured_subtitle_response(response_text):
                if require_all:
                    raise RuntimeError("文本 API 返回字幕 JSON 格式错误，已触发整段翻译兜底")
                return [dict(entry) for entry in original_entries]
            lines = [line.strip() for line in response_text.splitlines() if line.strip()]
            if len(lines) == len(original_entries):
                processed_map = {index + 1: [line] for index, line in enumerate(lines)}

        if require_all and len(processed_map) < len(original_entries):
            if processed_map or self._looks_like_structured_subtitle_response(response_text):
                raise RuntimeError("文本 API 返回字幕条数不完整，已触发整段翻译兜底")
            fallback_lines = self._fallback_response_lines(response_text)
            distributed_lines = self._distribute_fallback_lines(fallback_lines, original_entries)
            if distributed_lines:
                processed_map = {index + 1: [line] for index, line in enumerate(distributed_lines) if line}
            if len(processed_map) < len(original_entries):
                raise RuntimeError("文本 API 返回字幕条数不完整，已触发整段翻译兜底")

        merged: list[dict[str, Any]] = []
        for index, entry in enumerate(original_entries, 1):
            texts = processed_map.get(index) or []
            if not texts:
                merged.append(dict(entry))
                continue
            merged.extend(self._split_entry_by_processed_texts(entry, texts))
        return merged

    def _split_entry_by_processed_texts(self, entry: dict[str, Any], texts: list[str]) -> list[dict[str, Any]]:
        """把同一原字幕返回的多段译文分配到原时间段内"""
        cleaned_texts = [str(text or "").strip() for text in texts if str(text or "").strip()]
        if not cleaned_texts:
            return [dict(entry)]
        if len(cleaned_texts) == 1:
            next_entry = dict(entry)
            next_entry["text"] = cleaned_texts[0]
            next_entry["source_index"] = entry.get("index")
            return [next_entry]

        start_ms = self._srt_time_to_milliseconds(str(entry.get("start") or "00:00:00,000"))
        end_ms = self._srt_time_to_milliseconds(str(entry.get("end") or entry.get("start") or "00:00:00,000"))
        if end_ms <= start_ms:
            end_ms = start_ms + max(1000, len(cleaned_texts) * 500)
        total_duration = max(1, end_ms - start_ms)
        weights = [max(1, len(text)) for text in cleaned_texts]
        total_weight = max(1, sum(weights))
        elapsed_weight = 0
        result: list[dict[str, Any]] = []
        segment_start = start_ms
        for index, text in enumerate(cleaned_texts):
            elapsed_weight += weights[index]
            segment_end = end_ms if index == len(cleaned_texts) - 1 else start_ms + int(total_duration * elapsed_weight / total_weight)
            segment_end = max(segment_start + 1, min(end_ms, segment_end))
            next_entry = dict(entry)
            next_entry["text"] = text
            next_entry["source_index"] = entry.get("index")
            next_entry["start"] = self._milliseconds_to_srt_time(segment_start)
            next_entry["end"] = self._milliseconds_to_srt_time(segment_end)
            result.append(next_entry)
            segment_start = segment_end
        return result

    def _fallback_response_lines(self, response_text: str) -> list[str]:
        """把非结构化模型返回转成可回填的文本行"""
        cleaned = self._strip_markdown_fence(str(response_text or "").strip())
        if not cleaned:
            return []
        return [line.strip() for line in cleaned.splitlines() if line.strip()]

    def _distribute_fallback_lines(self, lines: list[str], original_entries: list[dict[str, Any]]) -> list[str]:
        """把当前批次的非结构化译文回填到原字幕槽位，避免整段视频级粗切"""
        if not lines or not original_entries:
            return []
        text = self._join_fallback_lines(lines)
        units = self._fallback_text_units(text)
        if not units:
            return []

        weights = [max(1, len(str(entry.get("text") or "").replace("\\N", " ").strip())) for entry in original_entries]
        total_units = len(units)
        total_weight = max(1, sum(weights))
        distributed: list[str] = []
        unit_start = 0
        elapsed_weight = 0
        for index, weight in enumerate(weights):
            elapsed_weight += weight
            unit_end = total_units if index == len(weights) - 1 else round(total_units * elapsed_weight / total_weight)
            unit_end = max(unit_start + 1, min(total_units, unit_end))
            if index < len(weights) - 1:
                unit_end = adjust_cjk_unit_boundary(units, unit_end, min_index=unit_start + 1, max_index=total_units - 1)
            distributed.append(self._join_fallback_units(units[unit_start:unit_end]))
            unit_start = unit_end
            if unit_start >= total_units:
                distributed.extend(["" for _ in range(index + 1, len(weights))])
                break
        return distributed[:len(original_entries)]

    def _join_fallback_lines(self, lines: list[str]) -> str:
        """合并模型兜底文本，中文不插空格，英文单词间保留空格"""
        text = ""
        for line in lines:
            cleaned = " ".join(str(line or "").split())
            if not cleaned:
                continue
            if not text:
                text = cleaned
                continue
            separator = " " if re.match(r"[\w\]]$", text, re.ASCII) and re.match(r"^[\w\[]", cleaned, re.ASCII) else ""
            text = f"{text}{separator}{cleaned}"
        return text

    def _fallback_text_units(self, text: str) -> list[str]:
        """生成字幕回填单元，中文按字、英文按词，方便保留时间轴数量"""
        normalized = " ".join(str(text or "").split())
        tokens = re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*|[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]|[^\s]", normalized)
        if len(tokens) >= 2:
            return tokens
        return [char for char in normalized if not char.isspace()]

    def _join_fallback_units(self, units: list[str]) -> str:
        """合并字幕回填单元，避免中文被加空格"""
        text = ""
        for unit in [item.strip() for item in units if item.strip()]:
            if not text:
                text = unit
                continue
            separator = " " if re.match(r"[A-Za-z0-9]$", text) and re.match(r"^[A-Za-z0-9]", unit) else ""
            text = f"{text}{separator}{unit}"
        return text.strip()

    def _parse_processed_subtitle_response(self, response_text: str) -> dict[int, list[str]]:
        """解析模型返回的 JSON 或编号列表"""
        cleaned = self._strip_markdown_fence(response_text.strip())
        try:
            data = json.loads(cleaned)
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                data = data["items"]
            if isinstance(data, list):
                parsed: dict[int, list[str]] = {}
                for index, item in enumerate(data, 1):
                    if isinstance(item, dict):
                        item_id = self._int(item.get("id"), index)
                        text = str(item.get("text") or "").strip()
                    else:
                        item_id = index
                        text = str(item).strip()
                    if text:
                        parsed.setdefault(item_id, []).append(text)
                return parsed
        except json.JSONDecodeError:
            pass

        loose_json = self._parse_loose_processed_subtitle_response(cleaned)
        if loose_json:
            return loose_json

        parsed: dict[int, list[str]] = {}
        pattern = re.compile(r"^\s*(?:\[?(\d+)\]?[\.:：、\)\-]\s*)(.+?)\s*$")
        for line in cleaned.splitlines():
            match = pattern.match(line)
            if match:
                parsed.setdefault(int(match.group(1)), []).append(match.group(2).strip())
        return parsed

    def _parse_loose_processed_subtitle_response(self, text: str) -> dict[int, list[str]]:
        """兼容模型漏掉逗号的 JSON 数组，能修则修，不能修就交给兜底重试"""
        parsed: dict[int, list[str]] = {}
        pattern = re.compile(
            r"['\"]?id['\"]?\s*[:=]\s*(\d+)\s*,?\s*['\"]?text['\"]?\s*[:=]\s*(['\"])(.*?)\2",
            re.DOTALL,
        )
        for match in pattern.finditer(text or ""):
            subtitle_id = self._int(match.group(1), 0)
            subtitle_text = " ".join(str(match.group(3) or "").split())
            if subtitle_id > 0 and subtitle_text:
                parsed.setdefault(subtitle_id, []).append(subtitle_text)
        return parsed

    def _srt_time_to_milliseconds(self, value: str) -> int:
        """把 SRT 时间码转成毫秒，用于拆分同一原字幕的时间段"""
        text = str(value or "").strip().replace(",", ".")
        parts = text.split(":")
        if len(parts) != 3:
            return 0
        try:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])
        except ValueError:
            return 0
        return max(0, int(round((hours * 3600 + minutes * 60 + seconds) * 1000)))

    def _milliseconds_to_srt_time(self, value: int) -> str:
        """把毫秒转成标准 SRT 时间码"""
        total_ms = max(0, int(value))
        hours = total_ms // 3600000
        minutes = (total_ms % 3600000) // 60000
        seconds = (total_ms % 60000) // 1000
        millis = total_ms % 1000
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    def _looks_like_structured_subtitle_response(self, response_text: str) -> bool:
        """识别模型返回的坏 JSON，避免把结构化残片写进字幕正文"""
        cleaned = self._strip_markdown_fence(str(response_text or "").strip())
        if not cleaned:
            return False
        lowered = cleaned.lower()
        if cleaned.startswith(("[", "{")) and "id" in lowered and "text" in lowered:
            return True
        return bool(re.search(r"['\"]?id['\"]?\s*[:=]\s*\d+.*?['\"]?text['\"]?\s*[:=]", cleaned, re.DOTALL | re.IGNORECASE))

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
