import { createRouter, createWebHistory, type Router, type RouterHistory } from 'vue-router'
import WorkspaceLayout from '@/layouts/WorkspaceLayout.vue'
import DescribePage from '@/pages/DescribePage.vue'
import ConfirmPage from '@/pages/ConfirmPage.vue'
import GeneratePage from '@/pages/GeneratePage.vue'
import ResultsPage from '@/pages/ResultsPage.vue'
import LoginPage from '@/pages/LoginPage.vue'
import AdminAccountsPage from '@/pages/AdminAccountsPage.vue'
import AccountSecurityPage from '@/pages/AccountSecurityPage.vue'
import SetPasswordPage from '@/pages/SetPasswordPage.vue'

export const STAGE_ROUTE_NAMES = ['describe', 'confirm', 'generate', 'results'] as const

export function createAppRouter(history?: RouterHistory): Router {
  return createRouter({
    history: history ?? createWebHistory(),
    routes: [
      { path: '/login', name: 'login', component: LoginPage },
      { path: '/set-password', name: 'set-password', component: SetPasswordPage, meta: { public: true } },
      { path: '/account/security', name: 'account-security', component: AccountSecurityPage },
      {
        path: '/admin/accounts',
        name: 'admin-accounts',
        component: AdminAccountsPage,
        meta: { requiresAdmin: true },
      },
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
