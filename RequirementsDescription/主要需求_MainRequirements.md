### 生产级 ETL-Agent 数据集成平台

- 项目目标：实现一个面向企业数据集成场景的生产级 ETL-Agent 控制面平台，结合 LangGraph 与不可绕过的 Agent Harness 内核，覆盖自然语言需求解析、受管异构元数据安全探查、结构化 EtlPlan 与 SeaTunnel HOCON 配置生成、确定性门禁校验、不可变制品版本化、四眼职责审批（Maker-Checker）、单次 Capability 令牌授权、Celery 异步调度与 SeaTunnel 执行、运行监督与质量契约分流闭环
- 交付形式：提供可运行 Vue 前端控制台、FastAPI 控制面服务、Celery 异步 Worker/Beat 服务、PostgreSQL/Redis 数据库脚本、SeaTunnel Zeta 集成配置、仿真评测 Benchmark 数据集、接口说明文档与完整业务演示链路

#### 2.8.1 开发范围

- 基础能力：用户认证鉴权、多租户项目管理、项目成员与角色分配、敏感凭据受管加密存储（SecretProvider / Vault KV v2）
- 数据源与元数据探查：异构数据库接入（MySQL、PostgreSQL、Oracle、Doris、ClickHouse）、文件与 API 数据源接入（CSV、Excel、JSON、Parquet、S3/MinIO、REST API）、只读元数据安全探查（Schema、字段类型、近似统计、脱敏样本采样）
- 智能生成与编排能力：自然语言需求解析、LangGraph 中断与 Checkpoint 恢复、多模型 Provider 适配（OpenAI 兼容接口 / DeepSeek / Qwen）、EtlPlan 与 SeaTunnel HOCON 候选生成、语法与 Schema 对齐校验、有限自动修复
- Harness 安全内核能力：策略决策点（PDP / P0-P3 风险评级）、`prepare → approve → commit` 三阶段执行协议、四眼原则独立审批（Maker-Checker）、Ed25519 签名短时单次 Capability 令牌、防重放 Guard、事务 Outbox 可靠投递、证据账本（Evidence Ledger）追加与防篡改
- 数据面执行与治理：Celery 异步任务执行、SeaTunnel 引擎对接、影子表与原子 Swap 发布、错误数据分流与质量契约（QualityContract）、运行监督（行数/字节/时长/放大比/拒绝率预算监控）、任务取消与回滚
- 评测与安全进化：分层仿真（L0 静态注入、L1 模拟故障、L2 真实链路）、版本化 Benchmark 自动化评测、受控安全进化闭环

#### 2.8.2 用户角色与职责权限

- `数据工程师（申请人 / Maker）`：登记数据源连接、发起元数据探查、输入自然语言需求、调试并生成 Pipeline 候选方案、提交不可变版本与审批申请
- `数据审批人（Checker 1）`：审查需求描述、Schema 映射、脱敏前后对比、质量规则与 DAG 结构，执行数据维度审批
- `安全审批人（Checker 2）`：审查资源范围、数据敏感分级、执行预算、Secret 引用与回滚方案，执行安全维度审批；管理 Benchmark 与安全进化
- `系统操作员`：在所有审批通过后执行 Commit 操作、触发受管执行、监控运行状态、执行任务暂停/取消/回滚与凭据轮转
- `审计人员`：查看执行历史、审计事件、签名证据账本、数据质量报告与 Benchmark 评测大盘
- 强制职责分离规则：申请人禁止自批，高风险操作禁止同一人占用多个审批职责槽

#### 2.8.3 页面需求

- `总览工作台`：展示当前项目连接数、Pipeline 数量、待审批门禁概况、运行成功率与核心资源分布
- `数据连接与资产页`：
  - 连接管理：维护数据库连接与文件数据源，支持连接测试与 Secret 密文保存
  - 元数据探查：展示数据表 Schema、字段类型、主键、近似统计指标与脱敏样本视图
  - 文件资产管理：上传与解析本地/对象存储文件，提取文件头与字段推断
