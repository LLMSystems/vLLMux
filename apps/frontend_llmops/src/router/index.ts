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
      path: '/playground',
      name: 'playground',
      meta: { title: 'Playground' },
      component: () => import('@/views/PlaygroundView.vue'),
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
