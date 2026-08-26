<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

type AnyRecord = Record<string, unknown>
type Project = { id: string; code: string; name: string }
type Connection = {
  id: string
  code: string
  name: string
  connection_type: string
  host: string
  port: number
  database_name?: string | null
  username?: string | null
  secret_ref?: string
  status: string
}
type Profile = { id: string; connection_id: string; fingerprint: string; estimated_row_count?: number; schema_json?: AnyRecord; schema_snapshot?: AnyRecord }
type ProfileOption = { id: string; label: string }
type Pipeline = { id: string; code: string; name: string; status: string }
type Version = { id: string; version_number: number; status: string; immutable: boolean; artifact_digest?: string }
type Preparation = { id: string; pipeline_version_id: string; status: string; risk_level: string; required_roles: string[]; approval_requests: Approval[]; expires_at: string }
type Approval = { id: string; required_role: string; status: string; decision?: string; comment?: string }
type Execution = { id: string; preparation_id: string; status: string; quality_status: string; publish_status: string; rollback_status: string; metrics: AnyRecord; error_code?: string; error_detail?: string }
type BenchmarkReport = {
  benchmark_id: string
  status: string
  project_id: string
  level: string
  dataset_rows: number
  repeat: number
  seed: number
  dataset_digest: string
  metrics: AnyRecord
  artifact_digest: string
  policy_version: string
  environment: string
  started_at: string
  completed_at: string
}

const token = ref(localStorage.getItem('etl_agent_token') ?? '')
const username = ref('')
const password = ref('')
const displayName = ref('')
const registerProjectCode = ref('')
const registerProjectRole = ref('')
const registerMode = ref(false)
const message = ref('')
const busy = ref(false)
const activeTab = ref('overview')
const projects = ref<Project[]>([])
const selectedProjectId = ref('')
const connections = ref<Connection[]>([])
const editingConnectionId = ref<string | null>(null)
const profiles = ref<Record<string, Profile>>({})
const profileTableNames = ref<Record<string, string>>({})
const pipelines = ref<Pipeline[]>([])
const versions = ref<Version[]>([])
const preparations = ref<Preparation[]>([])
const executions = ref<Execution[]>([])
const benchmarkReport = ref<BenchmarkReport | null>(null)
const benchmarkHistory = ref<BenchmarkReport[]>([])

// 开发机通过 VM 暴露的端口访问合成 MySQL，不能使用浏览器所在机器的 localhost。
const syntheticMysqlHost = import.meta.env.VITE_SYNTHETIC_MYSQL_HOST ?? '192.168.181.128'
const connectionForm = ref({ code: 'synthetic_mysql', name: '合成 MySQL', connection_type: 'mysql', host: syntheticMysqlHost, port: 3306, database_name: 'etl_demo', username: 'etl_demo', secret_ref: 'secret/data/etl-agent/mysql' })
const projectForm = ref({ code: 'etl_learning', name: 'ETL 学习项目' })
const pipelineForm = ref({ code: 'orders_sync', name: '订单同步', business_request: '同步订单到目标表，保留 id 和 amount' })
const sourceProfileId = ref('')
const targetProfileId = ref('')
const selectedVersionId = ref('')
const selectedPreparationId = ref('')
const benchmarkForm = ref({ level: 'l0', dataset_rows: 1000, repeat: 1, seed: 20260826, artifact_digest: 'synthetic-etl-plan-v1', policy_version: 'pdp-v1', environment: 'development' })

const selectedProject = computed(() => projects.value.find((project) => project.id === selectedProjectId.value))
const selectedPreparation = computed(() => preparations.value.find((item) => item.id === selectedPreparationId.value))
const profileOptions = computed<ProfileOption[]>(() => connections.value.flatMap((connection) => {
  const profile = profiles.value[connection.id]
  if (!profile) return []
  return [{
    id: profile.id,
    label: `${connection.name} · ${profile.id.slice(0, 8)}... · ${profile.estimated_row_count ?? '未知'} 行`,
  }]
}))

function synchronizeProfileSelections(): void {
  // Profile 只能从已读取的 UUID 中选择，避免把示例数字误当成真实 ID 提交。
  const firstId = profileOptions.value[0]?.id ?? ''
  const validIds = new Set(profileOptions.value.map((item) => item.id))
  if (!validIds.has(sourceProfileId.value)) sourceProfileId.value = firstId
  if (!validIds.has(targetProfileId.value)) targetProfileId.value = firstId
}

