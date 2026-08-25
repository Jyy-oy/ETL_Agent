# ETL-Agent 学习资料与工程沉淀变更记录

## 2026-08-25：阶段 0 网络与依赖健康检查通过

- 级别：开发环境/验证
- 类型：验收完成
- 来源：Windows 本地 `.env` 和 VM 依赖连通性复测
- 根因与解决过程：确认 Windows 到 VM 的 PostgreSQL、Redis、MinIO、Vault、SeaTunnel 端口均可达；协议级检查中 PostgreSQL `select 1`、Redis PING、MinIO health `200`、Vault health `200` 全部通过，阶段 0 的环境基线完成。
- 涉及文件：`项目学习资料_ProjectLearning/开发计划与里程碑_DevelopmentPlan.md`、根目录 `AGENTS.md`、本目录 `AGENTS.md`、`RequirementsDescription/整理版需求说明_RequirementsSpecification.md`

## 2026-08-25：执行阶段 0 环境与契约基线

- 级别：开发环境/工程基线
- 类型：执行记录
- 来源：首期开发手册阶段 0 完成定义
- 根因与解决过程：完成 VM Compose 依赖启动确认、源码/测试/迁移目录骨架、GitHub Actions CI 基线及本地 `uv`/Ruff/pytest/Mypy/锁文件检查；Windows 到 VM 的端口测试失败，原因是 VM Compose 仍绑定 `127.0.0.1`，阶段 0 暂标记为部分完成。
- 涉及文件：`src/etl_agent/`、`tests/`、`migrations/`、`.github/workflows/ci.yml`、`pyproject.toml`、`项目学习资料_ProjectLearning/开发计划与里程碑_DevelopmentPlan.md`

## 2026-08-25：SeaTunnel 单节点启动验证通过

- 级别：开发环境/验证
- 类型：修复完成
- 来源：VM `docker compose --profile data-plane ps seatunnel` 和最近日志
- 根因与解决过程：移除不受支持的显式 `master_and_worker` 参数后，SeaTunnel 2.3.10 容器稳定保持 `Up`，端口 `5801-5803` 已绑定，最近 10 秒无新增错误日志。
- 涉及文件：VM `docker-compose.yml`、根目录 `AGENTS.md`、本目录 `AGENTS.md`、`RequirementsDescription/整理版需求说明_RequirementsSpecification.md`

## 2026-08-25：纠正 SeaTunnel 单节点角色参数

- 级别：开发环境/故障排查
- 类型：配置纠正
- 来源：SeaTunnel 2.3.10 日志显示 `Not supported cluster role: master_and_worker`
- 根因与解决过程：确认该版本显式角色只接受 `master` 或 `worker`；不传 `-r` 时由引擎默认使用 `MASTER_AND_WORKER`。撤销上一条错误的 `-r master_and_worker` 修正，单节点 Compose 改为不传角色参数。
- 涉及文件：`docker-compose.yml`、`docs/operations/Ubuntu虚拟机部署_LocalVMDeployment.md`、根目录 `AGENTS.md`、本目录 `AGENTS.md`、`RequirementsDescription/整理版需求说明_RequirementsSpecification.md`

## 2026-08-25：定位 SeaTunnel 启动角色参数故障

- 级别：开发环境/故障排查
- 类型：配置修正
- 来源：VM SeaTunnel 日志显示 `Expected a value after parameter -r`
- 根因与解决过程：默认配置补齐后，SeaTunnel 继续因 Compose 仅传入 `-r` 而未传入角色值退出；将单节点开发配置修正为 `-r master_and_worker`，并要求重新创建容器。
- 涉及文件：`docker-compose.yml`、`docs/operations/Ubuntu虚拟机部署_LocalVMDeployment.md`、根目录 `AGENTS.md`、本目录 `AGENTS.md`、`RequirementsDescription/整理版需求说明_RequirementsSpecification.md`

## 2026-08-25：定位 SeaTunnel 配置目录故障

