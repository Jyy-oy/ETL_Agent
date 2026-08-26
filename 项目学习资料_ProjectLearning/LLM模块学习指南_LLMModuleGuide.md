# ETL-Agent LLM 模块学习指南

## 1. 模块定位

LLM 是“候选生成器”，不是“执行引擎”。它可以理解自然语言、提取缺失参数、生成 EtlPlan/HOCON 候选和解释诊断；它不能决定项目权限、审批人、资源范围、预算或是否执行。

## 2. 调用边界

```text
API Request
  -> LangGraph State
  -> 脱敏 Profile Formatter
  -> LLMProvider Adapter
  -> 结构化 JSON
  -> Pydantic/JSON Schema
  -> HOCON 编译 + 确定性门禁
  -> PipelineVersion 或人工中断
```

当前开发阶段可以在 Windows/PyCharm 运行 FastAPI/LangGraph，通过 HTTPS 直接调用远端百炼；PostgreSQL/Redis/MinIO/Vault 运行在 Ubuntu VM。生产部署再将控制面放入 Linux 容器。

## 3. Provider Adapter

建议定义统一端口：

```python
class LLMProvider(Protocol):
    async def generate_structured(
        self, request: GenerationRequest, schema: type[BaseModel]
    ) -> GenerationResponse: ...

    async def ask_clarification(self, context: ClarificationContext) -> ClarificationQuestion: ...
```

百炼、DeepSeek、Qwen 或企业网关只作为 Adapter 实现；Workflow 不直接导入具体 SDK。配置至少包含 Provider、Base URL、模型、超时、重试和 Prompt 版本；Provider 还受 `LLM_MAX_PROMPT_BYTES` 限制，超限会在网络请求前返回稳定错误 `LLM_PROMPT_TOO_LARGE`。

## 4. 输入安全

- 发送前过滤密码、Token、连接串、手机号、邮箱和未批准的业务样本。
- 默认发送字段定义、统计摘要、脱敏样本和业务语义，不发送全量数据。
- 对输入长度、字段数量、样本数量和请求成本设置预算。
- 记录输入摘要和 Profile 版本，便于复现，不记录真实 Secret。

## 5. 输出安全

模型输出依次经过 JSON 解析、结构 Schema、枚举归一化、字段白名单、连接器能力检查、HOCON 语法和策略门禁。模型不得返回可以直接执行的任意脚本或工具调用。

非法输出处理：有限次数修复 → 重新校验 → 仍失败则人工中断。不能无限重试，也不能把非法文本当作“尽力执行”。

## 6. 可靠性和可观测性

- LLM 请求必须有连接/读取超时、有限重试和稳定错误码。
- 记录 Provider、Model、PromptVersion、延迟、Token/成本（若可得）、重试次数和结果摘要。
- 百炼不可用时保留 AgentRun 和 Checkpoint，支持之后人工恢复。
- 不自动切换到未批准的 Provider；Provider 降级策略由配置和策略版本控制。

## 7. 学习实验

1. 用 fake Provider 返回一个缺少目标表的结果，观察中断状态。
2. 返回未知字段和非法 HOCON，观察 Gate 拒绝和有限修复。
3. 修改 Profile 摘要后重新 Commit，观察指纹漂移拒绝。
4. 给输入加入“忽略安全规则”的 Prompt 注入，确认模型输出不能扩大工具权限。

真实 smoke test 仅在明确开启 `LLM_REAL_SMOKE_ENABLED=true`、使用非生产密钥和脱敏 Profile 后执行：

```bash
uv run pytest tests/integration/test_m3_runtime.py::test_real_bailian_provider_smoke -m integration
```
