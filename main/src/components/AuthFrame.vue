<script setup lang="ts">
import { useId } from 'vue'
import BrandMark from '@/components/BrandMark.vue'

defineProps<{
  eyebrow: string
  title: string
  description: string
}>()

const titleId = useId()
</script>

<template>
  <main class="auth-frame">
    <div class="auth-frame-shell">
      <aside class="auth-story" aria-label="产品介绍">
        <p class="auth-lockup">
          <BrandMark :size="38" />
          <span>
            <strong>电商生图工作台</strong>
            <small>LOCAL IMAGE WORKBENCH</small>
          </span>
        </p>

        <div class="auth-story-copy">
          <p class="auth-story-kicker">从描述到交付，一条本地工作流</p>
          <p class="auth-story-title">把商品想法，整理成一张可交付的主图。</p>
          <p class="auth-story-text">
            需求澄清、单图生成、独立检查与人工发布都在同一条流程里完成。每次确认只受理一个清晰任务，不排队、不自动重发。
          </p>
        </div>

        <ul class="auth-points">
          <li><span>01</span>一次授权，结果与额度都有据可查</li>
          <li><span>02</span>账号、项目与文件访问彼此隔离</li>
          <li><span>03</span>桌面与手机均可键盘操作</li>
        </ul>

        <div class="auth-canvas" aria-hidden="true">
          <div class="auth-canvas-frame">
            <span class="auth-canvas-glow"></span>
            <span class="auth-canvas-card auth-canvas-card-a"></span>
            <span class="auth-canvas-card auth-canvas-card-b"></span>
            <span class="auth-canvas-caption">PRODUCT STUDY · 01</span>
          </div>
        </div>

        <p class="auth-local">本地部署 · 自有账号 · 受控文件访问</p>
      </aside>

      <section class="auth-card" :aria-labelledby="titleId">
        <header class="auth-card-head">
          <p class="auth-eyebrow">{{ eyebrow }}</p>
          <h1 :id="titleId">{{ title }}</h1>
          <p class="auth-description">{{ description }}</p>
        </header>
        <slot />
        <footer class="auth-card-footer">
          <slot name="footer">
            <p>本系统不开放自助注册，也不发送邮件。需要账号或重置链接时，请联系管理员。</p>
          </slot>
        </footer>
      </section>
    </div>
  </main>
</template>

<style scoped>
.auth-frame {
  min-height: 100%;
  display: grid;
  place-items: center;
  padding: 32px 24px;
  background:
    radial-gradient(circle at 12% 8%, rgba(184, 154, 104, 0.2), transparent 34%),
    radial-gradient(circle at 88% 92%, rgba(112, 82, 57, 0.14), transparent 32%),
    var(--color-page);
}

.auth-frame-shell {
  width: min(1120px, 100%);
  min-height: 660px;
  display: grid;
  grid-template-columns: minmax(0, 1.06fr) minmax(420px, 0.94fr);
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: 24px;
  background: var(--color-panel);
  box-shadow: 0 28px 80px rgba(70, 50, 34, 0.13);
}

.auth-story {
  position: relative;
  isolation: isolate;
  display: flex;
  flex-direction: column;
  gap: 26px;
  padding: 38px 42px 34px;
  overflow: hidden;
  color: #f9f4ea;
  background:
    linear-gradient(145deg, rgba(44, 39, 35, 0.98), rgba(66, 52, 40, 0.96)),
    var(--color-text);
}

.auth-story::before {
  content: '';
  position: absolute;
  z-index: -1;
  inset: -30% 28% 45% -35%;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(184, 154, 104, 0.34), transparent 62%);
}

.auth-story::after {
  content: '';
  position: absolute;
  z-index: -1;
  inset: 40% -30% -35% 35%;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(112, 82, 57, 0.42), transparent 64%);
}

.auth-lockup {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 0;
}
.auth-lockup :deep(.brand-mark) {
  color: #d8bd8e;
}
.auth-lockup span {
  display: grid;
  gap: 2px;
}
.auth-lockup strong {
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 0.04em;
}
.auth-lockup small {
  color: rgba(249, 244, 234, 0.58);
  font-size: 9px;
  letter-spacing: 0.19em;
}

.auth-story-copy {
  max-width: 450px;
  margin-top: 16px;
}
.auth-story-kicker {
  margin: 0 0 12px;
  color: #d8bd8e;
  font-size: 12px;
  letter-spacing: 0.12em;
}
.auth-story-title {
  margin: 0;
  font-family: 'Songti SC', 'STSong', 'Noto Serif SC', serif;
  font-size: clamp(28px, 3.2vw, 43px);
  line-height: 1.28;
  font-weight: 600;
  letter-spacing: -0.02em;
}
.auth-story-text {
  margin: 18px 0 0;
  max-width: 410px;
  color: rgba(249, 244, 234, 0.7);
  font-size: 13px;
  line-height: 1.85;
}

