<template>
  <main class="library-page">
    <header class="library-header">
      <div>
        <p class="eyebrow">行程资产</p>
        <h1>我的行程</h1>
        <p>游客草稿会保存在当前浏览器，登录后可跨设备恢复。</p>
      </div>
      <a-button type="primary" size="large" @click="router.push('/')">
        <PlusOutlined />
        新建行程
      </a-button>
    </header>

    <a-spin :spinning="loading">
      <a-empty v-if="!loading && trips.length === 0" description="还没有保存的行程">
        <a-button type="primary" @click="router.push('/')">开始规划</a-button>
      </a-empty>
      <div v-else class="trip-list">
        <article v-for="trip in trips" :key="trip.id" class="trip-row">
          <div class="trip-date">
            <CalendarOutlined />
            <span>{{ trip.start_date }} 至 {{ trip.end_date }}</span>
          </div>
          <div class="trip-main">
            <h2>{{ trip.title }}</h2>
            <span>{{ trip.city }}</span>
          </div>
          <div class="trip-version">版本 {{ trip.current_version }}</div>
          <div class="trip-actions">
            <a-button type="primary" @click="openTrip(trip.id)">
              <EyeOutlined />
              查看
            </a-button>
            <a-popconfirm
              title="删除后将从列表隐藏，确定继续吗？"
              ok-text="删除"
              cancel-text="取消"
              @confirm="removeTrip(trip.id)"
            >
              <a-button danger aria-label="删除行程">
                <DeleteOutlined />
              </a-button>
            </a-popconfirm>
          </div>
        </article>
      </div>
    </a-spin>
  </main>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import {
  CalendarOutlined,
  DeleteOutlined,
  EyeOutlined,
  PlusOutlined
} from '@ant-design/icons-vue'
import { deleteSavedTrip, listSavedTrips } from '@/services/api'
import type { TripSummary } from '@/types'

const router = useRouter()
const loading = ref(false)
const trips = ref<TripSummary[]>([])

/**
 * 从服务器加载当前用户或游客的行程摘要。
 *
 * @returns 无返回值。
 */
const loadTrips = async (): Promise<void> => {
  loading.value = true
  try {
    trips.value = await listSavedTrips()
  } catch (error: any) {
    message.error(error.response?.data?.detail || error.message || '行程列表加载失败')
  } finally {
    loading.value = false
  }
}

/**
 * 进入服务器行程详情页。
 *
 * @param tripId 行程资产 ID。
 * @returns 路由跳转 Promise。
 */
const openTrip = (tripId: string): Promise<unknown> => {
  return router.push(`/trips/${tripId}`)
}

/**
 * 软删除行程并刷新当前列表。
 *
 * @param tripId 行程资产 ID。
 * @returns 无返回值。
 */
const removeTrip = async (tripId: string): Promise<void> => {
  try {
    await deleteSavedTrip(tripId)
    trips.value = trips.value.filter((trip) => trip.id !== tripId)
    message.success('行程已删除')
  } catch (error: any) {
    message.error(error.response?.data?.detail || error.message || '删除失败')
  }
}

onMounted(() => {
  void loadTrips()
})
</script>

<style scoped>
.library-page {
  width: min(1120px, calc(100% - 32px));
  margin: 0 auto;
  padding: 48px 0 64px;
}

.library-header {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 32px;
}

.eyebrow {
  margin: 0 0 6px;
  color: #24766c;
  font-weight: 700;
}

h1 {
  margin: 0;
  font-size: 34px;
}

.library-header p:last-child {
  margin: 8px 0 0;
  color: #66736f;
}

.trip-list {
  border-top: 1px solid #d7dddb;
}

.trip-row {
  display: grid;
  grid-template-columns: minmax(180px, 0.9fr) minmax(240px, 1.5fr) 100px auto;
  align-items: center;
  gap: 24px;
  padding: 22px 12px;
  background: #fff;
  border-bottom: 1px solid #d7dddb;
}

.trip-date,
.trip-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.trip-date,
.trip-version {
  color: #66736f;
}

.trip-main h2 {
  margin: 0 0 5px;
  font-size: 18px;
}

.trip-main span {
  color: #24766c;
}

@media (max-width: 760px) {
  .library-header {
    align-items: stretch;
    flex-direction: column;
  }

  .trip-row {
    grid-template-columns: 1fr auto;
  }

  .trip-main {
    grid-column: 1 / -1;
    grid-row: 1;
  }

  .trip-date {
    grid-column: 1 / -1;
  }
}
</style>