function localizeValidationMessage(item: unknown): string {
  // 将 Pydantic 字段校验的英文摘要转换为带字段名称的中文提示。
  const record = (item ?? {}) as AnyRecord
  const location = Array.isArray(record.loc) ? record.loc : []
  const field = String([...location].reverse().find((part) => typeof part === 'string' && part !== 'body') ?? '请求')
  const fieldLabels: Record<string, string> = {
    username: '用户名', display_name: '显示名称', password: '密码', project_id: '项目',
    project_code: '项目编码', project_role: 'Checker 职责',
    code: '编码', name: '名称', connection_type: '连接类型', host: '主机', port: '端口',
    database_name: '数据库', secret_ref: 'SecretRef', options: '扩展选项',
    source_profile_ids: '源 Profile', target_profile_ids: '目标 Profile',
  }
  const raw = String(record.msg ?? '字段无效')
  const matched = raw.match(/at least (\d+) characters/i)
  if (/at least \d+ characters/i.test(raw) && matched) return `${fieldLabels[field] ?? field}至少需要 ${matched[1]} 个字符`
  const maxMatched = raw.match(/at most (\d+) characters/i)
  if (/at most \d+ characters/i.test(raw) && maxMatched) return `${fieldLabels[field] ?? field}最多只能有 ${maxMatched[1]} 个字符`
  if (/field required/i.test(raw)) return `${fieldLabels[field] ?? field}不能为空`
  if (/valid integer/i.test(raw)) return `${fieldLabels[field] ?? field}必须是整数`
  if (/greater than 0/i.test(raw)) return `${fieldLabels[field] ?? field}必须大于 0`
  if (/less than or equal to/i.test(raw)) return `${fieldLabels[field] ?? field}超出允许范围`
  if (/string should match pattern/i.test(raw)) return `${fieldLabels[field] ?? field}格式不正确`
  if (/valid UUID/i.test(raw)) return `${fieldLabels[field] ?? field}必须选择有效的 Profile UUID`
  if (/value error,?\s*/i.test(raw)) return `${fieldLabels[field] ?? field}：${raw.replace(/^value error,?\s*/i, '')}`
  return `${fieldLabels[field] ?? field}：${raw}`
}

function statusLabel(status: string): string {
  // 将稳定状态枚举转换为控制台使用的中文显示文本。
  const labels: Record<string, string> = {
    active: '启用', disabled: '停用', draft: '草稿', ready: '已就绪',
    approval_pending: '待审批', approved: '已批准', rejected: '已拒绝', pending: '待处理',
    queued: '排队中', running: '运行中', succeeded: '成功', failed: '失败',
    cancel_requested: '取消中', cancelled: '已取消', passed: '通过',
    not_started: '未开始', swap_requested: '发布中', published: '已发布', cleaned: '已清理',
    requested: '已请求', completed: '已完成', not_requested: '未请求', validation_failed: '校验失败',
    needs_clarification: '等待澄清',
  }
  return labels[status] ?? status
}

function roleLabel(role: string): string {
  // 将项目职责槽转换为用户可读的中文名称。
  const labels: Record<string, string> = {
    maker: '制作人', checker_1: 'Checker 1', checker_2: 'Checker 2', operator: '执行人', auditor: '审计人',
  }
  return labels[role] ?? role
}

function formatBenchmarkTime(value: string): string {
  // 将 UTC 时间转换为本地中文时间，便于按运行先后阅读历史报告。
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(new Date(value))
}

function errorLabel(code: string): string {
  // 将后端稳定错误码转换为可直接行动的中文提示。
  const labels: Record<string, string> = {
    AUTH_REQUIRED: '请先登录', AUTH_INVALID: '用户名或密码错误', USERNAME_EXISTS: '用户名已存在',
    REGISTRATION_DISABLED: '当前环境不允许注册账号', VALIDATION_ERROR: '请求参数校验失败',
    REGISTRATION_ASSIGNMENT_INVALID: '项目编码和 Checker 职责必须同时填写',
    REGISTRATION_ROLE_INVALID: '注册页只能分配 Checker 1 或 Checker 2',
    PROJECT_NOT_FOUND: '项目编码不存在，请先创建项目', CHECKER_ROLE_OCCUPIED: '该 Checker 职责槽已经被占用',
    ROLE_FORBIDDEN: '当前职责无权执行此操作', PROJECT_FORBIDDEN: '当前用户不是该项目成员',
    CONNECTION_NOT_FOUND: '连接不存在', CONNECTION_CODE_EXISTS: '项目内连接编码已存在',
    SECRET_UNAVAILABLE: '连接凭据暂时不可用', PROFILE_FAILED: '只读 Profile 探查失败',
    FILE_INVALID: '文件格式或内容无效', FILE_STORAGE_FAILED: '文件存储服务暂时不可用',
    PROFILE_NOT_FOUND: 'Profile 不存在或不属于当前项目', VERSION_NOT_READY: '版本尚未通过生成门禁',
    PREPARATION_EXPIRED: 'Preparation 已过期，请重新准备', SELF_APPROVAL_FORBIDDEN: '制作人不能审批自己的申请',
    APPROVALS_INCOMPLETE: '审批尚未完成', PREPARATION_FINGERPRINT_MISMATCH: 'Preparation 指纹已变化，请重新准备',
    EXECUTION_NOT_FOUND: '执行记录不存在', EXECUTION_NOT_ROLLBACKABLE: '当前执行状态不可回滚', OUTBOX_DISPATCH_FAILED: '受管执行投递失败', BENCHMARK_NOT_FOUND: 'Benchmark 运行记录不存在',
    INTERNAL_ERROR: '服务内部错误，请根据请求编号查看后端日志',
    HTTP_400: '请求参数错误', HTTP_401: '登录状态无效，请重新登录', HTTP_403: '当前用户无权执行此操作',
    HTTP_404: '请求的资源不存在', HTTP_405: '当前操作不被支持', HTTP_422: '请求参数校验失败',
    HTTP_429: '请求过于频繁，请稍后再试', HTTP_500: '服务内部错误，请根据请求编号查看后端日志',
    HTTP_503: '依赖服务暂时不可用，请稍后再试',
  }
  return labels[code] ?? code
}

