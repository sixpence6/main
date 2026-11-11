import { createApp } from 'vue'
import './styles/tokens.css'
import './styles/base.css'
import App from './App.vue'
import { createAppRouter } from './router'
import { installAuthGuard, installSessionLifecycle } from './auth/session'

const router = createAppRouter()
installAuthGuard(router)
installSessionLifecycle()

createApp(App).use(router).mount('#app')
