import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import './styles/tokens.css'
import './styles/base.css'
import App from './App.vue'
import { createAppRouter } from './router'
import { installAuthGuard } from './auth/session'

const router = createAppRouter()
installAuthGuard(router)

createApp(App).use(ElementPlus).use(router).mount('#app')
