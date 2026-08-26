"""远端 OpenAI-compatible LLM Provider 适配器。

Provider 只负责受限的结构化候选生成，不拥有项目权限、预算或执行能力。
"""

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from etl_agent.config import Settings
from etl_agent.domain.generation import GenerationRequest


class LLMProviderError(RuntimeError):
    """远端 Provider 的稳定错误，避免把 SDK 或响应原文暴露给 API。"""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        """创建 Provider 错误并记录是否允许有界重试。"""
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class StructuredGenerationResponse:
    """Provider 返回的结构化候选和最小证据摘要。"""

    payload: dict[str, Any]
    provider: str
    model: str
    latency_ms: int
    attempts: int
    response_digest: str


class LLMProvider(Protocol):
    """工作流依赖的 LLM 端口，具体供应商只能在适配器层出现。"""

    async def generate_structured(
        self,
        request: GenerationRequest,
        schema: dict[str, Any] | type[BaseModel],
        *,
        repair_errors: list[str] | None = None,
        previous_candidate: dict[str, Any] | None = None,
    ) -> StructuredGenerationResponse: ...


_SENSITIVE_KEY_PARTS = ("password", "secret", "token", "api_key", "access_key", "authorization")
_JSON_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.IGNORECASE | re.DOTALL)


def redact_for_llm(value: Any) -> Any:
    """递归移除可能包含凭据的字段，作为发送到远端模型前的最后一道保护。"""
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]"
            if any(part in str(key).lower() for part in _SENSITIVE_KEY_PARTS)
            else redact_for_llm(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_for_llm(item) for item in value]
    return value


def _schema_dict(schema: dict[str, Any] | type[BaseModel]) -> dict[str, Any]:
    """将 Pydantic 类型或已生成的 JSON Schema 统一为字典。"""
    return schema.model_json_schema() if isinstance(schema, type) else schema


def _parse_json_content(content: Any) -> dict[str, Any]:
    """解析兼容接口返回的 JSON 文本，同时支持模型常见的 Markdown 围栏。"""
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        raise LLMProviderError("LLM_INVALID_RESPONSE", "LLM 返回内容不是 JSON 对象")
    candidate = _JSON_FENCE.sub(r"\1", content.strip())
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise LLMProviderError("LLM_INVALID_JSON", "LLM 返回了无法解析的 JSON") from exc
    if not isinstance(parsed, dict):
        raise LLMProviderError("LLM_INVALID_RESPONSE", "LLM 返回内容必须是 JSON 对象")
    return parsed


