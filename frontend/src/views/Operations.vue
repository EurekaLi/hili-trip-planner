<template>
  <main class="operations-page">
    <header class="page-heading">
      <div>
        <p class="eyebrow">系统运营</p>
        <h1>质量、成本与故障追踪</h1>
      </div>
      <div class="heading-actions">
        <a-segmented
          v-model:value="windowHours"
          :options="windowOptions"
          @change="loadOverview"
        />
        <a-button :loading="loading" @click="loadAll">
          <ReloadOutlined />
          刷新
        </a-button>
      </div>
    </header>

    <a-alert
      v-if="errorMessage"
      type="error"
      show-icon
      :message="errorMessage"
      closable
      @close="errorMessage = ''"
    />

    <section v-if="overview" class="metric-grid" aria-label="核心指标">
      <a-card size="small"><a-statistic title="生成任务" :value="overview.jobs_total" /></a-card>
      <a-card size="small"><a-statistic title="成功率" :value="overview.success_rate * 100" suffix="%" :precision="1" /></a-card>
      <a-card size="small"><a-statistic title="P95 耗时" :value="overview.p95_job_seconds" suffix="秒" :precision="1" /></a-card>
      <a-card size="small"><a-statistic title="已知费用" :value="overview.known_cost_cny" prefix="¥" :precision="4" /></a-card>
      <a-card size="small"><a-statistic title="用户反馈" :value="overview.feedback_count" /></a-card>
      <a-card size="small"><a-statistic title="平均评分" :value="overview.average_rating || 0" suffix="/ 5" :precision="1" /></a-card>
      <a-card size="small"><a-statistic title="图片缓存命中" :value="(overview.image_cache_hit_rate || 0) * 100" suffix="%" :precision="1" /></a-card>
    </section>

    <section class="status-band">
      <div>
        <h2>任务状态</h2>
        <a-space wrap>
          <a-tag v-for="item in overview?.status_counts || []" :key="item.name">
            {{ item.name }} · {{ item.count }}
          </a-tag>
          <span v-if="!overview?.status_counts.length" class="muted">暂无任务</span>
        </a-space>
      </div>
      <div>
        <h2>熔断器</h2>
        <a-space wrap>
          <a-tag
            v-for="circuit in circuits"
            :key="circuit.provider"
            :color="circuit.is_open ? 'error' : 'success'"
          >
            {{ circuit.provider }} · {{ circuit.is_open ? `熔断 ${circuit.retry_after_seconds}s` : '正常' }}
          </a-tag>
        </a-space>
      </div>
    </section>

    <section class="data-section">
      <div class="section-heading">
        <div>
          <h2>第三方调用</h2>
          <p>按提供方与操作查看调用量、成功率、P95 和已知费用。</p>
        </div>
      </div>
      <a-table
        :columns="providerColumns"
        :data-source="overview?.provider_metrics || []"
        :pagination="false"
        row-key="operation"
        size="small"
        :scroll="{ x: 760 }"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'success_rate'">
            {{ (record.success_rate * 100).toFixed(1) }}%
          </template>
          <template v-else-if="column.key === 'known_cost_cny'">
            ¥{{ record.known_cost_cny.toFixed(4) }}
          </template>
        </template>
      </a-table>
    </section>

    <section class="data-section">
      <div class="section-heading trace-heading">
        <div>
          <h2>失败与单次追踪</h2>
          <p>输入 job_id 或响应头 X-Request-ID，可还原一次请求的完整路径。</p>
        </div>
        <a-input-search
          v-model:value="traceLocator"
          placeholder="job_id / request_id"
          enter-button="查询"
          class="trace-search"
          @search="loadTrace"
        />
      </div>
      <a-table
        :columns="failureColumns"
        :data-source="overview?.recent_failures || []"
        :pagination="false"
        row-key="job_id"
        size="small"
        :scroll="{ x: 820 }"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'job_id'">
            <a-button type="link" class="trace-link" @click="openFailure(record.job_id)">
              {{ record.job_id.slice(0, 12) }}
            </a-button>
          </template>
          <template v-else-if="column.key === 'created_at'">
            {{ formatTime(record.created_at) }}
          </template>
        </template>
      </a-table>
    </section>

    <a-drawer
      v-model:open="traceOpen"
      title="任务执行轨迹"
      width="min(760px, 100vw)"
    >
      <a-spin :spinning="traceLoading">
        <template v-if="jobTrace">
          <a-descriptions :column="1" size="small" bordered>
            <a-descriptions-item label="Job ID">{{ jobTrace.job_id }}</a-descriptions-item>
            <a-descriptions-item label="Request ID">{{ jobTrace.request_id || '未记录' }}</a-descriptions-item>
            <a-descriptions-item label="状态">{{ jobTrace.status }}</a-descriptions-item>
            <a-descriptions-item label="城市">{{ jobTrace.city }}</a-descriptions-item>
            <a-descriptions-item label="错误">{{ jobTrace.error_code || '无' }} {{ jobTrace.error_message || '' }}</a-descriptions-item>
            <a-descriptions-item label="预算">{{ formatJson(jobTrace.budget) }}</a-descriptions-item>
          </a-descriptions>
          <h3>外部调用明细</h3>
          <a-table
            :columns="traceColumns"
            :data-source="jobTrace.external_calls"
            :pagination="false"
            row-key="requested_at"
            size="small"
            :scroll="{ x: 680 }"
          />
          <h3>Agent 决策轨迹</h3>
          <pre class="trace-json">{{ formatJson(jobTrace.agent_trace) }}</pre>
        </template>
      </a-spin>
    </a-drawer>
  </main>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { ReloadOutlined } from '@ant-design/icons-vue'
