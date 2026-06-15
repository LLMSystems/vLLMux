import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'overview',
      meta: { title: 'Overview' },
      component: () => import('@/views/OverviewView.vue'),
    },
    {
      path: '/models',
      name: 'models',
      meta: { title: 'Models' },
      component: () => import('@/views/ModelsView.vue'),
    },
    {
      path: '/traffic',
      name: 'traffic',
      meta: { title: 'Traffic' },
      component: () => import('@/views/TrafficView.vue'),
    },
    {
      path: '/trends',
      name: 'trends',
      meta: { title: 'Trends' },
      component: () => import('@/views/TrendsView.vue'),
    },
    {
      path: '/requests',
      name: 'requests',
      meta: { title: 'Requests' },
      component: () => import('@/views/RequestsView.vue'),
    },
    {
      path: '/benchmark',
      name: 'benchmark',
      meta: { title: 'Benchmark' },
      component: () => import('@/views/BenchmarkView.vue'),
    },
    {
      path: '/playground',
      name: 'playground',
      meta: { title: 'Playground' },
      component: () => import('@/views/PlaygroundView.vue'),
    },
    {
      path: '/library',
      name: 'library',
      meta: { title: 'Model Library' },
      component: () => import('@/views/LibraryView.vue'),
    },
    {
      path: '/keys',
      name: 'keys',
      meta: { title: 'API Keys' },
      component: () => import('@/views/KeysView.vue'),
    },
    {
      path: '/usage',
      name: 'usage',
      meta: { title: '使用指南' },
      component: () => import('@/views/UsageView.vue'),
    },
    {
      path: '/resources',
      name: 'resources',
      meta: { title: 'Resources' },
      component: () => import('@/views/ResourcesView.vue'),
    },
    {
      path: '/activity',
      name: 'activity',
      meta: { title: 'Activity' },
      component: () => import('@/views/ActivityView.vue'),
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

export default router
