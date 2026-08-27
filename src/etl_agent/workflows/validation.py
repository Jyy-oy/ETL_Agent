"""EtlPlan 的 Schema、HOCON 和确定性门禁校验。"""

import json
from copy import deepcopy
from typing import Any

from jsonschema import Draft202012Validator
from pydantic import ValidationError
from pyhocon import ConfigFactory

from etl_agent.domain.generation import (
    EtlPlan,
    GenerationRequest,
    TransformOperation,
    ValidationIssue,
)

# 首期真实数据面只会把这两类转换编译成可执行的 MySQL 查询。
_SUPPORTED_RUNTIME_TRANSFORMS = {TransformOperation.FILTER, TransformOperation.CAST}

# 这些关键词代表当前编译器尚未具备的语义；在门禁层明确拒绝，避免把复杂需求静默降级为全量直迁。
_UNSUPPORTED_REQUEST_MARKERS = (
    ("增量", "增量水位执行"),
    ("incremental", "增量水位执行"),
    ("cdc", "CDC 实时同步"),
    ("脱敏", "字段脱敏"),
    ("mask", "字段脱敏"),
    ("填充空值", "空值填充"),
    ("fill_null", "空值填充"),
    ("join", "Join 多表关联"),
    ("关联", "Join 多表关联"),
    ("聚合", "聚合计算"),
    ("aggregate", "聚合计算"),
)


def _issue(code: str, message: str, path: list[str | int] | None = None) -> ValidationIssue:
    """创建统一格式的校验问题，避免把第三方异常原文直接暴露给 API。"""
    return ValidationIssue(code=code, message=message, path=path or [])


def _requested_marker(text: str, marker: str) -> bool:
    """判断业务需求是否真正请求某个未支持能力，忽略常见的否定表达。"""
    normalized = text.lower()
    start = 0
    while True:
        index = normalized.find(marker, start)
        if index < 0:
            return False
        prefix = normalized[max(0, index - 4) : index]
        if not any(
            negation in prefix for negation in ("不", "不要", "无需", "无须", "禁止", "not ")
        ):
            return True
        start = index + len(marker)


def _normalize_legacy_mapping_transform(payload: dict[str, Any]) -> dict[str, Any]:
    """兼容模型把字段转换误写成嵌套 TransformRule 的历史候选格式。"""
    normalized = deepcopy(payload)
    mappings = normalized.get("field_mappings")
    if not isinstance(mappings, list):
        return normalized
    for mapping in mappings:
        if not isinstance(mapping, dict):
            continue
        transform = mapping.get("transform")
        if not isinstance(transform, dict):
            continue
        operation = transform.get("operation")
        source_fields = transform.get("source_fields", [])
        target_field = transform.get("target_field")
        parameters = transform.get("parameters", {})
        # 只有结构完全对应当前字段映射时才归一化，其他对象继续交给 Schema 拒绝。
        if (
            operation in {"rename", "cast"}
            and source_fields in ([], [mapping.get("source_field")])
            and target_field in (None, mapping.get("target_field"))
            and parameters in ({}, None)
        ):
            mapping["transform"] = operation
    return normalized


def parse_plan_payload(payload: Any) -> tuple[EtlPlan | None, list[ValidationIssue]]:
    """解析候选 JSON 并执行 Pydantic 与 JSON Schema 双重结构校验。"""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None, [_issue("INVALID_JSON", "候选内容不是合法 JSON")]
    if not isinstance(payload, dict):
        return None, [_issue("INVALID_OBJECT", "候选内容必须是 JSON 对象")]
    # LLM 偶尔会将 field_mappings.transform 误当作完整 TransformRule；
    # 这里仅兼容结构无歧义的 rename/cast，不放宽任意脚本或未知操作。
    payload = _normalize_legacy_mapping_transform(payload)
    schema = EtlPlan.model_json_schema()
    schema_errors = sorted(
        Draft202012Validator(schema).iter_errors(payload), key=lambda error: list(error.path)
    )
    if schema_errors:
        return None, [
            _issue(
                "SCHEMA_INVALID",
                error.message[:500],
                [str(item) if not isinstance(item, int) else item for item in error.path],
            )
            for error in schema_errors[:20]
        ]
    try:
        return EtlPlan.model_validate(payload), []
    except ValidationError as exc:
        return None, [
            _issue("SCHEMA_INVALID", error["msg"][:500], list(error["loc"]))
            for error in exc.errors()
        ]


def compile_hocon(hocon: str) -> tuple[Any | None, ValidationIssue | None]:
    """编译 HOCON 候选，并拒绝潜在的脚本或命令执行配置。"""
    try:
        # PyHOCON 0.3.x 通过 parse_string 的 resolve 参数执行替换解析。
        config = ConfigFactory.parse_string(hocon, resolve=True)
    except Exception as exc:
        del exc
        return None, _issue("HOCON_INVALID", "HOCON 配置无法编译")
    forbidden_keys = {"command", "shell", "script", "exec", "python", "tool"}

    def walk(value: Any) -> bool:
        """递归检查配置键名，阻断任意脚本或工具调用入口。"""
        if hasattr(value, "items"):
            for key, child in value.items():
                if str(key).lower() in forbidden_keys:
                    return True
                if walk(child):
                    return True
        elif isinstance(value, list):
            return any(walk(child) for child in value)
        return False

    if walk(config):
        return None, _issue("HOCON_FORBIDDEN_KEY", "HOCON 包含禁止的脚本或工具配置")
    return config, None


