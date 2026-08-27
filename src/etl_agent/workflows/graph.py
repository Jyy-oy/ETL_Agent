"""阶段 3 的最小 LangGraph 生成工作流。

图只生成和校验候选，不创建审批、执行或其他外部副作用。节点状态可以由
PostgreSQL Checkpoint 持久化，供澄清回答后的同一 thread 恢复。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypedDict, cast
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from pydantic import TypeAdapter

from etl_agent.domain.generation import (
    ClarificationQuestion,
    EtlPlan,
    GenerationRequest,
    GenerationResult,
    ValidationIssue,
)
from etl_agent.infrastructure.llm import LLMProvider, LLMProviderError
from etl_agent.workflows.validation import compile_hocon, validate_plan_payload

# 这些错误代表真实数据面尚未具备对应能力，不能靠再次提示模型修复。
_TERMINAL_VALIDATION_CODES = {
    "UNSUPPORTED_DATA_PLANE_FEATURE",
    "UNSUPPORTED_DATA_PLANE_TRANSFORM",
}


class GenerationState(TypedDict, total=False):
    """LangGraph 节点之间传递的可序列化状态。"""

    request: GenerationRequest
    raw_candidate: dict[str, Any]
    plan: EtlPlan
    clarification_questions: list[ClarificationQuestion]
    validation_issues: list[ValidationIssue]
    repair_count: int
    node_trace: list[str]
    attempts: list[dict[str, Any]]
    status: str
    provider: str
    model: str
    error_code: str
    error_message: str
    hocon_config: Any


def _trace(state: GenerationState, node: str) -> list[str]:
    """追加节点名，帮助 AgentRun 记录可审计的执行路径。"""
    return [*state.get("node_trace", []), node]


def _has_terminal_validation_issue(state: GenerationState) -> bool:
    """判断校验结果是否属于数据面能力边界，避免无效的模型修复调用。"""
    return any(
        issue.code in _TERMINAL_VALIDATION_CODES for issue in state.get("validation_issues", [])
    )


def _clarification_questions(request: GenerationRequest) -> list[ClarificationQuestion]:
    """根据确定性输入检查生成所需的最低参数，不让模型猜测关键边界。"""
    questions: list[ClarificationQuestion] = []
    if not request.business_request.strip():
        questions.append(
            ClarificationQuestion(key="business_request", question="请描述需要实现的 ETL 业务需求")
        )
    if not request.source_profiles:
        questions.append(
            ClarificationQuestion(key="source_profile", question="请提供一个已授权的源 Profile")
        )
    if not request.target_profiles:
        questions.append(
            ClarificationQuestion(key="target_profile", question="请提供一个已授权的目标 Profile")
        )
    text = request.business_request.lower()
    if ("增量" in text or "incremental" in text) and not request.answers.get("incremental_field"):
        questions.append(
            ClarificationQuestion(key="incremental_field", question="请指定增量同步使用的字段")
        )
    return questions


def build_generation_graph(
    provider: LLMProvider,
    *,
    checkpointer: Any | None = None,
    max_repairs: int = 1,
) -> Any:
    """构建可注入 Provider 和 PostgreSQL Checkpoint 的生成图。"""
    if max_repairs < 0:
        raise ValueError("max_repairs 不能为负数")

    async def intent_parse(state: GenerationState) -> dict[str, Any]:
        """解析最小意图并在缺少关键参数时进入人工澄清。"""
        request = state["request"]
        questions = _clarification_questions(request)
        updates: dict[str, Any] = {
            "clarification_questions": questions,
            "node_trace": _trace(state, "IntentParseNode"),
        }
        if questions:
            updates["status"] = "needs_clarification"
        else:
            updates["status"] = "running"
        return updates

    async def profile_enrichment(state: GenerationState) -> dict[str, Any]:
        """只传递请求中已经脱敏的 Profile 摘要，不查询海量业务数据。"""
        return {"node_trace": _trace(state, "ProfileEnrichmentNode")}

    async def human_interrupt(state: GenerationState) -> dict[str, Any]:
        """记录人工中断边界，等待 API 提交答案或人工处理后再恢复。"""
        return {"node_trace": _trace(state, "HumanInterruptNode")}

    async def candidate_generation(state: GenerationState) -> dict[str, Any]:
        """调用 Provider 生成结构化候选，并保存最小 Provider 证据。"""
        request = state["request"]
        try:
            response = await provider.generate_structured(
                request,
                EtlPlan,
                repair_errors=[issue.message for issue in state.get("validation_issues", [])],
                previous_candidate=state.get("raw_candidate"),
            )
        except LLMProviderError as exc:
            attempts = [
                *state.get("attempts", []),
                {
                    "attempt_number": len(state.get("attempts", [])) + 1,
                    "kind": "repair" if state.get("repair_count", 0) else "candidate",
                    "output_digest": None,
                    "status": "failed",
                    "validation_errors": [],
                },
            ]
            return {
                "status": "failed",
                "error_code": exc.code,
                "error_message": str(exc),
                "attempts": attempts,
                "node_trace": _trace(state, "CandidateGenerationNode"),
            }
        attempts = [
            *state.get("attempts", []),
            {
                "attempt_number": len(state.get("attempts", [])) + 1,
                "kind": "repair" if state.get("repair_count", 0) else "candidate",
                "output_digest": response.response_digest,
                "status": "candidate_generated",
                "validation_errors": [],
            },
        ]
        return {
            "status": "candidate_generated",
            "raw_candidate": response.payload,
            "provider": response.provider,
            "model": response.model,
            "attempts": attempts,
            "node_trace": _trace(state, "CandidateGenerationNode"),
        }

    async def schema_validation(state: GenerationState) -> dict[str, Any]:
        """执行 Pydantic、JSON Schema、Profile 引用和预算校验。"""
        plan, issues = validate_plan_payload(state.get("raw_candidate"), state["request"])
        attempts = [*state.get("attempts", [])]
        if attempts:
            attempts[-1] = {
                **attempts[-1],
                "status": "validated" if plan is not None else "validation_failed",
                "validation_errors": [issue.model_dump(mode="json") for issue in issues],
            }
        updates: dict[str, Any] = {
            "validation_issues": issues,
            # 清理上一次候选留下的 plan，避免修复候选非法时沿用旧版本。
            "plan": plan,
            "attempts": attempts,
            "node_trace": _trace(state, "SchemaValidationNode"),
        }
        if plan is not None:
            updates["status"] = "schema_valid"
        else:
            updates["status"] = "validation_failed"
        return updates

    async def hocon_compile(state: GenerationState) -> dict[str, Any]:
        """单独记录 HOCON 编译节点，确保配置语法通过后才进入门禁。"""
        plan = state.get("plan")
        if plan is None:
            return {"node_trace": _trace(state, "HoconCompileNode")}
        config, issue = compile_hocon(plan.hocon)
        if issue is not None:
            return {
                "status": "validation_failed",
                "validation_issues": [*state.get("validation_issues", []), issue],
                "node_trace": _trace(state, "HoconCompileNode"),
            }
        return {"hocon_config": config, "node_trace": _trace(state, "HoconCompileNode")}

    async def deterministic_gate(state: GenerationState) -> dict[str, Any]:
        """只根据代码校验结果决定成功或进入有限修复，模型不能绕过门禁。"""
        issues = state.get("validation_issues", [])
        updates: dict[str, Any] = {"node_trace": _trace(state, "DeterministicGateNode")}
        if issues:
            updates["status"] = "validation_failed"
        else:
            updates["status"] = "completed"
        return updates

    async def repair(state: GenerationState) -> dict[str, Any]:
        """增加一次有界修复计数，下一节点重新请求和完整校验候选。"""
        return {
            "repair_count": state.get("repair_count", 0) + 1,
            "node_trace": _trace(state, "RepairNode"),
            "status": "repairing",
        }

    def after_intent(state: GenerationState) -> str:
        """根据是否缺参选择人工中断或继续生成。"""
        return "interrupt" if state.get("status") == "needs_clarification" else "continue"

    def after_candidate(state: GenerationState) -> str:
        """Provider 失败时结束，否则进入结构化校验。"""
        return "stop" if state.get("status") == "failed" else "validate"

    def after_validation(state: GenerationState) -> str:
        """合法候选进入 HOCON 节点，非法候选按次数进入修复或结束。"""
        if state.get("plan") is not None:
            return "hocon"
        if _has_terminal_validation_issue(state):
            return "stop"
        return "repair" if state.get("repair_count", 0) < max_repairs else "stop"

    def after_gate(state: GenerationState) -> str:
        """门禁通过结束，失败仅允许有限次修复。"""
        if state.get("status") == "completed":
            return "done"
        if _has_terminal_validation_issue(state):
            return "stop"
        return "repair" if state.get("repair_count", 0) < max_repairs else "stop"

    graph = StateGraph(GenerationState)
    graph.add_node("intent_parse", intent_parse)
    graph.add_node("human_interrupt", human_interrupt)
    graph.add_node("profile_enrichment", profile_enrichment)
    graph.add_node("candidate_generation", candidate_generation)
    graph.add_node("schema_validation", schema_validation)
    graph.add_node("hocon_compile", hocon_compile)
    graph.add_node("deterministic_gate", deterministic_gate)
    graph.add_node("repair", repair)
    graph.add_edge(START, "intent_parse")
    graph.add_conditional_edges(
        "intent_parse",
        after_intent,
        {"interrupt": "human_interrupt", "continue": "profile_enrichment"},
    )
    graph.add_edge("human_interrupt", END)
    graph.add_edge("profile_enrichment", "candidate_generation")
    graph.add_conditional_edges(
        "candidate_generation", after_candidate, {"stop": END, "validate": "schema_validation"}
    )
    graph.add_conditional_edges(
        "schema_validation",
        after_validation,
        {"hocon": "hocon_compile", "repair": "repair", "stop": "human_interrupt"},
    )
    graph.add_edge("hocon_compile", "deterministic_gate")
    graph.add_conditional_edges(
        "deterministic_gate",
        after_gate,
        {"done": END, "repair": "repair", "stop": "human_interrupt"},
    )
    graph.add_edge("repair", "candidate_generation")
    return graph.compile(checkpointer=checkpointer)


async def run_generation_workflow(
    request: GenerationRequest,
    provider: LLMProvider,
    *,
    thread_id: str | None = None,
    checkpointer: Any | None = None,
    max_repairs: int = 1,
    progress_callback: Callable[[str, GenerationState], Awaitable[None]] | None = None,
) -> GenerationResult:
    """运行一次生成图并返回稳定结果；可选逐节点回调用于持久化进度。"""
    graph = build_generation_graph(provider, checkpointer=checkpointer, max_repairs=max_repairs)
    config = {"configurable": {"thread_id": thread_id or str(uuid4())}} if checkpointer else None
    if progress_callback is None:
        state = await graph.ainvoke({"request": request}, config=config)
    else:
        # updates 模式只返回节点增量；这里合并成累计状态后再交给持久化回调。
        state = {"request": request}
        async for update in graph.astream(
            {"request": request}, config=config, stream_mode="updates"
        ):
            for node_name, delta in update.items():
                state = cast(GenerationState, {**state, **delta})
                await progress_callback(str(node_name), state)
    return GenerationResult(
        status=state.get("status", "failed"),
        plan=state.get("plan"),
        clarification_questions=state.get("clarification_questions", []),
        validation_issues=state.get("validation_issues", []),
        repair_count=state.get("repair_count", 0),
        node_trace=state.get("node_trace", []),
        attempts=state.get("attempts", []),
        provider=state.get("provider"),
        model=state.get("model"),
        error_code=state.get("error_code"),
    )


def generation_result_adapter() -> TypeAdapter[GenerationResult]:
    """提供结果类型适配器，供持久化和 API 序列化复用。"""
    return TypeAdapter(GenerationResult)