async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  // 统一添加认证头并把后端稳定错误转换为页面可读消息。
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  if (token.value) headers.set('Authorization', `Bearer ${token.value}`)
  let response: Response
  try {
    response = await fetch(path, { ...init, headers })
  } catch {
    throw new Error('无法连接控制面 API，请确认 8000 端口的 FastAPI 已启动')
  }
  const body = await response.json().catch(() => ({})) as AnyRecord
  if (!response.ok) {
    // Vite 代理连不上 FastAPI 时通常返回无业务错误体的 500，给出可执行提示。
    if (response.status === 500 && !body.code && !body.message && !body.detail) {
      throw new Error('控制面 API 未启动或代理不可达，请先运行 8000 端口的 FastAPI')
    }
    const detail = body.detail as AnyRecord | undefined
    const validationErrors = (body.details as AnyRecord | undefined)?.errors
    const validationMessage = Array.isArray(validationErrors)
      ? [...new Set(validationErrors.map((item) => localizeValidationMessage(item)))].join('；')
      : undefined
    const rawCode = body.code ? String(body.code) : ''
    const translatedCode = rawCode ? errorLabel(rawCode) : ''
    const codeMessage = translatedCode && translatedCode !== rawCode ? translatedCode : undefined
    const rawMessage = String(detail?.message ?? validationMessage ?? codeMessage ?? body.message ?? `请求失败（${response.status}）`)
    const messageLabels: Record<string, string> = {
      'Request validation failed': '请求参数校验失败',
      'An internal error occurred': '服务内部错误，请根据请求编号查看后端日志',
      'Not Found': '请求地址不存在',
    }
    throw new Error(messageLabels[rawMessage] ?? rawMessage)
  }
  return body as T
}

function notify(text: string): void {
  // 保留最后一条稳定提示，避免把后端堆栈或敏感信息展示在控制台。
  message.value = text
  window.setTimeout(() => { if (message.value === text) message.value = '' }, 5000)
}

async function authenticate(): Promise<void> {
  busy.value = true
  try {
    if (registerMode.value) {
      const registerPayload: AnyRecord = {
        username: username.value,
        display_name: displayName.value || username.value,
        password: password.value,
      }
      // 选择 Checker 时才把项目绑定信息发送给开发环境注册接口。
      if (registerProjectRole.value) {
        registerPayload.project_code = registerProjectCode.value
        registerPayload.project_role = registerProjectRole.value
      }
      await api('/api/v1/auth/register', { method: 'POST', body: JSON.stringify(registerPayload) })
    }
    const result = await api<{ access_token: string }>('/api/v1/auth/login', { method: 'POST', body: JSON.stringify({ username: username.value, password: password.value }) })
    token.value = result.access_token
    localStorage.setItem('etl_agent_token', token.value)
    await loadProjects()
    notify('已登录')
  } catch (error) {
    notify(error instanceof Error ? error.message : '登录失败')
  } finally {
    busy.value = false
  }
}

async function loadProjects(): Promise<void> {
  // 登录后只加载当前用户可见的项目，并自动选择第一个项目。
  projects.value = await api<Project[]>('/api/v1/projects')
  if (!selectedProjectId.value && projects.value.length) selectedProjectId.value = projects.value[0].id
  if (selectedProjectId.value) await loadProjectData()
}

async function createProject(): Promise<void> {
  // 为新用户创建项目并自动选中，创建者默认获得 Maker 和 Operator 职责。
  try {
    const project = await api<Project>('/api/v1/projects', { method: 'POST', body: JSON.stringify(projectForm.value) })
    projects.value = [project]
    selectedProjectId.value = project.id
    await loadProjectData()
    notify('项目创建成功')
  } catch (error) {
    notify(error instanceof Error ? error.message : '项目创建失败')
  }
}

async function loadProjectData(): Promise<void> {
  if (!selectedProjectId.value) return
  busy.value = true
  try {
    const projectId = selectedProjectId.value
    const [connectionRows, pipelineRows, preparationRows, executionRows, benchmarkRows] = await Promise.all([
      api<Connection[]>(`/api/v1/projects/${projectId}/connections`),
      api<Pipeline[]>(`/api/v1/projects/${projectId}/pipelines`),
      api<Preparation[]>(`/api/v1/projects/${projectId}/preparations`),
      api<Execution[]>(`/api/v1/projects/${projectId}/execution-runs`),
      api<BenchmarkReport[]>(`/api/v1/projects/${projectId}/benchmarks`),
    ])
    connections.value = connectionRows
    pipelines.value = pipelineRows
    preparations.value = preparationRows
    executions.value = executionRows
    benchmarkHistory.value = benchmarkRows
    benchmarkReport.value = benchmarkRows[0] ?? null
  } catch (error) {
    notify(error instanceof Error ? error.message : '项目数据加载失败')
  } finally {
    busy.value = false
  }
}