.auth-points {
  display: grid;
  gap: 9px;
  margin: 0;
  padding: 0;
  list-style: none;
  color: rgba(249, 244, 234, 0.76);
  font-size: 12px;
}
.auth-points li {
  display: flex;
  align-items: center;
  gap: 10px;
}
.auth-points span {
  color: #d8bd8e;
  font-size: 10px;
  letter-spacing: 0.08em;
}

.auth-canvas {
  display: flex;
  flex: 1;
  align-items: flex-end;
  min-height: 150px;
  margin-top: auto;
}
.auth-canvas-frame {
  position: relative;
  width: min(330px, 86%);
  aspect-ratio: 4 / 3;
  margin-left: auto;
  overflow: hidden;
  border: 1px solid rgba(216, 189, 142, 0.34);
  border-radius: 18px;
  background:
    linear-gradient(135deg, rgba(255, 252, 247, 0.11), rgba(255, 252, 247, 0.03)),
    repeating-linear-gradient(90deg, transparent 0 31px, rgba(216, 189, 142, 0.09) 31px 32px),
    repeating-linear-gradient(0deg, transparent 0 31px, rgba(216, 189, 142, 0.09) 31px 32px);
  transform: rotate(-3deg);
}
.auth-canvas-glow {
  position: absolute;
  inset: 18% 12% auto auto;
  width: 105px;
  height: 105px;
  border-radius: 50%;
  background: radial-gradient(circle at 35% 32%, #ead7b4, #b89a68 52%, #705239 100%);
  box-shadow: 0 18px 34px rgba(25, 17, 10, 0.3);
}
.auth-canvas-card {
  position: absolute;
  border: 1px solid rgba(249, 244, 234, 0.44);
  border-radius: 12px;
  background: rgba(249, 244, 234, 0.09);
  backdrop-filter: blur(3px);
}
.auth-canvas-card-a {
  inset: auto auto 18% 12%;
  width: 46%;
  height: 34%;
  transform: rotate(7deg);
}
.auth-canvas-card-b {
  inset: 17% auto auto 41%;
  width: 21%;
  height: 46%;
  border-radius: 999px;
  transform: rotate(18deg);
}
.auth-canvas-caption {
  position: absolute;
  left: 14px;
  bottom: 12px;
  color: rgba(249, 244, 234, 0.5);
  font-size: 8px;
  letter-spacing: 0.18em;
}

.auth-local {
  margin: 0;
  color: rgba(249, 244, 234, 0.42);
  font-size: 10px;
  letter-spacing: 0.1em;
}

.auth-card {
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 48px 48px 36px;
  background:
    radial-gradient(circle at 100% 0, rgba(184, 154, 104, 0.12), transparent 28%),
    var(--color-panel);
}
.auth-card-head {
  margin-bottom: 28px;
}
.auth-eyebrow {
  margin: 0 0 10px;
  color: var(--color-accent);
  font-size: 10px;
  letter-spacing: 0.2em;
}
h1 {
  margin: 0;
  font-size: clamp(28px, 3vw, 34px);
  line-height: 1.2;
  font-weight: 600;
  letter-spacing: -0.02em;
}
.auth-description {
  margin: 12px 0 0;
  max-width: 390px;
  color: var(--color-text-muted);
  font-size: 13px;
  line-height: 1.75;
}
.auth-card-footer {
  margin-top: 26px;
  padding-top: 18px;
  border-top: 1px solid var(--color-border);
}
.auth-card-footer :deep(p) {
  margin: 0;
  color: var(--color-text-muted);
  font-size: 11px;
  line-height: 1.7;
}

@media (max-width: 900px) {
  .auth-frame {
    padding: 20px;
  }
  .auth-frame-shell {
    min-height: 0;
    grid-template-columns: 1fr;
  }
  .auth-story {
    gap: 16px;
    padding: 24px 26px;
  }
  .auth-story-copy {
    margin-top: 4px;
  }
  .auth-story-title {
    font-size: 26px;
  }
  .auth-story-text,
  .auth-points,
  .auth-canvas {
    display: none;
  }
  .auth-local {
    display: none;
  }
  .auth-card {
    padding: 34px 30px 28px;
  }
}

@media (max-width: 520px) {
  .auth-frame {
    padding: 0;
    place-items: stretch;
  }
  .auth-frame-shell {
    min-height: 100%;
    border: 0;
    border-radius: 0;
    box-shadow: none;
  }
  .auth-story {
    padding: 20px 20px 18px;
  }
  .auth-story-kicker {
    margin-bottom: 6px;
    font-size: 10px;
  }
  .auth-story-title {
    font-size: 21px;
  }
  .auth-card {
    padding: 28px 20px 24px;
  }
}
</style>
