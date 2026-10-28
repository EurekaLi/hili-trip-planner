import { expect, test, type Page, type Route } from '@playwright/test'

const tripPlan = {
  city: '北京',
  start_date: '2026-10-01',
  end_date: '2026-10-01',
  overall_suggestions: '提前预约并错峰游览。',
  budget: {
    total_attractions: 60,
    total_hotels: 280,
    total_meals: 180,
    total_transportation: 30,
    total: 550,
    unknown_cost_items: []
  },
  weather_info: [
    {
      date: '2026-10-01',
      day_weather: '晴',
      night_weather: '晴',
      day_temp: 24,
      night_temp: 14,
      wind_direction: '北',
      wind_power: '1-3'
    }
  ],
  warnings: [],
  days: [
    {
      date: '2026-10-01',
      day_index: 0,
      description: '上午游览故宫，下午参观景山。',
      transportation: '公共交通',
      accommodation: '经济型酒店',
      attractions: [
        {
          name: '故宫博物院',
          address: '景山前街4号',
          location: { longitude: 116.397029, latitude: 39.917839 },
          visit_duration: 240,
          description: '明清皇家宫殿建筑群。',
          poi_id: 'B000A8UIN8',
          image_url: 'https://example.invalid/forbidden-city.jpg',
          ticket_price: 60
        }
      ],
      meals: [],
      routes: [],
      timeline: [
        {
          kind: 'attraction',
          start_time: '09:00',
          end_time: '13:00',
          title: '故宫博物院',
          status: 'scheduled',
          poi_id: 'B000A8UIN8'
        }
      ],
      schedule_status: 'feasible',
      total_visit_minutes: 240,
      total_transit_minutes: 0
    }
  ]
}

const succeededJob = {
  job_id: 'e2e-job-1',
  status: 'succeeded',
  stage: 'completed',
  progress: 100,
  message: '旅行计划生成成功',
  result: tripPlan,
  saved_trip_id: null,
  attempt_count: 1,
  max_attempts: 2,
  created_at: '2026-09-17T00:00:00Z',
  updated_at: '2026-09-17T00:00:02Z',
  finished_at: '2026-09-17T00:00:02Z',
  revision: 3
}

/**
 * 返回统一 JSON 响应，避免每个路由重复响应头。
 *
 * @param route Playwright 当前拦截的路由。
 * @param body 需要序列化的响应正文。
 * @param status HTTP 状态码。
 */
async function fulfillJson(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body)
  })
}

/**
 * 拦截浏览器 API 请求，提供无付费且确定性的完整任务生命周期。
 *
 * @param page 当前测试页面。
 */
async function installApiMocks(page: Page): Promise<void> {
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())

    if (url.pathname === '/api/auth/me') {
      await fulfillJson(route, {
        authenticated: false,
        user: null,
        claimed_trip_count: 0
      })
      return
    }
    if (url.pathname === '/api/compliance/security') {
      await fulfillJson(route, {
        turnstile_enabled: false,
        turnstile_site_key: null
      })
      return
    }
    if (url.pathname === '/api/trip/jobs' && request.method() === 'POST') {
      await fulfillJson(route, {
        job_id: 'e2e-job-1',
        status: 'queued',
        stage: 'queued',
        progress: 0,
        message: '任务已提交',
        created: true,
        attempt_count: 0,
        max_attempts: 2,
        saved_trip_id: null
      }, 202)
      return
    }
    if (url.pathname === '/api/trip/jobs/e2e-job-1/events') {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `event: trip-job\ndata: ${JSON.stringify(succeededJob)}\n\n`
      })
      return
    }
    if (url.pathname === '/api/trip/jobs/e2e-job-1') {
      await fulfillJson(route, succeededJob)
      return
    }
    if (url.pathname === '/api/poi/photo') {
      await fulfillJson(route, { success: true, data: null })
      return
    }
    await fulfillJson(route, { detail: `Unhandled E2E route: ${url.pathname}` }, 404)
  })
}

test.beforeEach(async ({ page }) => {
  await installApiMocks(page)
})

test('submits an asynchronous trip and renders the result', async ({ page }) => {
  await page.goto('/')
  await page.getByPlaceholder('例如: 北京').fill('北京')

  const visitDate = new Date()
  visitDate.setDate(visitDate.getDate() + 7)
  const dateTitle = [
    visitDate.getFullYear(),
    String(visitDate.getMonth() + 1).padStart(2, '0'),
    String(visitDate.getDate()).padStart(2, '0')
  ].join('-')
  const dateInputs = page.getByPlaceholder('选择日期')
  await dateInputs.nth(0).click()
  await page.locator('.ant-picker-dropdown:visible').locator(`[title="${dateTitle}"]`).click()
  await dateInputs.nth(1).click()
  await page.locator('.ant-picker-dropdown:visible').locator(`[title="${dateTitle}"]`).click()

  await page.getByRole('button', { name: '开始规划我的旅行' }).click()

  await expect(page).toHaveURL(/\/result$/)
  await expect(page.getByText('北京旅行计划')).toBeVisible()
  await expect(page.getByText('故宫博物院').first()).toBeVisible()
  await expect(page.getByText('预估总费用')).toBeVisible()
})

test('keeps the primary workflow inside the mobile viewport', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '智能旅行助手' })).toBeVisible()

  const overflow = await page.evaluate(() => (
    document.documentElement.scrollWidth - document.documentElement.clientWidth
  ))
  expect(overflow).toBeLessThanOrEqual(1)
  await expect(page.getByRole('button', { name: '开始规划我的旅行' })).toBeVisible()
})