- `Pipeline Studio（设计与生成工作台）`：
  - 需求输入区：输入自然语言业务需求、选择源端与目标端 Profile
  - Agent 对话与澄清区：展示 LangGraph 状态机执行步骤、需求澄清提问、交互表单与有限修复过程
  - 方案与配置审查区：展示生成的 EtlPlan 结构化设计、SeaTunnel HOCON 配置、DAG 拓扑图、字段映射 Diff 与脱敏前后结构对比
  - 版本与审批提交区：将通过门禁的方案冻结为不可变版本（SHA256 摘要），提交审批申请并配置调度策略
- `运行中心`：
  - 准备与审批面板：展示 Preparation 准备单、影响范围、回滚方案、审批人列表与审批决策入口
  - 执行与监控：展示 ExecutionRun 列表、实时运行状态、读取/写入/过滤行数、字节数、执行时长、吞吐量与实时日志
  - 质量与诊断报告：展示有效数据写入统计、错误表分流明细、错误码分布、质量契约命中与可解释根因诊断
  - 运维操作：提供任务取消、安全重跑、影子表回滚与清理入口
- `安全治理与 Benchmark 页`：
  - Benchmark 大盘：运行自动化 Benchmark 测试集，展示生成准确率、Schema 覆盖率、P0/P1 安全拦截率与延迟对比
  - 安全进化管理：展示 Prompt/策略改进候选、审查报告与小流量/影子授权开关

#### 2.8.4 核心协议与系统规则

- 控制面与数据面分离：FastAPI + LangGraph + Celery + PostgreSQL 负责控制面治理，Apache SeaTunnel 负责数据面搬运，控制面不直连搬运海量业务数据
- 三层状态隔离规则：
  - 对话状态（Conversation State）：记录自然语言交互历史
  - Agent 状态机（Workflow State）：LangGraph 管理澄清、生成、门禁校验与有限修复，通过 PostgreSQL Checkpoint 跨请求持久化与恢复
  - 底层执行状态（Execution State）：记录实际任务运行实例（ExecutionRun）、SeaTunnel 作业 ID 与指标
- `prepare → approve → commit` 三阶段执行协议：
  1. `Prepare` 阶段：推导输入指纹、资源范围、影响范围、数据分级、运行预算与回滚方案，冻结生成 Preparation 单，不产生外部副作用
  2. `Approve` 阶段：具名审批人基于冻结事实执行独立审批，强制四眼原则与职责分离
  3. `Commit` 阶段：服务端重新比对指纹与审批事实，签发 Ed25519 签名短时单次 Capability 令牌，经 Tool Broker 在同一 PostgreSQL 事务中原子创建 ExecutionRun 与 Outbox 投递命令
- 防重放与单次能力规则：Capability 令牌绑定工具、主体、环境与制品指纹，有效期 5 分钟且由 Replay Guard 保证只能消费一次
- 质量分流与原子发布规则：
  - 有效数据写入影子表，错误数据按质量契约分流至专用错误表并打标错误码
  - 校验通过后通过原子 Swap（如 Doris `REPLACE TABLE ... swap=true`）将影子表替换为正式表

#### 2.8.5 前端模块要求

- `连接与 Profile 管理模块`：处理数据连接表单、连通性测试、元数据安全探查与脱敏样本渲染
- `Pipeline Studio 交互模块`：处理自然语言提示词输入、LangGraph 澄清交互、HOCON 语法高亮与 DAG 图形化渲染
- `不可变版本与 Diff 模块`：对比不同 Pipeline 版本的 Schema、HOCON 配置与质量规则差异
- `审批流与准备单模块`：展示 Preparation 冻结事实、影响分析、回滚方案与具名审批操作卡片
- `运行监控与指标模块`：轮询或订阅执行状态、展示吞吐曲线、质量分流统计与可解释诊断面板
- `安全治理与评测模块`：展示 Benchmark 测评用例集、触发评测并渲染安全指标大盘

#### 2.8.6 后端与 Harness 核心模块要求

