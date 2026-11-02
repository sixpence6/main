import { createRouter, createWebHistory, type Router, type RouterHistory } from 'vue-router'
import WorkspaceLayout from '@/layouts/WorkspaceLayout.vue'
import ChatPage from '@/pages/ChatPage.vue'
import ResultsPage from '@/pages/ResultsPage.vue'
import LoginPage from '@/pages/LoginPage.vue'
import AdminAccountsPage from '@/pages/AdminAccountsPage.vue'
import AccountSecurityPage from '@/pages/AccountSecurityPage.vue'
import SetPasswordPage from '@/pages/SetPasswordPage.vue'

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
          { path: '', name: 'describe', component: ChatPage },
          { path: 'projects/:projectId/chat', name: 'project-chat', component: ChatPage },
          { path: 'projects/:projectId/results', name: 'project-results', component: ResultsPage },
        ],
      },
      // 外部遗留书签只做无副作用跳转，不重放确认、生成或恢复动作。
      { path: '/describe', redirect: (to) => ({ name: 'describe', query: to.query, hash: to.hash }) },
      { path: '/confirm', redirect: (to) => ({ name: 'describe', query: to.query, hash: to.hash }) },
      { path: '/generate', redirect: (to) => ({ name: 'describe', query: to.query, hash: to.hash }) },
      { path: '/results', redirect: (to) => ({ name: 'describe', query: to.query, hash: to.hash }) },
      { path: '/:pathMatch(.*)*', redirect: { name: 'describe' } },
    ],
  })
}
