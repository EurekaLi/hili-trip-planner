<template>
  <main class="share-page">
    <a-spin :spinning="loading">
      <a-result v-if="errorMessage" status="404" title="分享不可用" :sub-title="errorMessage" />
      <template v-else-if="sharedTrip">
        <header class="share-header">
          <p>只读分享 · 版本 {{ sharedTrip.version_number }}</p>
          <h1>{{ sharedTrip.title }}</h1>
          <div class="share-meta">
            <span><EnvironmentOutlined /> {{ sharedTrip.plan.city }}</span>
            <span><CalendarOutlined /> {{ sharedTrip.plan.start_date }} 至 {{ sharedTrip.plan.end_date }}</span>
          </div>
        </header>

        <section
          v-for="day in sharedTrip.plan.days"
          :key="day.day_index"
          class="shared-day"
        >
          <div class="day-heading">
            <div>
              <span>第 {{ day.day_index + 1 }} 天</span>
              <h2>{{ day.date }}</h2>
            </div>
            <a-tag :color="day.schedule_status === 'feasible' ? 'green' : 'gold'">
              {{ day.schedule_status === 'feasible' ? '可执行' : '含估算信息' }}
            </a-tag>
          </div>
          <p class="day-description">{{ day.description }}</p>
          <div class="shared-attractions">
            <article v-for="attraction in day.attractions" :key="attraction.poi_id || attraction.name">
              <div>
                <h3>{{ attraction.name }}</h3>
                <p>{{ attraction.address }}</p>
              </div>
              <span>{{ attraction.visit_duration }} 分钟</span>
            </article>
          </div>
          <div v-if="day.timeline.length" class="shared-timeline">
            <div v-for="entry in day.timeline" :key="`${entry.start_time}-${entry.title}`">
              <time>{{ entry.start_time }}–{{ entry.end_time }}</time>
              <span>{{ entry.title }}</span>
            </div>
          </div>
        </section>

        <section class="feedback-section">
          <div>
            <h2>这份行程有帮助吗？</h2>
            <p>反馈会提供给行程创建者，用于继续改进行程。</p>
          </div>
          <a-rate v-model:value="feedbackRating" />
          <a-textarea
            v-model:value="feedbackComment"
            :rows="3"
            :maxlength="2000"
            placeholder="可选：写下你的建议"
          />
          <a-button type="primary" :loading="submitting" @click="submitFeedback">
            <SendOutlined />
            提交反馈
          </a-button>
        </section>
      </template>
    </a-spin>
  </main>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { message } from 'ant-design-vue'
import {
  CalendarOutlined,
  EnvironmentOutlined,
  SendOutlined
} from '@ant-design/icons-vue'
import { getSharedTrip, submitTripFeedback } from '@/services/api'
import type { SharedTrip } from '@/types'

const route = useRoute()
const loading = ref(true)
const submitting = ref(false)
const errorMessage = ref('')
const sharedTrip = ref<SharedTrip | null>(null)
const feedbackRating = ref(5)
const feedbackComment = ref('')

/**
 * 读取当前 URL 令牌绑定的固定行程版本。
 *
 * @returns 无返回值。
 */
const loadSharedTrip = async (): Promise<void> => {
  loading.value = true
  try {
    sharedTrip.value = await getSharedTrip(String(route.params.token))
  } catch (error: any) {
    errorMessage.value = error.response?.data?.detail || error.message || '链接已失效'
  } finally {
    loading.value = false
  }
}

/**
 * 提交访问者评分和可选文本反馈。
 *
 * @returns 无返回值。
 */
const submitFeedback = async (): Promise<void> => {
  if (submitting.value) return
  submitting.value = true
  try {
    await submitTripFeedback(
      String(route.params.token),
      feedbackRating.value,
      feedbackComment.value
    )
    feedbackComment.value = ''
    message.success('感谢你的反馈')
  } catch (error: any) {
    message.error(error.response?.data?.detail || error.message || '反馈提交失败')
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  void loadSharedTrip()
})
</script>

<style scoped>
.share-page {
  width: min(900px, calc(100% - 32px));
  min-height: 70vh;
  margin: 0 auto;
  padding: 48px 0 72px;
}

.share-header {
  padding-bottom: 28px;
  border-bottom: 1px solid #cfd8d5;
}

.share-header > p {
  color: #24766c;
  font-weight: 700;
}

.share-header h1 {
  margin: 8px 0 18px;
  font-size: 38px;
}

.share-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 20px;
  color: #53625e;
}

.shared-day {
  padding: 32px 0;
  border-bottom: 1px solid #cfd8d5;
}

.day-heading,
.shared-attractions article,
.shared-timeline > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
}

.day-heading span {
  color: #24766c;
  font-weight: 700;
}

.day-heading h2,
.shared-attractions h3 {
  margin: 4px 0;
}

.day-description,
.shared-attractions p,
.feedback-section p {
  color: #62706c;
}

.shared-attractions article {
  padding: 14px 0;
  border-bottom: 1px dashed #d7dddb;
}

.shared-timeline {
  margin-top: 20px;
  padding-left: 16px;
  border-left: 3px solid #d7a93d;
}

.shared-timeline > div {
  justify-content: flex-start;
  padding: 8px 0;
}

.shared-timeline time {
  width: 110px;
  color: #24766c;
  font-variant-numeric: tabular-nums;
}

.feedback-section {
  display: grid;
  gap: 16px;
  margin-top: 40px;
  padding: 28px;
  background: #fff;
  border: 1px solid #d7dddb;
  border-radius: 6px;
}

.feedback-section h2,
.feedback-section p {
  margin: 0 0 6px;
}

.feedback-section .ant-btn {
  justify-self: start;
}
</style>