async function loadProfiles(): Promise<void> {
  // Profile 查询是显式动作，避免页面刷新时触发外部探查。
  for (const connection of connections.value) {
    try { profiles.value[connection.id] = await api<Profile>(`/api/v1/connections/${connection.id}/profiles/latest`) } catch { /* 尚无 Profile 时保持空白 */ }
  }
  synchronizeProfileSelections()
}

async function createConnection(): Promise<void> {
  // 只提交非敏感连接字段，密码始终由 SecretRef 管理。
  try {
    const wasEditing = Boolean(editingConnectionId.value)
    const path = wasEditing ? `/api/v1/connections/${editingConnectionId.value}` : '/api/v1/connections'
    const method = wasEditing ? 'PUT' : 'POST'
    const payload = wasEditing ? connectionForm.value : { project_id: selectedProjectId.value, ...connectionForm.value, options: {} }
    const saved = await api<Connection>(path, { method, body: JSON.stringify(payload) })
    if (wasEditing) {
      const index = connections.value.findIndex((item) => item.id === saved.id)
      if (index >= 0) connections.value[index] = saved
    } else {
      connections.value.push(saved)
    }
    editingConnectionId.value = null
    notify(wasEditing ? '连接更新成功' : '连接登记成功')
  } catch (error) { notify(error instanceof Error ? error.message : '连接保存失败') }
}

function startEditConnection(connection: Connection): void {
  // 将已有连接的非敏感字段载入表单，允许修正 VM 地址或 SecretRef。
  editingConnectionId.value = connection.id
  connectionForm.value = {
    code: connection.code,
    name: connection.name,
    connection_type: connection.connection_type,
    host: connection.host,
    port: connection.port,
    database_name: connection.database_name ?? '',
    username: connection.username ?? '',
    secret_ref: connection.secret_ref ?? '',
  }
}

function cancelEditConnection(): void {
  // 退出编辑模式并恢复下一次登记使用的 VM 默认地址。
  editingConnectionId.value = null
  connectionForm.value = { code: 'synthetic_mysql', name: '合成 MySQL', connection_type: 'mysql', host: syntheticMysqlHost, port: 3306, database_name: 'etl_demo', username: 'etl_demo', secret_ref: 'secret/data/etl-agent/mysql' }
}

function validateSyntheticMysqlHost(connection: Connection): boolean {
  // Windows 控制面访问 VM MySQL 时，阻止旧连接把请求发往本机回环地址。
  const isLoopback = ['127.0.0.1', 'localhost', '::1'].includes(connection.host.toLowerCase())
  if (connection.connection_type === 'mysql' && isLoopback && syntheticMysqlHost !== connection.host) {
    notify('该 MySQL 连接仍指向本机，请编辑为 192.168.181.128 后再操作')
    return false
  }
  return true
}

async function testConnection(connection: Connection): Promise<void> {
  // 触发后端受限探针，只展示稳定状态和耗时摘要。
  if (!validateSyntheticMysqlHost(connection)) return
  try { const result = await api<{ status: string; detail: string }>(`/api/v1/connections/${connection.id}/tests`, { method: 'POST' }); notify(`${connection.name}：${statusLabel(result.status)}，${result.detail}`) } catch (error) { notify(error instanceof Error ? error.message : '连接测试失败') }
}

async function createProfile(connection: Connection): Promise<void> {
  // 请求一次只读 Profile，并把脱敏快照绑定到连接。
  if (!validateSyntheticMysqlHost(connection)) return
  try {
    // 允许按逗号指定表名；留空时探查连接中的全部业务表。
    const tableNames = (profileTableNames.value[connection.id] ?? '')
      .split(',')
      .map((name) => name.trim())
      .filter(Boolean)
    const profile = await api<Profile>(`/api/v1/connections/${connection.id}/profiles`, { method: 'POST', body: JSON.stringify({ table_names: tableNames, sample_rows: 5 }) })
    profiles.value[connection.id] = profile
    synchronizeProfileSelections()
    notify(`${connection.name} Profile 已生成`)
  } catch (error) { notify(error instanceof Error ? error.message : 'Profile 生成失败') }
}

async function createPipelineAndVersion(): Promise<void> {
  // 先创建 Pipeline，再创建不可变前的草稿版本供生成工作流使用。
  try {
    const pipeline = await api<Pipeline>('/api/v1/pipelines', { method: 'POST', body: JSON.stringify({ project_id: selectedProjectId.value, code: pipelineForm.value.code, name: pipelineForm.value.name }) })
    pipelines.value.push(pipeline)
    const version = await api<Version>(`/api/v1/pipelines/${pipeline.id}/versions`, { method: 'POST', body: JSON.stringify({}) })
    versions.value = [version]
    selectedVersionId.value = version.id
    notify('Pipeline 草稿已创建')
  } catch (error) { notify(error instanceof Error ? error.message : 'Pipeline 创建失败') }
}

