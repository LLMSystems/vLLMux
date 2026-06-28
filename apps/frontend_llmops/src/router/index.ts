import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'overview',
      meta: { title: 'overview' },
      component: () => import('@/views/OverviewView.vue'),
    },
    {
      path: '/models',
      name: 'models',
      meta: { title: 'models' },
      component: () => import('@/views/ModelsView.vue'),
    },
    {
      path: '/traffic',
      name: 'traffic',
      meta: { title: 'traffic' },
      component: () => import('@/views/TrafficView.vue'),
    },
    {
      path: '/requests',
      name: 'requests',
      meta: { title: 'requests' },
      component: () => import('@/views/RequestsView.vue'),
    },
    {
      path: '/monitoring',
      name: 'monitoring',
      meta: { title: 'monitoring' },
      component: () => import('@/views/MonitoringView.vue'),
    },
    {
      path: '/benchmark',
      name: 'benchmark',
      meta: { title: 'benchmark' },
      component: () => import('@/views/BenchmarkView.vue'),
    },
    {
      path: '/playground',
      name: 'playground',
      meta: { title: 'playground' },
      component: () => import('@/views/PlaygroundView.vue'),
    },
    {
      path: '/library',
      name: 'library',
      meta: { title: 'library' },
      component: () => import('@/views/LibraryView.vue'),
    },
    {
      path: '/lora-library',
      name: 'lora-library',
      meta: { title: 'loraLibrary' },
      component: () => import('@/views/LoraLibraryView.vue'),
    },
    {
      path: '/datasets',
      name: 'datasets',
      meta: { title: 'datasets' },
      component: () => import('@/views/DatasetsView.vue'),
    },
    {
      path: '/eval',
      name: 'eval',
      meta: { title: 'eval' },
      component: () => import('@/views/EvalView.vue'),
    },
    {
      path: '/keys',
      name: 'keys',
      meta: { title: 'keys' },
      component: () => import('@/views/KeysView.vue'),
    },
    {
      path: '/operators',
      name: 'operators',
      meta: { title: 'operators' },
      component: () => import('@/views/OperatorsView.vue'),
    },
    {
      path: '/audit',
      name: 'audit',
      meta: { title: 'audit' },
      component: () => import('@/views/AuditView.vue'),
    },
    {
      path: '/notifications',
      name: 'notifications',
      meta: { title: 'notifications' },
      component: () => import('@/views/NotificationsView.vue'),
    },
    {
      path: '/cost',
      name: 'cost',
      meta: { title: 'cost' },
      component: () => import('@/views/CostView.vue'),
    },
    {
      path: '/config',
      name: 'config',
      meta: { title: 'config' },
      component: () => import('@/views/ConfigVersionsView.vue'),
    },
    {
      path: '/usage',
      name: 'usage',
      meta: { title: 'usage' },
      component: () => import('@/views/UsageView.vue'),
    },
    {
      path: '/resources',
      name: 'resources',
      meta: { title: 'resources' },
      component: () => import('@/views/ResourcesView.vue'),
    },
    {
      path: '/activity',
      name: 'activity',
      meta: { title: 'activity' },
      component: () => import('@/views/ActivityView.vue'),
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

export default router