- `API 控制面服务`：提供连接、元数据、设计生成、审批、执行与运行监控 RESTful 接口
- `LangGraph 编排引擎`：构建可恢复的 ETL 生成状态机（意图解析 → 元数据探查 → 生成 EtlPlan/HOCON → 确定性门禁 → 试运行 → 准备审批）
- `Harness 安全执行内核`：
  - `Policy Decision Point (PDP)`：根据 ToolIntent、资源范围、环境与数据分级输出 P0-P3 风险决策与审批要求
  - `Capability Issuer & Verifier`：基于 Ed25519 算法签发与校验单次执行能力令牌
  - `Tool Broker & Replay Guard`：作为所有副作用执行的唯一出口，拦截未授权调用与重放请求
  - `Transactional Outbox`：保证本地业务事实与 Worker 任务投递的原子落库
  - `Evidence Ledger`：维护关键事件的哈希链与签名检查点，保证审计证据不可篡改
- `Celery Worker 执行服务`：接收验签后的受管命令，物化 Secret，调用 SeaTunnel 执行作业并回传状态与指标
- `运行时监督引擎`：依据 `RuntimeSupervisionContract` 实时监控记录数、字节数、时长、输出放大比与拒绝率，触发预警或硬中断

#### 2.8.7 数据表要求

- `users`：`id`、`username`、`display_name`、`email`、`status`、`created_at`、`updated_at`
- `projects`：`id`、`name`、`code`、`description`、`created_at`、`updated_at`
- `project_memberships`：`id`、`project_id`、`user_id`、`role`、`created_at`、`updated_at`
- `project_role_grants`：`id`、`project_id`、`user_id`、`role_slot`、`created_at`、`updated_at`
- `pipelines`：`id`、`project_id`、`name`、`code`、`description`、`status`、`created_at`、`updated_at`
- `pipeline_versions`：`id`、`pipeline_id`、`version_number`、`etl_plan_json`、`hocon_text`、`artifact_digest`、`is_immutable`、`created_at`、`updated_at`
- `pipeline_artifacts`：`id`、`version_id`、`artifact_type`、`artifact_digest`、`content`、`created_at`
- `agent_runs`：`id`、`version_id`、`thread_id`、`prompt`、`status`、`step_count`、`error_message`、`created_at`、`updated_at`
- `approval_requests`：`id`、`preparation_id`、`version_id`、`required_role`、`status`、`decision`、`approver_id`、`decided_at`、`created_at`、`updated_at`
- `execution_runs`：`id`、`version_id`、`preparation_id`、`capability_token_digest`、`status`、`engine_job_id`、`input_records`、`output_records`、`error_records`、`bytes_processed`、`started_at`、`finished_at`、`created_at`、`updated_at`
- `file_assets`：`id`、`project_id`、`file_name`、`file_path`、`file_size`、`file_format`、`schema_json`、`created_at`、`updated_at`
- `runtime_supervision_snapshots`：`id`、`execution_run_id`、`metrics_json`、`decision`、`action_taken`、`created_at`
- `audit_events`：`id`、`project_id`、`actor_id`、`event_type`、`resource_type`、`resource_id`、`payload_digest`、`prev_event_hash`、`event_hash`、`created_at`

#### 2.8.8 接口要求

- `GET /health`：检查系统组件与依赖就绪状态
- `GET /api/v1/projects/{project_id}/connections`：查询项目数据源连接列表
- `POST /api/v1/connections` / `PUT /api/v1/connections/{id}`：创建与编辑数据源连接
- `POST /api/v1/connections/{id}/tests`：测试数据源连通性
- `POST /api/v1/connections/{id}/profiles`：发起只读元数据探查与脱敏样本采集
- `POST /api/v1/file-assets`：上传并解析异构文件数据源
- `POST /api/v1/pipelines`：创建 Pipeline 项目定义
- `POST /api/v1/versions/{version_id}/generation`：输入自然语言需求，触发 LangGraph 候选生成
- `POST /api/v1/agent-runs/{run_id}/answers`：提交澄清问题回答，恢复 Agent 状态机
- `GET /api/v1/versions/{version_id}/design`：查询生成的 EtlPlan、HOCON 配置、DAG 拓扑与质量契约
- `POST /api/v1/versions/{version_id}/prepare`：生成 Preparation 准备单，推导影响范围与回滚方案
- `POST /api/v1/approval-requests/{approval_id}/decisions`：提交具名审批决策（approve / reject）
- `POST /api/v1/preparations/{preparation_id}/commit`：校验审批与指纹，签发 Capability 并原子提交执行
- `GET /api/v1/execution-runs/{id}`：查询任务执行状态、指标与质量报告
- `POST /api/v1/execution-runs/{id}/cancel`：请求取消运行中作业
- `POST /api/v1/execution-runs/{id}/rollback`：触发受管影子表回滚与清理
- `POST /api/v1/benchmarks/run`：触发自动化安全与准确率 Benchmark 评测