async function loadVersions(pipelineId: string): Promise<void> {
  // 读取指定 Pipeline 的版本列表并默认选中最新版本。
  try { versions.value = await api<Version[]>(`/api/v1/pipelines/${pipelineId}/versions`); if (versions.value[0]) selectedVersionId.value = versions.value[0].id } catch (error) { notify(error instanceof Error ? error.message : '版本加载失败') }
}

function selectPipeline(event: Event): void {
  // 从选择器读取 Pipeline ID，再加载它的版本列表。
  const value = (event.target as HTMLSelectElement).value
  if (value) void loadVersions(value)
}

async function generateVersion(): Promise<void> {
  // 把业务需求和 Profile 引用提交给 LangGraph 生成边界。
  try {
    const run = await api<AnyRecord>(`/api/v1/versions/${selectedVersionId.value}/generation`, { method: 'POST', body: JSON.stringify({ business_request: pipelineForm.value.business_request, source_profile_ids: [sourceProfileId.value], target_profile_ids: [targetProfileId.value] }) })
    notify(`生成状态：${statusLabel(String(run.status))}`)
    await loadProjectData()
  } catch (error) { notify(error instanceof Error ? error.message : '生成失败') }
}

async function prepareVersion(): Promise<void> {
  // 只冻结已通过门禁的版本事实，不在浏览器执行外部副作用。
  try {
    const preparation = await api<Preparation>(`/api/v1/versions/${selectedVersionId.value}/prepare`, { method: 'POST', body: JSON.stringify({}) })
    preparations.value.unshift(preparation)
    selectedPreparationId.value = preparation.id
    notify(`Preparation 已创建，风险级别 ${preparation.risk_level}`)
  } catch (error) { notify(error instanceof Error ? error.message : 'Prepare 失败') }
}

async function decideApproval(approval: Approval, decision: 'approve' | 'reject'): Promise<void> {
  // 通过审批 API 提交 Checker 决定，服务端负责四眼原则校验。
  try {
    await api(`/api/v1/approval-requests/${approval.id}/decisions`, { method: 'POST', body: JSON.stringify({ decision, comment: decision === 'approve' ? '控制台审批' : '控制台拒绝' }) })
    await loadProjectData()
    notify('审批决定已提交')
  } catch (error) { notify(error instanceof Error ? error.message : '审批失败') }
}

async function commitPreparation(): Promise<void> {
  // 由 Operator 触发 Commit，执行事实和 Outbox 由服务端事务创建。
  try {
    const result = await api<{ execution_run_id: string }>(`/api/v1/preparations/${selectedPreparationId.value}/commit`, { method: 'POST' })
    notify(`Commit 已受理：${result.execution_run_id}`)
    await loadProjectData()
    activeTab.value = 'runs'
  } catch (error) { notify(error instanceof Error ? error.message : 'Commit 失败') }
}

async function cancelExecution(execution: Execution): Promise<void> {
  // 登记取消动作，实际引擎调用仍由 Worker 和 Tool Broker 执行。
  try { await api(`/api/v1/execution-runs/${execution.id}/cancel`, { method: 'POST', body: JSON.stringify({ reason: '控制台请求取消' }) }); await loadProjectData(); notify('取消请求已登记') } catch (error) { notify(error instanceof Error ? error.message : '取消失败') }
}

async function rollbackExecution(execution: Execution): Promise<void> {
  // 对终态执行登记回滚请求，避免前端直接操作目标表。
  try { await api(`/api/v1/execution-runs/${execution.id}/rollback`, { method: 'POST', body: JSON.stringify({ reason: '控制台请求回滚' }) }); await loadProjectData(); notify('回滚请求已登记') } catch (error) { notify(error instanceof Error ? error.message : '回滚失败') }
}

async function runBenchmark(): Promise<void> {
  // 运行不访问业务库的合成 L0/L1 Benchmark 并展示报告摘要。
  try {
    const report = await api<BenchmarkReport>('/api/v1/benchmarks/run', { method: 'POST', body: JSON.stringify({ project_id: selectedProjectId.value, ...benchmarkForm.value }) })
    benchmarkReport.value = report
    benchmarkHistory.value = [report, ...benchmarkHistory.value.filter((item) => item.benchmark_id !== report.benchmark_id)]
    notify('Benchmark 已完成')
  } catch (error) { notify(error instanceof Error ? error.message : 'Benchmark 失败') }
}

function logout(): void {
  // 清除本地令牌，不触碰服务端业务事实。
  token.value = ''
  localStorage.removeItem('etl_agent_token')
  projects.value = []
}

onMounted(async () => { if (token.value) await loadProjects() })
</script>

