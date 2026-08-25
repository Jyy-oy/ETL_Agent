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
| GET | `/projects/{project_id}/connections` | 查询项目连接 | Project Member |
| POST | `/connections` | 创建连接和 SecretRef | Maker |
| PUT | `/connections/{id}` | 更新连接配置 | Maker/Operator |
| POST | `/connections/{id}/tests` | 测试连接 | Maker |
| POST | `/connections/{id}/profiles` | 发起只读 Profile | Maker |
| POST | `/file-assets` | 上传并解析文件 | Maker |
| POST | `/pipelines` | 创建 Pipeline | Maker |
| POST | `/versions/{version_id}/generation` | 启动/恢复生成 | Maker |
| POST | `/agent-runs/{run_id}/answers` | 提交澄清回答 | Maker |
| GET | `/versions/{version_id}/design` | 查询 EtlPlan/HOCON/质量规则 | Project Member |
| POST | `/versions/{version_id}/prepare` | 冻结 Preparation | Maker |
| POST | `/approval-requests/{approval_id}/decisions` | 提交审批决策 | Checker 1/2 |
| POST | `/preparations/{preparation_id}/commit` | 校验并提交执行 | Operator |
| GET | `/execution-runs/{id}` | 查询状态和指标 | Project Member/Operator |
| POST | `/execution-runs/{id}/cancel` | 请求取消作业 | Operator |
| POST | `/execution-runs/{id}/rollback` | 受管回滚 | Operator |
| POST | `/benchmarks/run` | 启动 Benchmark | Security/Auditor |

## 4. 状态码策略

| 状态码 | 场景 |
| --- | --- |
| 200 | 查询、幂等重复请求返回已有结果 |
| 201 | 同步创建资源 |
| 202 | Agent、Profile、执行和 Benchmark 等异步任务已受理 |
| 400 | 请求结构或业务参数无效 |
| 401 | 未认证或令牌无效 |
| 403 | 已认证但无项目/角色/工具权限 |
| 404 | 资源不存在或对当前租户不可见 |
| 409 | 状态冲突、指纹漂移、版本不可变或幂等键复用冲突 |
| 422 | 结构化 Schema、HOCON 或质量规则校验失败 |
| 429 | LLM、连接器或 API 配额受限 |
| 500/503 | 内部错误或依赖不可用，返回 request ID |

## 5. 版本和兼容性

新增字段默认可选；删除或改变语义必须经历兼容期。模型输出 Schema、EtlPlan、HOCON、QualityContract、RuntimeSupervisionContract 和事件 payload 都必须有版本号。破坏性 API 变更使用 `/api/v2` 或发布明确迁移窗口。