import {
  getApiErrorMessage,
  getCircuitStatuses,
  getOperationsJobTrace,
  getOperationsOverview
} from '@/services/api'
import { initializeSession, sessionState } from '@/services/session'
import type {
  CircuitStatus,
  JobTrace,
  OperationsOverview
} from '@/types'

const router = useRouter()
const loading = ref(false)
const traceLoading = ref(false)
const errorMessage = ref('')
const windowHours = ref(24)
const overview = ref<OperationsOverview | null>(null)
const circuits = ref<CircuitStatus[]>([])
const traceLocator = ref('')
const traceOpen = ref(false)
const jobTrace = ref<JobTrace | null>(null)
const windowOptions = [
  { label: '24 小时', value: 24 },
  { label: '7 天', value: 168 },
  { label: '30 天', value: 720 }
]
const providerColumns = [
  { title: '提供方', dataIndex: 'provider', key: 'provider' },
  { title: '操作', dataIndex: 'operation', key: 'operation' },
  { title: '调用', dataIndex: 'calls', key: 'calls' },
  { title: '成功率', dataIndex: 'success_rate', key: 'success_rate' },
  { title: '平均秒数', dataIndex: 'average_latency_seconds', key: 'average_latency_seconds' },
  { title: 'P95 秒数', dataIndex: 'p95_latency_seconds', key: 'p95_latency_seconds' },
  { title: '费用', dataIndex: 'known_cost_cny', key: 'known_cost_cny' }
]
const failureColumns = [
  { title: '任务', dataIndex: 'job_id', key: 'job_id' },
  { title: '城市', dataIndex: 'city', key: 'city' },
  { title: '错误分类', dataIndex: 'error_code', key: 'error_code' },
  { title: '错误摘要', dataIndex: 'error_message', key: 'error_message', ellipsis: true },
  { title: '时间', dataIndex: 'created_at', key: 'created_at' }
]
const traceColumns = [
  { title: '提供方', dataIndex: 'provider', key: 'provider' },
  { title: '操作', dataIndex: 'operation', key: 'operation' },
  { title: '成功', dataIndex: 'succeeded', key: 'succeeded' },
  { title: '耗时', dataIndex: 'elapsed_seconds', key: 'elapsed_seconds' },
  { title: '错误', dataIndex: 'error_type', key: 'error_type' }
]

