# ETL-Agent API 契约基线

状态：MVP API 设计基线

## 1. 通用约定

- Base URL：`/api/v1`。
- 认证：`Authorization: Bearer <access_token>`；`/health` 可匿名但不得暴露 Secret 或内部堆栈。
- 写请求建议携带 `Idempotency-Key`；服务端保存键、请求摘要和结果引用，摘要不一致时拒绝复用。
- 异步操作返回 `202 Accepted`、资源 ID 和状态查询地址。
- 时间统一使用 UTC ISO-8601；ID 使用 UUID；枚举值使用小写稳定字符串。
- 列表接口使用游标分页，默认限制返回数量。

## 2. 错误结构

```json
{
  "code": "PREPARATION_FINGERPRINT_MISMATCH",
  "message": "Preparation facts changed; commit is rejected",
  "request_id": "01J...",
  "details": {
    "resource_type": "preparation",
    "resource_id": "..."
  }
}
```

`message` 面向用户，不能包含堆栈、密码、Token 或完整业务样本；详细原因写入带 `request_id` 的结构化日志和审计事件。

## 3. 端点矩阵

| 方法 | 路径 | 用途 | 主要角色 |
| --- | --- | --- | --- |
| GET | `/health` | 依赖就绪状态 | 匿名/运维 |
| POST | `/auth/register` | 开发环境创建本地用户，可选绑定项目 Checker 槽 | 匿名（仅 development） |
| POST | `/auth/login` | 签发 JWT 访问令牌 | 匿名 |
| GET | `/auth/me` | 查询当前用户 | Authenticated |
| POST | `/projects` | 创建项目并建立初始职责槽 | Authenticated |
| GET | `/projects` | 查询当前用户所属项目 | Authenticated |
| GET | `/projects/{project_id}/members` | 查询项目成员和职责槽 | Project Member |
| POST | `/projects/{project_id}/members` | 添加成员并分配职责槽 | Project Operator |
| GET | `/projects/{project_id}/connections` | 查询项目连接 | Project Member |
| GET | `/projects/{project_id}/pipelines` | 查询项目 Pipeline | Project Member |
| GET | `/pipelines/{pipeline_id}/versions` | 查询 Pipeline 版本 | Project Member |
| POST | `/connections` | 创建连接和 SecretRef | Maker |
| GET | `/connections/{connection_id}/profiles/latest` | 查询最近一次元数据 Profile | Project Member |
| PUT | `/connections/{connection_id}` | 更新连接配置 | Maker/Operator |
| POST | `/connections/{connection_id}/tests` | 测试连接 | Maker |
| POST | `/connections/{connection_id}/profiles` | 发起只读 Profile | Maker |
| POST | `/file-assets` | 上传并解析文件 | Maker |
| GET | `/projects/{project_id}/file-assets` | 查询项目文件资产 | Project Member |
| GET | `/file-assets/{asset_id}` | 查询文件资产和脱敏 Profile | Project Member |
| POST | `/pipelines` | 创建 Pipeline | Maker |
| POST | `/pipelines/{pipeline_id}/versions` | 创建可生成草稿版本 | Maker/Operator |
| POST | `/versions/{version_id}/generation` | 启动/恢复生成 | Maker |
| POST | `/agent-runs/{run_id}/answers` | 提交澄清回答 | Maker |
| GET | `/versions/{version_id}/design` | 查询 EtlPlan/HOCON/质量规则 | Project Member |
| POST | `/versions/{version_id}/prepare` | 冻结 Preparation | Maker |
| GET | `/projects/{project_id}/preparations` | 查询 Preparation 与审批槽 | Project Member |
| POST | `/approval-requests/{approval_id}/decisions` | 提交审批决策 | Checker 1/2 |
| POST | `/preparations/{preparation_id}/commit` | 校验并提交执行 | Operator |
| GET | `/execution-runs/{id}` | 查询状态和指标 | Project Member/Operator |
| POST | `/execution-runs/{id}/cancel` | 请求取消作业 | Operator |
| POST | `/execution-runs/{id}/rollback` | 受管回滚 | Operator |
| GET | `/execution-runs/{id}/supervision` | 查询运行预算和引擎状态快照 | Project Member/Operator |
| GET | `/execution-runs/{id}/quality` | 查询质量分流报告和影子/错误表 | Project Member/Operator |
| GET | `/projects/{project_id}/execution-runs` | 查询项目执行事实 | Project Member |
| POST | `/benchmarks/run` | 启动 Benchmark | Operator/Auditor |
| GET | `/projects/{project_id}/benchmarks` | 查询项目最近 Benchmark 历史摘要 | Project Member |
| GET | `/benchmarks/{benchmark_id}` | 查询单条 Benchmark 历史报告 | Project Member |

## 4. 状态码策略

| 状态码 | 场景 |
| --- | --- |
| 200 | 查询、幂等重复请求返回已有结果 |
| 201 | 同步创建资源；当前 M2.2 Profile 探查在受限线程中同步完成并返回快照 |
| 202 | Agent、执行等异步任务已受理；M6 首期 Benchmark 为有界同步合成计算，返回 `200`；Profile 后续接入 Celery 后切换为此状态码 |
| 400 | 请求结构或业务参数无效 |
| 401 | 未认证或令牌无效 |
| 403 | 已认证但无项目/角色/工具权限 |
| 404 | 资源不存在或对当前租户不可见 |
| 409 | 状态冲突、指纹漂移、版本不可变或幂等键复用冲突 |
| 422 | 结构化 Schema、HOCON 或质量规则校验失败 |
| 429 | LLM、连接器或 API 配额受限 |
| 500/503 | 内部错误或依赖不可用，返回 request ID |

M4.4/M5 已实现 Commit 与 ExecutionRun 查询：首次 Commit 返回 `201`，重复 Preparation 或相同 `Idempotency-Key` 返回 `200`；服务端会返回 `PREPARATION_FINGERPRINT_MISMATCH`、`APPROVALS_INCOMPLETE`、`CAPABILITY_ISSUE_FAILED` 等稳定错误码。Commit 响应只返回 `capability_token_digest`，不返回 Capability 原文。取消和回滚返回 `202`，状态通过 ExecutionRun、监督快照和质量报告查询；外部动作不由 API 直接调用。

M6/M6.1 新增控制台所需的项目级列表查询，列表仅按当前用户项目成员关系过滤。`POST /benchmarks/run` 在首期同步返回确定性合成报告（`200`），并将同一报告摘要写入 PostgreSQL `benchmark_runs`；`GET /projects/{project_id}/benchmarks` 默认返回最近 20 条，`GET /benchmarks/{benchmark_id}` 返回单条报告。报告绑定项目、Benchmark 级别、数据摘要、制品摘要、策略版本和环境；它不访问真实业务数据库，也不保存业务样本。

开发环境注册接口可额外提交 `project_code` 与 `project_role`（仅支持 `checker_1`、`checker_2`），注册成功后自动建立项目成员关系和对应职责槽；两个字段必须同时提供。未提供时仍按普通本地账号注册，不自动加入项目。

## 5. 版本和兼容性

新增字段默认可选；删除或改变语义必须经历兼容期。模型输出 Schema、EtlPlan、HOCON、QualityContract、RuntimeSupervisionContract 和事件 payload 都必须有版本号。破坏性 API 变更使用 `/api/v2` 或发布明确迁移窗口。