<template>
  <main class="app-shell">
    <section v-if="!token" class="auth-screen">
      <div class="auth-panel">
        <p class="eyebrow">ETL-AGENT / CONTROL PLANE</p>
        <h1>受管数据集成控制台</h1>
        <p class="muted">连接、生成、审批与运行事实在同一工作台内追踪。</p>
        <label>用户名<input v-model="username" autocomplete="username" /></label>
        <label v-if="registerMode">显示名称<input v-model="displayName" /></label>
        <label v-if="registerMode">注册后的项目职责<select v-model="registerProjectRole"><option value="">普通开发账号（稍后分配职责）</option><option value="checker_1">Checker 1</option><option value="checker_2">Checker 2</option></select></label>
        <label v-if="registerMode && registerProjectRole">项目编码<input v-model="registerProjectCode" placeholder="例如 etl_learning" /><small>填写已由 Maker 创建的项目编码</small></label>
        <label>密码<input v-model="password" type="password" minlength="8" autocomplete="current-password" /><small>密码至少 8 位</small></label>
        <button class="primary wide" :disabled="busy" @click="authenticate">{{ registerMode ? '注册并登录' : '登录' }}</button>
        <button class="link-button" @click="registerMode = !registerMode">{{ registerMode ? '已有账号，返回登录' : '开发环境注册账号' }}</button>
        <p v-if="message" class="notice">{{ message }}</p>
      </div>
    </section>

    <template v-else>
      <header class="topbar">
        <div><span class="brand-mark">EA</span><strong>ETL-Agent</strong><span class="crumb">控制面</span></div>
        <div class="top-actions"><select v-model="selectedProjectId" @change="loadProjectData"><option v-for="project in projects" :key="project.id" :value="project.id">{{ project.name }}（{{ project.code }}）</option></select><button class="quiet" @click="loadProjectData">刷新</button><button class="quiet" @click="logout">退出</button></div>
      </header>
      <div class="workspace">
        <aside class="sidebar">
          <p class="eyebrow">PROJECT / {{ selectedProject?.code ?? '未选择' }}</p>
          <button v-for="item in [{id:'overview',label:'总览',icon:'⌂'},{id:'connections',label:'连接与 Profile',icon:'◈'},{id:'studio',label:'Pipeline Studio',icon:'◇'},{id:'approvals',label:'审批工作台',icon:'✓'},{id:'runs',label:'运行中心',icon:'▶'},{id:'benchmark',label:'Benchmark',icon:'▦'}]" :key="item.id" :class="['nav-item', { active: activeTab === item.id }]" @click="activeTab = item.id"><span>{{ item.icon }}</span>{{ item.label }}</button>
          <div class="sidebar-foot"><span class="status-dot"></span> API 已连接<div class="muted small">{{ selectedProject?.name }}</div></div>
        </aside>

        <section class="content">
          <div v-if="message" class="notice global-notice">{{ message }}</div>
          <div v-if="activeTab === 'overview' && !projects.length" class="view">
            <div class="view-heading"><div><p class="eyebrow">FIRST RUN / PROJECT</p><h2>创建学习项目</h2></div></div>
            <section class="panel form-panel first-project-panel"><h3>先创建一个项目，再开始测试</h3><div class="form-grid"><label>项目编码<input v-model="projectForm.code" /></label><label>项目名称<input v-model="projectForm.name" /></label></div><button class="primary" @click="createProject">创建项目</button></section>
          </div>

          <div v-if="activeTab === 'overview' && projects.length" class="view">
            <div class="view-heading"><div><p class="eyebrow">WORKSPACE / OVERVIEW</p><h2>项目总览</h2><p class="project-context">当前项目：{{ selectedProject?.name }} <span class="mono">（{{ selectedProject?.code }}）</span></p></div><button class="primary" @click="activeTab = 'studio'">新建 Pipeline</button></div>
            <div class="metric-grid"><div class="metric"><span>连接</span><strong>{{ connections.length }}</strong><small>项目级登记</small></div><div class="metric"><span>Pipeline</span><strong>{{ pipelines.length }}</strong><small>含草稿与冻结版本</small></div><div class="metric"><span>待审批</span><strong>{{ preparations.filter(p => p.status === 'approval_pending').length }}</strong><small>等待 Checker</small></div><div class="metric"><span>执行</span><strong>{{ executions.length }}</strong><small>受管运行事实</small></div></div>
            <div class="section-grid"><section class="panel"><div class="panel-title"><h3>最近执行</h3><button class="link-button" @click="activeTab = 'runs'">查看全部</button></div><div v-if="executions.length" class="table-wrap"><table><thead><tr><th>状态</th><th>执行 ID</th><th>质量</th></tr></thead><tbody><tr v-for="item in executions.slice(0,5)" :key="item.id"><td><span :class="['badge', item.status]">{{ statusLabel(item.status) }}</span></td><td class="mono">{{ item.id.slice(0, 12) }}…</td><td>{{ statusLabel(item.quality_status) }}</td></tr></tbody></table></div><p v-else class="empty">暂无执行事实</p></section><section class="panel"><div class="panel-title"><h3>Pipeline</h3><button class="link-button" @click="activeTab = 'studio'">打开 Studio</button></div><div v-if="pipelines.length" class="list"> <button v-for="pipeline in pipelines" :key="pipeline.id" class="list-row" @click="activeTab = 'studio'; loadVersions(pipeline.id)"><span><strong>{{ pipeline.name }}</strong><small>{{ pipeline.code }}</small></span><span class="badge">{{ statusLabel(pipeline.status) }}</span></button></div><p v-else class="empty">尚未创建 Pipeline</p></section></div>
          </div>

          <div v-if="activeTab === 'connections'" class="view"><div class="view-heading"><div><p class="eyebrow">ASSETS / PROFILE</p><h2>连接与 Profile</h2></div></div><section class="panel form-panel"><h3>{{ editingConnectionId ? '编辑连接' : '登记合成连接' }}</h3><div class="form-grid"><label>编码<input v-model="connectionForm.code" /></label><label>名称<input v-model="connectionForm.name" /></label><label>类型<select v-model="connectionForm.connection_type"><option>mysql</option><option>doris</option><option>postgresql</option></select></label><label>主机<input v-model="connectionForm.host" /></label><label>端口<input v-model.number="connectionForm.port" type="number" /></label><label>数据库<input v-model="connectionForm.database_name" /></label><label>用户名<input v-model="connectionForm.username" /></label><label>SecretRef<input v-model="connectionForm.secret_ref" /></label></div><div class="actions"><button class="primary" @click="createConnection">{{ editingConnectionId ? '保存连接' : '登记连接' }}</button><button v-if="editingConnectionId" class="quiet" @click="cancelEditConnection">取消</button></div></section><section class="panel"><div class="panel-title"><h3>项目连接</h3><button class="quiet" @click="loadProfiles">读取最近 Profile</button></div><div v-if="connections.length" class="table-wrap"><table><thead><tr><th>连接</th><th>类型</th><th>地址</th><th>Profile</th><th>操作</th></tr></thead><tbody><tr v-for="connection in connections" :key="connection.id"><td><strong>{{ connection.name }}</strong><small>{{ connection.code }}</small></td><td>{{ connection.connection_type }}</td><td class="mono">{{ connection.host }}:{{ connection.port }}</td><td><strong>{{ profiles[connection.id]?.estimated_row_count ?? '暂无' }}</strong><small class="mono">{{ profiles[connection.id]?.id ?? '' }}</small></td><td class="actions"><input v-model="profileTableNames[connection.id]" class="profile-table-input" placeholder="表名，可逗号分隔" /><button class="quiet" @click="testConnection(connection)">测试</button><button class="quiet" @click="createProfile(connection)">探查</button><button class="quiet" @click="startEditConnection(connection)">编辑</button></td></tr></tbody></table></div><p v-else class="empty">暂无连接</p></section></div>

          <div v-if="activeTab === 'studio'" class="view"><div class="view-heading"><div><p class="eyebrow">DESIGN / IMMUTABLE VERSION</p><h2>Pipeline Studio</h2></div></div><section class="panel form-panel"><h3>创建 Pipeline 草稿</h3><div class="form-grid"><label>编码<input v-model="pipelineForm.code" /></label><label>名称<input v-model="pipelineForm.name" /></label><label class="wide-field">业务需求<textarea v-model="pipelineForm.business_request" rows="3" /></label></div><button class="primary" @click="createPipelineAndVersion">创建草稿版本</button></section><section class="panel"><div class="panel-title"><h3>生成与 Prepare</h3><span class="muted">先在“连接与 Profile”读取或探查 Profile，再提交生成</span></div><div class="form-grid"><label>Pipeline<select @change="selectPipeline"><option value="">选择 Pipeline</option><option v-for="pipeline in pipelines" :key="pipeline.id" :value="pipeline.id">{{ pipeline.name }}</option></select></label><label>版本<select v-model="selectedVersionId"><option value="">选择版本</option><option v-for="version in versions" :key="version.id" :value="version.id">v{{ version.version_number }} / {{ statusLabel(version.status) }}</option></select></label><label>源 Profile<select v-model="sourceProfileId" :disabled="!profileOptions.length"><option value="">{{ profileOptions.length ? '选择源 Profile' : '请先读取 Profile' }}</option><option v-for="profile in profileOptions" :key="`source-${profile.id}`" :value="profile.id">{{ profile.label }}</option></select></label><label>目标 Profile<select v-model="targetProfileId" :disabled="!profileOptions.length"><option value="">{{ profileOptions.length ? '选择目标 Profile' : '请先读取 Profile' }}</option><option v-for="profile in profileOptions" :key="`target-${profile.id}`" :value="profile.id">{{ profile.label }}</option></select></label></div><div class="actions"><button class="primary" :disabled="!selectedVersionId || !sourceProfileId || !targetProfileId" @click="generateVersion">运行生成</button><button class="quiet" :disabled="!selectedVersionId || !sourceProfileId || !targetProfileId" @click="prepareVersion">Prepare</button></div></section></div>

          <div v-if="activeTab === 'approvals'" class="view"><div class="view-heading"><div><p class="eyebrow">HARNESS / FOUR EYES</p><h2>审批工作台</h2></div></div><section v-for="preparation in preparations" :key="preparation.id" class="panel"><div class="panel-title"><div><h3>{{ preparation.id.slice(0, 12) }}…</h3><small>风险 {{ preparation.risk_level }} · {{ statusLabel(preparation.status) }}</small></div><button v-if="preparation.status === 'approved'" class="primary" @click="selectedPreparationId = preparation.id; commitPreparation()">Commit</button></div><div class="approval-list"><div v-for="approval in preparation.approval_requests" :key="approval.id" class="approval-row"><span class="badge">{{ roleLabel(approval.required_role) }}</span><span>{{ statusLabel(approval.status) }}</span><span class="spacer"></span><button v-if="approval.status === 'pending'" class="quiet" @click="decideApproval(approval, 'reject')">拒绝</button><button v-if="approval.status === 'pending'" class="primary small-button" @click="decideApproval(approval, 'approve')">批准</button></div></div></section><p v-if="!preparations.length" class="empty">暂无 Preparation</p></div>

          <div v-if="activeTab === 'runs'" class="view"><div class="view-heading"><div><p class="eyebrow">DATA PLANE / SUPERVISION</p><h2>运行中心</h2></div><button class="quiet" @click="loadProjectData">刷新状态</button></div><section v-for="execution in executions" :key="execution.id" class="panel execution-panel"><div class="panel-title"><div><h3 class="mono">{{ execution.id }}</h3><span :class="['badge', execution.status]">{{ statusLabel(execution.status) }}</span></div><div class="actions"><button v-if="['queued','running','cancel_requested'].includes(execution.status)" class="quiet" @click="cancelExecution(execution)">取消</button><button v-if="['succeeded','failed','cancelled'].includes(execution.status)" class="quiet" @click="rollbackExecution(execution)">回滚</button></div></div><div class="metric-strip"><span>质量 <strong>{{ statusLabel(execution.quality_status) }}</strong></span><span>发布 <strong>{{ statusLabel(execution.publish_status) }}</strong></span><span>回滚 <strong>{{ statusLabel(execution.rollback_status) }}</strong></span><span>输入 <strong>{{ execution.metrics.input_records ?? '-' }}</strong></span><span>输出 <strong>{{ execution.metrics.output_records ?? '-' }}</strong></span></div><p v-if="execution.error_code" class="error-text">{{ errorLabel(execution.error_code) }}<span v-if="execution.error_detail">：{{ execution.error_detail }}</span></p></section><p v-if="!executions.length" class="empty">暂无执行事实</p></div>

          <div v-if="activeTab === 'benchmark'" class="view"><div class="view-heading"><div><p class="eyebrow">EVIDENCE / REPEATABLE</p><h2>Benchmark</h2></div></div><section class="panel form-panel"><h3>运行合成基准</h3><div class="form-grid"><label>级别<select v-model="benchmarkForm.level"><option value="l0">L0 基线</option><option value="l1">L1 故障注入</option></select></label><label>数据行数<input v-model.number="benchmarkForm.dataset_rows" type="number" min="1" /></label><label>重复次数<input v-model.number="benchmarkForm.repeat" type="number" min="1" /></label><label>随机种子<input v-model.number="benchmarkForm.seed" type="number" /></label><label>制品摘要<input v-model="benchmarkForm.artifact_digest" /></label><label>策略版本<input v-model="benchmarkForm.policy_version" /></label></div><button class="primary" @click="runBenchmark">运行 Benchmark</button></section><section v-if="benchmarkReport" class="panel"><div class="panel-title"><h3>报告 {{ benchmarkReport.benchmark_id.slice(0, 12) }}…</h3><span class="badge">{{ benchmarkReport.level }}</span></div><div class="metric-grid compact"><div class="metric"><span>输入</span><strong>{{ benchmarkReport.metrics.input_records }}</strong></div><div class="metric"><span>输出</span><strong>{{ benchmarkReport.metrics.output_records }}</strong></div><div class="metric"><span>拒绝率</span><strong>{{ benchmarkReport.metrics.rejection_rate }}</strong></div><div class="metric"><span>P0 拦截率</span><strong>{{ benchmarkReport.metrics.p0_interception_rate }}</strong></div></div><pre class="report-json">{{ JSON.stringify(benchmarkReport, null, 2) }}</pre></section><section class="panel"><div class="panel-title"><h3>历史报告</h3><button class="quiet" @click="loadProjectData">刷新</button></div><div v-if="benchmarkHistory.length" class="table-wrap"><table><thead><tr><th>完成时间</th><th>级别</th><th>数据规模</th><th>拒绝率</th><th>质量结论</th></tr></thead><tbody><tr v-for="item in benchmarkHistory" :key="item.benchmark_id" @click="benchmarkReport = item"><td>{{ formatBenchmarkTime(item.completed_at) }}</td><td><span class="badge">{{ item.level }}</span></td><td>{{ item.dataset_rows }} × {{ item.repeat }}</td><td>{{ item.metrics.rejection_rate }}</td><td>{{ item.metrics.quality_decision === 'passed' ? '通过' : '拒绝' }}</td></tr></tbody></table></div><p v-else class="empty">暂无历史 Benchmark</p></section></div>
        </section>
      </div>
    </template>
  </main>
</template>
