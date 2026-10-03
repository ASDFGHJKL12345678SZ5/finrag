import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [{ path: '/', name: 'ask', component: () => import('@/views/AskView.vue') }],
})

export default router
