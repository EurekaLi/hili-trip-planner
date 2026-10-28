import { check } from 'k6'
import http from 'k6/http'

const baseUrl = __ENV.BASE_URL || 'http://127.0.0.1:8080'

export const options = {
  scenarios: {
    public_read_baseline: {
      executor: 'constant-arrival-rate',
      rate: Number(__ENV.REQUEST_RATE || 10),
      timeUnit: '1s',
      duration: __ENV.DURATION || '30s',
      preAllocatedVUs: 5,
      maxVUs: 30
    }
  },
  thresholds: {
    checks: ['rate>0.99'],
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<750']
  }
}

/**
 * 请求不触发 LLM/MCP 的公开端点，建立 API 和反向代理读性能基线。
 */
export default function publicReadScenario() {
  const live = http.get(`${baseUrl}/health/live`, { tags: { endpoint: 'live' } })
  check(live, { 'liveness returns 200': (response) => response.status === 200 })

  const privacy = http.get(`${baseUrl}/api/compliance/privacy`, {
    tags: { endpoint: 'privacy' }
  })
  check(privacy, { 'privacy returns 200': (response) => response.status === 200 })
}

/**
 * 把 k6 聚合统计写成机器可读报告，供发布证据和趋势比较使用。
 *
 * @param {Record<string, unknown>} data k6 汇总数据。
 * @returns {Record<string, string>} 报告文件映射。
 */
export function handleSummary(data) {
  return {
    'reports/k6-read-summary.json': JSON.stringify(data, null, 2),
    stdout: `k6 public-read baseline complete: ${data.metrics.http_reqs.values.count} requests\n`
  }
}
