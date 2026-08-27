"""远端 OpenAI-compatible LLM Provider 适配器。

Provider 只负责受限的结构化候选生成，不拥有项目权限、预算或执行能力。
"""

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from etl_agent.config import Settings
from etl_agent.domain.generation import GenerationRequest

logger = logging.getLogger(__name__)


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

    async def answer_question(self, question: str, context: dict[str, Any]) -> str: ...


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
            logger.warning(
                "llm_request_skipped provider=%s operation=generate_structured "
                "reason=not_configured",
                self.provider_name,
            )
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
            logger.warning(
                "llm_request_skipped provider=%s operation=generate_structured "
                "reason=prompt_too_large",
                self.provider_name,
            )
            raise LLMProviderError("LLM_PROMPT_TOO_LARGE", "发送到 LLM 的脱敏 Prompt 超出大小上限")
        messages = [
            {
                "role": "system",
                "content": (
                    "你是 ETL 设计候选生成器。只返回符合 JSON Schema 的 JSON 对象；"
                    "不要调用工具，不要决定权限、预算、审批或执行动作。"
                    "请严格依据源和目标 Profile 生成 field_mappings 与 transforms："
                    "field_mappings 每项必须包含 source_field、target_field 和 transform；"
                    'transform 只能是 null、"rename" 或 "cast"；'
                    "不要把 operation/source_fields/parameters 对象嵌套到 "
                    "field_mappings.transform；"
                    "源字段和目标字段类型一致时必须直接映射，不得添加冗余 CAST；"
                    "只有类型确实不一致或用户明确要求时才使用 CAST；"
                    "FILTER 过滤规则的 parameters 必须使用 condition 字段保存条件字符串，"
                    '例如 {"condition": "amount > 0"}，不要使用 expression；'
                    "hocon 必须是可被 PyHOCON 解析的普通文本；所有字符串值使用双引号，"
                    "键值之间使用换行或逗号分隔，不要输出紧凑无分隔文本或 Markdown 围栏；"
                    "不要把通用 SQL 类型名当作 MySQL 方言，具体执行 SQL 由服务端编译器生成；"
                    "不得生成未出现在 Profile 中的字段、表或任意脚本。"
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
        logger.info(
            "llm_request_started provider=%s model=%s operation=generate_structured "
            "prompt_bytes=%s",
            self.provider_name,
            self.model,
            len(user_content.encode("utf-8")),
        )
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(self.max_retries + 1):
                attempts = attempt + 1
                try:
                    response = await client.post(
                        f"{self.base_url}/chat/completions", json=request_body, headers=headers
                    )
                    if response.status_code in {408, 409, 429} or response.status_code >= 500:
                        if attempt < self.max_retries:
                            logger.warning(
                                "llm_request_retry provider=%s model=%s "
                                "operation=generate_structured "
                                "attempt=%s status_code=%s",
                                self.provider_name,
                                self.model,
                                attempts,
                                response.status_code,
                            )
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
                    logger.info(
                        "llm_request_completed provider=%s model=%s operation=generate_structured "
                        "latency_ms=%s attempts=%s response_digest=%s",
                        self.provider_name,
                        self.model,
                        int((time.perf_counter() - started) * 1000),
                        attempts,
                        digest,
                    )
                    return StructuredGenerationResponse(
                        payload=payload,
                        provider=self.provider_name,
                        model=self.model,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        attempts=attempts,
                        response_digest=digest,
                    )
                except LLMProviderError as exc:
                    logger.error(
                        "llm_request_failed provider=%s model=%s operation=generate_structured "
                        "attempts=%s error_code=%s",
                        self.provider_name,
                        self.model,
                        attempts,
                        exc.code,
                    )
                    raise
                except httpx.TimeoutException as exc:
                    if attempt < self.max_retries:
                        logger.warning(
                            "llm_request_retry provider=%s model=%s operation=generate_structured "
                            "attempt=%s reason=timeout",
                            self.provider_name,
                            self.model,
                            attempts,
                        )
                        await asyncio.sleep(min(2**attempt, 4))
                        continue
                    raise LLMProviderError(
                        "LLM_TIMEOUT", "远端 LLM 请求超时", retryable=True
                    ) from exc
                except httpx.TransportError as exc:
                    if attempt < self.max_retries:
                        logger.warning(
                            "llm_request_retry provider=%s model=%s operation=generate_structured "
                            "attempt=%s reason=transport",
                            self.provider_name,
                            self.model,
                            attempts,
                        )
                        await asyncio.sleep(min(2**attempt, 4))
                        continue
                    raise LLMProviderError(
                        "LLM_NETWORK_ERROR", "远端 LLM 网络请求失败", retryable=True
                    ) from exc
                except (KeyError, IndexError, TypeError, ValueError) as exc:
                    logger.error(
                        "llm_request_failed provider=%s model=%s operation=generate_structured "
                        "attempts=%s reason=invalid_response",
                        self.provider_name,
                        self.model,
                        attempts,
                    )
                    raise LLMProviderError("LLM_INVALID_RESPONSE", "远端 LLM 响应结构无效") from exc
        raise LLMProviderError("LLM_UPSTREAM_UNAVAILABLE", "远端 LLM 暂时不可用", retryable=True)

    async def answer_question(self, question: str, context: dict[str, Any]) -> str:
        """基于已通过门禁的候选回答审查问题，不允许模型修改或执行方案。"""
        if not self.base_url or not self.api_key or not self.model:
            logger.warning(
                "llm_request_skipped provider=%s operation=answer_question reason=not_configured",
                self.provider_name,
            )
            raise LLMProviderError("LLM_NOT_CONFIGURED", "远端 LLM Provider 配置不完整")
        prompt_data = {
            "question": question.strip(),
            "candidate_context": redact_for_llm(context),
        }
        user_content = json.dumps(prompt_data, ensure_ascii=False)
        if len(user_content.encode("utf-8")) > self.max_prompt_bytes:
            logger.warning(
                "llm_request_skipped provider=%s operation=answer_question reason=prompt_too_large",
                self.provider_name,
            )
            raise LLMProviderError("LLM_PROMPT_TOO_LARGE", "发送到 LLM 的脱敏 Prompt 超出大小上限")
        messages = [
            {
                "role": "system",
                "content": (
                    "你是 ETL 方案审查助手。只解释候选 EtlPlan 和 HOCON，指出与用户需求的差异；"
                    "不要编造 Profile 字段，不要修改权限、预算或执行状态，不要调用工具。"
                ),
            },
            {"role": "user", "content": user_content},
        ]
        request_body = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        started = time.perf_counter()
        logger.info(
            "llm_request_started provider=%s model=%s operation=answer_question prompt_bytes=%s",
            self.provider_name,
            self.model,
            len(user_content.encode("utf-8")),
        )
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(self.max_retries + 1):
                try:
                    response = await client.post(
                        f"{self.base_url}/chat/completions", json=request_body, headers=headers
                    )
                    if response.status_code in {408, 409, 429} or response.status_code >= 500:
                        if attempt < self.max_retries:
                            logger.warning(
                                "llm_request_retry provider=%s model=%s operation=answer_question "
                                "attempt=%s status_code=%s",
                                self.provider_name,
                                self.model,
                                attempt + 1,
                                response.status_code,
                            )
                            await asyncio.sleep(min(2**attempt, 4))
                            continue
                        raise LLMProviderError(
                            "LLM_UPSTREAM_UNAVAILABLE", "远端 LLM 暂时不可用", retryable=True
                        )
                    if response.status_code >= 400:
                        raise LLMProviderError("LLM_REQUEST_REJECTED", "远端 LLM 拒绝了请求")
                    body = response.json()
                    content = body["choices"][0]["message"]["content"]
                    if not isinstance(content, str) or not content.strip():
                        raise LLMProviderError("LLM_INVALID_RESPONSE", "LLM 返回内容为空")
                    answer = content.strip()[:8_000]
                    answer_digest = hashlib.sha256(answer.encode("utf-8")).hexdigest()
                    logger.info(
                        "llm_request_completed provider=%s model=%s operation=answer_question "
                        "latency_ms=%s attempts=%s response_digest=%s",
                        self.provider_name,
                        self.model,
                        int((time.perf_counter() - started) * 1000),
                        attempt + 1,
                        answer_digest,
                    )
                    return answer
                except LLMProviderError as exc:
                    logger.error(
                        "llm_request_failed provider=%s model=%s operation=answer_question "
                        "attempts=%s error_code=%s",
                        self.provider_name,
                        self.model,
                        attempt + 1,
                        exc.code,
                    )
                    raise
                except httpx.TimeoutException as exc:
                    if attempt < self.max_retries:
                        logger.warning(
                            "llm_request_retry provider=%s model=%s operation=answer_question "
                            "attempt=%s reason=timeout",
                            self.provider_name,
                            self.model,
                            attempt + 1,
                        )
                        await asyncio.sleep(min(2**attempt, 4))
                        continue
                    raise LLMProviderError(
                        "LLM_TIMEOUT", "远端 LLM 请求超时", retryable=True
                    ) from exc
                except httpx.TransportError as exc:
                    if attempt < self.max_retries:
                        logger.warning(
                            "llm_request_retry provider=%s model=%s operation=answer_question "
                            "attempt=%s reason=transport",
                            self.provider_name,
                            self.model,
                            attempt + 1,
                        )
                        await asyncio.sleep(min(2**attempt, 4))
                        continue
                    raise LLMProviderError(
                        "LLM_NETWORK_ERROR", "远端 LLM 网络请求失败", retryable=True
                    ) from exc
                except (KeyError, IndexError, TypeError, ValueError) as exc:
                    logger.error(
                        "llm_request_failed provider=%s model=%s operation=answer_question "
                        "attempts=%s reason=invalid_response",
                        self.provider_name,
                        self.model,
                        attempt + 1,
                    )
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

    async def answer_question(self, question: str, context: dict[str, Any]) -> str:
        """返回离线审查回答，保证对话测试不触发真实网络。"""
        del context
        return f"已收到审查问题：{question.strip()}"


def create_llm_provider(settings: Settings) -> LLMProvider:
    """根据配置创建 Provider；fake 仅允许用于测试或 development。"""
    if settings.llm_provider == "fake":
        return FakeLLMProvider([])
    if settings.llm_provider == "openai_compatible":
        return OpenAICompatibleProvider(settings)
    raise LLMProviderError("LLM_PROVIDER_UNSUPPORTED", "未支持的 LLM Provider")
