import { createRouter, createWebHistory, type Router, type RouterHistory } from 'vue-router'
import WorkspaceLayout from '@/layouts/WorkspaceLayout.vue'
import DescribePage from '@/pages/DescribePage.vue'
import ConfirmPage from '@/pages/ConfirmPage.vue'
import GeneratePage from '@/pages/GeneratePage.vue'
import ResultsPage from '@/pages/ResultsPage.vue'

export const STAGE_ROUTE_NAMES = ['describe', 'confirm', 'generate', 'results'] as const

export function createAppRouter(history?: RouterHistory): Router {
  return createRouter({
    history: history ?? createWebHistory(),
    routes: [
      {
        path: '/',
        component: WorkspaceLayout,
        children: [
          { path: '', name: 'describe', component: DescribePage },
          { path: 'confirm', name: 'confirm', component: ConfirmPage },
          { path: 'generate', name: 'generate', component: GeneratePage },
          { path: 'results', name: 'results', component: ResultsPage },
        ],
      },
      { path: '/:pathMatch(.*)*', redirect: { name: 'describe' } },
    ],
  })
}