def deterministic_gate(
    plan: EtlPlan,
    request: GenerationRequest,
    hocon_config: Any,
) -> list[ValidationIssue]:
    """校验 Profile 引用、字段边界和服务端运行预算，不采信模型扩大权限。"""
    issues: list[ValidationIssue] = []
    source_profiles = {profile.profile_id: profile for profile in request.source_profiles}
    target_profiles = {profile.profile_id: profile for profile in request.target_profiles}
    source = source_profiles.get(plan.source.profile_id)
    target = target_profiles.get(plan.target.profile_id)
    if source is None:
        issues.append(
            _issue("SOURCE_PROFILE_FORBIDDEN", "源 Profile 不在本次请求授权范围内", ["source"])
        )
    elif source.connection_id != plan.source.connection_id:
        issues.append(
            _issue("SOURCE_CONNECTION_MISMATCH", "源 Profile 与连接引用不匹配", ["source"])
        )
    if target is None:
        issues.append(
            _issue("TARGET_PROFILE_FORBIDDEN", "目标 Profile 不在本次请求授权范围内", ["target"])
        )
    elif target.connection_id != plan.target.connection_id:
        issues.append(
            _issue("TARGET_CONNECTION_MISMATCH", "目标 Profile 与连接引用不匹配", ["target"])
        )
    budget = request.max_runtime_budget
    candidate = plan.runtime_budget
    budget_pairs = (
        ("max_input_records", candidate.max_input_records, budget.max_input_records),
        ("max_output_bytes", candidate.max_output_bytes, budget.max_output_bytes),
        ("max_runtime_seconds", candidate.max_runtime_seconds, budget.max_runtime_seconds),
        (
            "max_output_amplification",
            candidate.max_output_amplification,
            budget.max_output_amplification,
        ),
        ("max_rejection_rate", candidate.max_rejection_rate, budget.max_rejection_rate),
    )
    for name, value, maximum in budget_pairs:
        if value > maximum:
            issues.append(
                _issue("BUDGET_EXCEEDED", f"模型请求扩大了 {name} 上限", ["runtime_budget", name])
            )
    if source is not None and source.fields:
        allowed = set(source.fields)
        for index, mapping in enumerate(plan.field_mappings):
            if mapping.source_field not in allowed:
                issues.append(
                    _issue(
                        "SOURCE_FIELD_NOT_FOUND",
                        "映射引用了不存在的源字段",
                        ["field_mappings", index],
                    )
                )
    if target is not None and target.fields:
        allowed = set(target.fields)
        for index, mapping in enumerate(plan.field_mappings):
            if mapping.target_field not in allowed:
                issues.append(
                    _issue(
                        "TARGET_FIELD_NOT_FOUND",
                        "映射引用了不存在的目标字段",
                        ["field_mappings", index],
                    )
                )
        for field in plan.quality_contract.required_fields:
            if field not in allowed:
                issues.append(
                    _issue(
                        "QUALITY_FIELD_NOT_FOUND",
                        "质量规则引用了不存在的目标字段",
                        ["quality_contract"],
                    )
                )
    if hocon_config is None:
        issues.append(_issue("HOCON_CONFIG_MISSING", "未找到已经编译的 HOCON 配置"))

    # 先检查冻结需求和候选转换，阻止模型把未实现语义改写成看似成功的全量作业。
    for marker, feature_name in _UNSUPPORTED_REQUEST_MARKERS:
        if _requested_marker(request.business_request, marker):
            issues.append(
                _issue(
                    "UNSUPPORTED_DATA_PLANE_FEATURE",
                    f"当前真实数据面暂不支持{feature_name}，不会生成可执行版本",
                    ["business_request"],
                )
            )
            break
    for index, transform in enumerate(plan.transforms):
        if transform.operation not in _SUPPORTED_RUNTIME_TRANSFORMS:
            issues.append(
                _issue(
                    "UNSUPPORTED_DATA_PLANE_TRANSFORM",
                    f"当前真实数据面暂不支持 {transform.operation.value} 转换",
                    ["transforms", index, "operation"],
                )
            )
    return issues


def validate_plan_payload(
    payload: Any,
    request: GenerationRequest,
) -> tuple[EtlPlan | None, list[ValidationIssue]]:
    """执行完整校验；只有无任何问题时才返回可用 EtlPlan。"""
    plan, issues = parse_plan_payload(payload)
    if plan is None:
        return None, issues
    hocon_config, hocon_issue = compile_hocon(plan.hocon)
    if hocon_issue is not None:
        issues.append(hocon_issue)
    issues.extend(deterministic_gate(plan, request, hocon_config))
    return (plan if not issues else None), issues
