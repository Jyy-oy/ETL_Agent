# ETL-Agent 安全设计与威胁模型

状态：MVP 安全基线

## 1. 保护目标

- 防止未授权用户读取、修改或搬运项目数据。
- 防止 Maker 自批、职责槽混用、Capability 伪造和重放。
- 防止连接凭据、业务样本和 LLM API Key 泄露。
- 保证审批事实、执行事实和审计证据可追溯、不可静默篡改。
- 在预算超限、异常和部分失败时保护源端/目标端并支持回滚。

## 2. 信任边界

```text
浏览器/开发机
  ──认证边界──> FastAPI 控制面
                    ├─应用边界──> PostgreSQL/Redis/MinIO/Vault
                    ├─模型边界──> 远端百炼（只发送脱敏 Profile）
                    └─执行边界──> Tool Broker/Celery/SeaTunnel
                                      ──数据边界──> 源库/目标库
```

每次跨边界调用都必须经过认证、授权、输入校验、超时、审计和错误归一化。

## 3. 威胁与控制

| 威胁 | 控制措施 | MVP 验证 |
| --- | --- | --- |
| 越权访问项目 | 租户/项目上下文、Membership 服务端校验 | API 越权测试 |
| Maker 自批 | 审批人和申请人不可相同 | Approval 单元测试 |
| 职责槽混用 | RoleGrant 互斥规则和独立审批 | PDP/审批集成测试 |
| Capability 伪造 | Ed25519 验签、绑定主体/工具/环境/摘要 | 签名篡改测试 |
| Capability 重放 | Redis 原子消费、TTL、唯一 Token Digest | 并发重放测试 |
| 指纹漂移 | Commit 重新计算 Preparation 指纹 | 版本变更测试 |
| Prompt 注入 | Profile 脱敏、模型输出结构校验、工具白名单 | 恶意样本 Benchmark |
| Secret 泄露 | Vault KV、SecretRef、日志过滤、短时物化 | 日志扫描/Secret 测试 |
| 过度搬运 | 只读 Profile、行数/字节/时长预算 | 监督中断测试 |
| Outbox 重复 | 唯一事件 ID、幂等消费、状态机 | Worker 重试测试 |
| 数据质量污染 | 影子表、错误表、QualityContract、原子 Swap | 质量分流测试 |
| 审计篡改 | payload digest + 前序哈希 + 当前哈希 | 链校验测试 |

## 4. LLM 安全边界

- 发送百炼前只允许必要的业务语义、字段定义、统计摘要和脱敏样本。
- 连接字符串、密码、Token、身份证/手机号等敏感字段不得进入 Prompt。
- LLM 只能返回澄清问题、EtlPlan/HOCON 候选或诊断文本；不能直接调用工具、修改审批或发起执行。
- 结构化输出必须通过 JSON Schema、字段白名单、枚举归一化和 HOCON 编译校验。
- 模型、Prompt、策略、输入摘要、输出摘要和门禁结果都要写入 AgentRun 证据。
- 百炼不可用时必须有稳定错误、有限重试和人工恢复，不自动切换到未批准 Provider。

## 5. Secret 与密钥管理

- `.env` 只用于本地占位值，生产 Secret 不进入 Git、镜像或普通日志。
- 连接凭据保存为 Vault `SecretRef`，Worker 仅在执行窗口内解析，并在内存中短暂使用。
- Capability 私钥优先由 Vault Transit/KMS 托管；MVP PEM 文件只能用于本地开发。
- JWT 密钥、MinIO 密钥、百炼 API Key 和数据库密码必须按环境独立轮换。
- Secret 轮换不应修改 PipelineVersion；连接器在运行时通过引用取得当前凭据。

## 6. 安全事件响应

1. 发现 Secret、Capability 或审计异常后立即冻结相关 Provider/连接/策略。
2. 保留 request ID、AgentRun、Preparation、ExecutionRun 和 AuditEvent 证据。
3. 轮换受影响 Secret/密钥，撤销未消费 Capability，暂停相关 Outbox。
4. 评估数据范围，必要时清理影子表并回滚目标发布。
5. 完成根因、影响、修复和复测记录，更新威胁模型与 Benchmark。