class OpenAICompatibleProvider:
    """调用百炼等 OpenAI-compatible 接口的最小异步适配器。"""

    provider_name = "openai_compatible"

    def __init__(self, settings: Settings) -> None:
        """读取远端地址、模型和有界重试参数，不在实例中记录完整请求。"""
        self.base_url = settings.llm_base_url.rstrip("/")
        self.api_key = settings.llm_api_key
        self.model = settings.llm_model
        self.timeout = settings.llm_request_timeout_seconds
        self.max_retries = settings.llm_max_retries
        self.max_prompt_bytes = settings.llm_max_prompt_bytes

    async def generate_structured(
        self,
        request: GenerationRequest,
        schema: dict[str, Any] | type[BaseModel],
        *,
        repair_errors: list[str] | None = None,
        previous_candidate: dict[str, Any] | None = None,
    ) -> StructuredGenerationResponse:
        """向远端模型请求候选 JSON，并对网络失败执行有限重试。"""
        if not self.base_url or not self.api_key or not self.model:
            raise LLMProviderError("LLM_NOT_CONFIGURED", "远端 LLM Provider 配置不完整")
        safe_request = redact_for_llm(request.model_dump(mode="json"))
        prompt_data = {
            "request": safe_request,
            "json_schema": _schema_dict(schema),
            "repair_errors": repair_errors or [],
            "previous_candidate": redact_for_llm(previous_candidate)
            if previous_candidate
            else None,
        }
        user_content = json.dumps(prompt_data, ensure_ascii=False)
        if len(user_content.encode("utf-8")) > self.max_prompt_bytes:
            raise LLMProviderError("LLM_PROMPT_TOO_LARGE", "发送到 LLM 的脱敏 Prompt 超出大小上限")
        messages = [
            {
                "role": "system",
                "content": (
                    "你是 ETL 设计候选生成器。只返回符合 JSON Schema 的 JSON 对象；"
                    "不要调用工具，不要决定权限、预算、审批或执行动作。"
                ),
            },
            {"role": "user", "content": user_content},
        ]
        request_body = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        started = time.perf_counter()
        attempts = 0
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(self.max_retries + 1):
                attempts = attempt + 1
                try:
                    response = await client.post(
                        f"{self.base_url}/chat/completions", json=request_body, headers=headers
                    )
                    if response.status_code in {408, 409, 429} or response.status_code >= 500:
                        if attempt < self.max_retries:
                            await asyncio.sleep(min(2**attempt, 4))
                            continue
                        raise LLMProviderError(
                            "LLM_UPSTREAM_UNAVAILABLE", "远端 LLM 暂时不可用", retryable=True
                        )
                    if response.status_code >= 400:
                        raise LLMProviderError("LLM_REQUEST_REJECTED", "远端 LLM 拒绝了请求")
                    body = response.json()
                    content = body["choices"][0]["message"]["content"]
                    payload = _parse_json_content(content)
                    digest = hashlib.sha256(
                        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
                    ).hexdigest()
                    return StructuredGenerationResponse(
                        payload=payload,
                        provider=self.provider_name,
                        model=self.model,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        attempts=attempts,
                        response_digest=digest,
                    )
                except httpx.TimeoutException as exc:
                    if attempt < self.max_retries:
                        await asyncio.sleep(min(2**attempt, 4))
                        continue
                    raise LLMProviderError(
                        "LLM_TIMEOUT", "远端 LLM 请求超时", retryable=True
                    ) from exc
                except httpx.TransportError as exc:
                    if attempt < self.max_retries:
                        await asyncio.sleep(min(2**attempt, 4))
                        continue
                    raise LLMProviderError(
                        "LLM_NETWORK_ERROR", "远端 LLM 网络请求失败", retryable=True
                    ) from exc
                except (KeyError, IndexError, TypeError, ValueError) as exc:
                    raise LLMProviderError("LLM_INVALID_RESPONSE", "远端 LLM 响应结构无效") from exc
        raise LLMProviderError("LLM_UPSTREAM_UNAVAILABLE", "远端 LLM 暂时不可用", retryable=True)


class FakeLLMProvider:
    """离线测试用 Provider，可按顺序返回预置候选。"""

    provider_name = "fake"

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        """保存候选队列并统计调用次数，确保测试不触发真实网络。"""
        self.responses = list(responses)
        self.calls = 0

    async def generate_structured(
        self,
        request: GenerationRequest,
        schema: dict[str, Any] | type[BaseModel],
        *,
        repair_errors: list[str] | None = None,
        previous_candidate: dict[str, Any] | None = None,
    ) -> StructuredGenerationResponse:
        """返回下一个预置候选，模拟模型修复调用。"""
        del request, schema, repair_errors, previous_candidate
        self.calls += 1
        if not self.responses:
            raise LLMProviderError("LLM_FAKE_EXHAUSTED", "fake Provider 没有更多候选")
        payload = self.responses.pop(0)
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        return StructuredGenerationResponse(payload, self.provider_name, "fake-model", 0, 1, digest)


def create_llm_provider(settings: Settings) -> LLMProvider:
    """根据配置创建 Provider；fake 仅允许用于测试或 development。"""
    if settings.llm_provider == "fake":
        return FakeLLMProvider([])
    if settings.llm_provider == "openai_compatible":
        return OpenAICompatibleProvider(settings)
    raise LLMProviderError("LLM_PROVIDER_UNSUPPORTED", "未支持的 LLM Provider")