- 级别：开发环境/故障排查
- 类型：修复指引
- 来源：VM `docker compose --profile data-plane logs --tail=200 seatunnel`
- 根因与解决过程：日志显示 `seatunnel-cluster.sh` 找不到 `/opt/seatunnel/config/jvm_options`；确认 Compose 的宿主机配置目录为空并覆盖了镜像内置配置。停止重启中的容器，补充从镜像提取默认配置再重启的操作，并同步部署手册。
- 涉及文件：`docs/operations/Ubuntu虚拟机部署_LocalVMDeployment.md`、根目录 `AGENTS.md`、本目录 `AGENTS.md`、`RequirementsDescription/整理版需求说明_RequirementsSpecification.md`

## 2026-08-25：更新 VM 基础设施运行状态

- 级别：开发环境/故障排查
- 类型：状态更新
- 来源：项目负责人提供 Ubuntu VM `docker ps` 输出
- 根因与解决过程：确认 PostgreSQL、Redis、MinIO、Vault 已由 Compose 启动且健康；SeaTunnel 2.3.10 容器持续 `Restarting (1)`，待通过容器日志和挂载配置进一步定位
- 涉及文件：根目录 `AGENTS.md`、本目录 `AGENTS.md`、`RequirementsDescription/整理版需求说明_RequirementsSpecification.md`

## 2026-08-25：核对并校正 Agent 文档状态

- 级别：文档治理/安全提醒
- 类型：校正
- 来源：核对根目录和学习资料目录 `AGENTS.md` 与当前仓库、需求基线及开发环境记录
- 根因与解决过程：修正已删除 `AGENT.md` 的残留阅读指引，明确当前尚无 `src/`、`tests/` 等业务实现，明确 VM Docker 尚无运行实例，并将控制面描述调整为后续开发环境；同时发现本机 `.env` 存在非占位 LLM 密钥值，已在交付说明中要求轮换，未修改本机配置
- 涉及文件：根目录 `AGENTS.md`、本目录 `AGENTS.md`

## 2026-08-25：合并项目根目录 Agent 入口

- 级别：文档治理
- 类型：整理
- 来源：统一项目根目录 Agent 自动发现入口，减少主入口与增量约定之间的重复跳转
- 根因与解决过程：将根目录 `AGENT.md` 的项目定位、技术边界、导航和工程规则合并到 `AGENTS.md`，再合并原 `AGENTS.md` 的执行与文档约定；删除根目录重复的 `AGENT.md`
- 涉及文件：根目录 `AGENTS.md`、删除的根目录 `AGENT.md`、本目录 `AGENTS.md`

## 2026-08-25：合并学习资料目录入口

- 级别：文档治理
- 类型：整理
- 来源：统一 Agent 使用入口，减少入口文档重复和维护漂移
- 根因与解决过程：将本目录原 `AGENT.md`、`AGENTS.md` 和 `README.md` 的入口、导航、学习顺序和执行约定合并到唯一权威文件 `AGENTS.md`；删除两个重复文件，并同步更新项目根目录和 `docs/README.md` 的链接
- 涉及文件：`项目学习资料_ProjectLearning/AGENTS.md`、根目录 `AGENT.md`、`docs/README.md`

## 2026-08-25：建立项目学习资料目录

- 级别：文档/工程基础
- 类型：新增
- 来源：参考 `smart-audiobook-assistant` 的 `AGENT.md`、`AGENTS.md`、学习笔记、架构亮点和开发计划组织方式
- 根因与解决过程：ETL-Agent 原有架构和开发文档较完整，但缺少面向初学者的扫盲、学习路径、代码阅读指南、LLM 专题、故障排查和 Agent 入口索引；新增 `项目学习资料_ProjectLearning/`，并将普通文档按中文+英文关键词命名
- 涉及文件：本目录全部文档、`docs/README.md`

## 记录规则

后续每次重要修复、优化、配置变更或学习结论按“级别、类型、来源、根因与解决过程、涉及文件”记录。未解决事项单独标记为待处理，不把失败假设写成完成结论。
