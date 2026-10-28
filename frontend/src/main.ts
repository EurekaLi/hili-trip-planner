import { createApp } from 'vue'
import type { Plugin } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import {
  Affix,
  Alert,
  BackTop,
  Button,
  Card,
  Checkbox,
  Col,
  Collapse,
  DatePicker,
  Descriptions,
  Divider,
  Drawer,
  Dropdown,
  Empty,
  Form,
  Input,
  InputNumber,
  Layout,
  List,
  Menu,
  Modal,
  Popconfirm,
  Progress,
  Rate,
  Result as AntResult,
  Row,
  Segmented,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Timeline
} from 'ant-design-vue'
import 'ant-design-vue/dist/reset.css'
import App from './App.vue'

const Home = () => import('./views/Home.vue')
const Result = () => import('./views/Result.vue')
const Library = () => import('./views/Library.vue')
const Share = () => import('./views/Share.vue')
const Operations = () => import('./views/Operations.vue')
const Privacy = () => import('./views/Privacy.vue')

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'Home',
      component: Home
    },
    {
      path: '/result',
      name: 'Result',
      component: Result
    },
    {
      path: '/trips',
      name: 'Library',
      component: Library
    },
    {
      path: '/trips/:tripId',
      name: 'SavedTrip',
      component: Result
    },
    {
      path: '/share/:token',
      name: 'SharedTrip',
      component: Share
    },
    {
      path: '/operations',
      name: 'Operations',
      component: Operations
    },
    {
      path: '/privacy',
      name: 'Privacy',
      component: Privacy
    }
  ]
})

const app = createApp(App)

app.use(router)
const antdComponents = [
  Affix,
  Alert,
  BackTop,
  Button,
  Card,
  Checkbox,
  Col,
  Collapse,
  DatePicker,
  Descriptions,
  Divider,
  Drawer,
  Dropdown,
  Empty,
  Form,
  Input,
  InputNumber,
  Layout,
  List,
  Menu,
  Modal,
  Popconfirm,
  Progress,
  Rate,
  AntResult,
  Row,
  Segmented,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Timeline
]
antdComponents.forEach((component) => app.use(component as Plugin))

app.mount('#app')