#### 2.8.9 质量契约与运行监督规则

- 质量契约（QualityContract）：
  - 声明字段级清洗过滤规则（如非空校验、正数校验、邮箱脱敏格式）
  - 配置错误数据流向与错误码标签（ErrorCode）
  - 数据面执行时将未命中规则的数据分流写入错误表，记录原始值与违规原因
- 运行时监督契约（RuntimeSupervisionContract）：
  - 冻结最大读取行数、最大写入字节数、最大执行时长预算
  - 监控输出放大比与错误拒绝率阈值
  - 越线时按策略触发预警、硬中断（Kill Job）或隔离告警

#### 2.8.10 核心业务流程

- `端到端需求生成与受管执行流程`：
  1. 数据工程师配置源库（MySQL）与目标库（Doris）连接，生成受管元数据 Profile
  2. 数据工程师在 Studio 输入自然语言 ETL 需求
  3. LangGraph 状态机解析需求，必要信息缺失时触发中断提问，工程师补充回答后从 Checkpoint 恢复
  4. 模型生成结构化 EtlPlan 与 SeaTunnel HOCON 配置，通过语法编译器与确定性门禁校验
  5. 冻结生成不可变 PipelineVersion 并计算 SHA256 摘要
  6. 调用 Prepare 接口生成准备单，PDP 计算风险等级并分配数据审批人与安全审批人职责槽
  7. 数据审批人与安全审批人分别完成独立审批（四眼原则）
  8. 操作员调用 Commit 接口，服务端校验审批与指纹后签发单次 Capability，写入 Outbox
  9. Celery Worker 消费任务，验签后调用 SeaTunnel 执行搬运
  10. SeaTunnel 将有效数据写入影子表，错误数据写入错误表，监督引擎持续监控预算
  11. 质量报告通过后，Doris 执行原子 Swap 发布正式表，关键链路生成防篡改证据账本
- `任务失败诊断与安全回滚流程`：
  1. 执行异常或监督超限时触发中断，记录失败快照
  2. 诊断服务提取错误堆栈，生成可解释根因说明与修复建议
  3. 操作员发起回滚指令，Harness 验证权限后调用受管清理逻辑恢复原状态

#### 2.8.11 验收标准

- 能在项目内配置独立的数据工程师、数据审批人与安全审批人，系统强制拦截申请人自批与职责混用
- 能接入异构数据库与文件数据源，完成安全元数据探查并展示脱敏样本，系统全链路无敏感明文泄露
- 能通过自然语言需求触发 LangGraph 状态机生成 EtlPlan 与 SeaTunnel HOCON 配置，并在缺少参数时正确中断与恢复
- 能通过确定性门禁对生成的 Pipeline 配置进行语法与 Schema 校验，通过后生成不可变版本摘要
- 能完整执行 `prepare → approve → commit` 三阶段协议，成功生成准备单、完成四眼审批并签发单次 Capability
- 能通过 Celery Worker 调度 SeaTunnel 完成数据搬运，实现有效数据影子表写入与错误数据质量分流
- 能在数据达标后通过原子 Swap 完成正式表发布，并在运行中心查看实时指标与质量报告
- 能在超预算或异常情况下触发运行时监督与可解释根因诊断，并支持受管回滚
- 能运行自动化 Benchmark 评测集，输出准确率与安全拦截率评测报告