/** 读取当前时间窗口的运营聚合。 */
const loadOverview = async (): Promise<void> => {
  overview.value = await getOperationsOverview(windowHours.value)
}

/** 并行刷新运营总览和共享熔断状态。 */
const loadAll = async (): Promise<void> => {
  loading.value = true
  errorMessage.value = ''
  try {
    const [nextOverview, nextCircuits] = await Promise.all([
      getOperationsOverview(windowHours.value),
      getCircuitStatuses()
    ])
    overview.value = nextOverview
    circuits.value = nextCircuits
  } catch (error: unknown) {
    errorMessage.value = getApiErrorMessage(error, '运营数据加载失败')
  } finally {
    loading.value = false
  }
}

/** 使用输入的任务或请求 ID 打开追踪抽屉。 */
const loadTrace = async (): Promise<void> => {
  const locator = traceLocator.value.trim()
  if (!locator) {
    message.warning('请输入 job_id 或 request_id')
    return
  }
  traceOpen.value = true
  traceLoading.value = true
  jobTrace.value = null
  try {
    jobTrace.value = await getOperationsJobTrace(locator)
  } catch (error: unknown) {
    message.error(getApiErrorMessage(error, '任务追踪失败'))
  } finally {
    traceLoading.value = false
  }
}

/** 从失败列表选择任务并执行追踪查询。 */
const openFailure = (jobId: string): void => {
  traceLocator.value = jobId
  void loadTrace()
}

/** 将 ISO 时间转换为本地短时间。 */
const formatTime = (value: string): string => new Date(value).toLocaleString()

/** 将结构化审计内容格式化为易读 JSON。 */
const formatJson = (value: unknown): string => JSON.stringify(value, null, 2)

onMounted(async () => {
  await initializeSession()
  if (!sessionState.user?.is_admin) {
    message.error('需要管理员权限')
    await router.replace('/')
    return
  }
  await loadAll()
})
</script>

<style scoped>
.operations-page {
  width: min(1240px, calc(100% - 32px));
  margin: 0 auto;
  padding: 32px 0 56px;
}

.page-heading,
.section-heading,
.status-band {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
}

.page-heading h1,
.data-section h2,
.status-band h2 {
  margin: 0;
  color: #172624;
  letter-spacing: 0;
}

.page-heading h1 { font-size: 28px; }
.data-section h2,
.status-band h2 { font-size: 17px; }

.eyebrow {
  margin: 0 0 4px;
  color: #27796e;
  font-size: 13px;
  font-weight: 700;
}

.heading-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin: 24px 0;
}

.metric-grid :deep(.ant-card) { border-radius: 6px; }

.status-band,
.data-section {
  margin-top: 24px;
  padding: 22px 0;
  border-top: 1px solid #dce3e1;
}

.status-band > div { flex: 1; }
.status-band h2 { margin-bottom: 12px; }
.section-heading p { margin: 4px 0 0; color: #67736f; }
.trace-heading { align-items: center; }
.trace-search { width: min(420px, 100%); }
.trace-link { padding: 0; }
.muted { color: #85908d; }
.trace-json {
  max-height: 320px;
  overflow: auto;
  padding: 14px;
  color: #dce8e5;
  background: #152321;
  border-radius: 4px;
  white-space: pre-wrap;
}

h3 { margin-top: 24px; font-size: 15px; letter-spacing: 0; }

@media (max-width: 900px) {
  .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .status-band { flex-direction: column; }
}

@media (max-width: 640px) {
  .operations-page { width: min(100% - 24px, 1240px); padding-top: 20px; }
  .page-heading,
  .section-heading { flex-direction: column; }
  .heading-actions { width: 100%; justify-content: space-between; }
  .metric-grid { grid-template-columns: 1fr 1fr; }
  .trace-search { width: 100%; }
}
</style>
