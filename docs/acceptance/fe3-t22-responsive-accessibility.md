# T22 (#108) responsive, accessible single-body workspace

## Baseline and scope

- Baseline: `4e82f2c` (integration/v3 after T21)
- Implementation: `12d3fa5` on `ticket/108`
- Scope: own brand and assistant signature, workspace-only light gray-purple palette, single-body chat with a bottom composer, 768-1023 drawer behaviour, 320/1440 reachability, focus/drawer/scroll/input-growth accessibility, and cleanup of the replaced four-stage/feedback presentation.
- Unchanged: login/account design, owner/account isolation, workflow gates, HTTP contracts, one-authorization and private-resource rules.

## Behaviour

| Situation | Result |
| --- | --- |
| Brand | Header, login frame and account pages show 电商生图工作台; assistant cards show 智能生图助手 |
| Desktop >=1024 | 288px rounded sidebar, 68px rounded top bar, centered 880px message column, bottom composer in one white main panel |
| Tablet 768-1023 | No permanent sidebar; one 打开侧栏 trigger opens a focus-trapped drawer |
| Small screen <768 | One overlay drawer; no second menu trigger; composer and reference uploader stack vertically |
| 320px width | Document scrollWidth 320; composer panel bottom 695 of 720; send and reference controls visible after a 14-line input |
| User scroll-up | `.chat-scroll` keeps scrollTop 120 after 2600ms of polling; new state does not force the bottom |
| Input growth | 14-line input grows the composer 278→348 and shrinks the message area 337→267; clearing restores 276/339 |
| Drawer close | Body overflow restored; focus returns to the 打开侧栏 trigger |
| 返回修改 | Focuses the visible `描述商品与图片需求` input with zero writes (covered by t104) |
| Reduced motion | Computed workspace scroll-behavior is `auto` |
| Old presentation | DescribePage/PageTemplate removed; dead stage-nav, app-page, result-panel and workflow-feedback CSS removed; no `.workflow-feedback` element |
| Results | No composer and no feedback form; image uses `object-fit: contain` |

## Commands and results

```text
cd .worktrees/t108
npx vitest run tests/t22-responsive-brand.spec.ts tests/t01-workspace.spec.ts
# 2 files passed, 17 tests passed in 4.65s

npx vitest run
# 46 files passed, 217 tests passed in 28.17s
# PowerShell *> log; $LASTEXITCODE = 0

npx vue-tsc --noEmit
# exit 0, no diagnostics
```

Real Edge browser check (playwright-core 1.64.1, system Edge, mocked `/api`):

```text
1440: documentWidth=1440, sidebar=288, chatScroll=880x337,
      composerPanel bottom=959, composer bottom=951, no horizontal overflow
768:  documentWidth=768, sidebar=null, chatScroll height=205,
      composer bottom=863, drawer width=272
320:  documentWidth=320, sidebar=null, chatScroll height=82,
      composerPanel bottom=695, composer bottom=691, drawer width=288,
      no horizontal overflow
scroll: scrollable=true, scrollHeight=2392, clientHeight=337, scrollTopAfterPolling=120
inputGrowth: before 278/337 -> after 348/267, input height=240, overflowY=auto
mobile input growth: sendVisible=true, referenceVisible=true, docScrollWidth=320
drawer: bodyOverflow=hidden, activeInside=true, close focus=打开侧栏
results: hasComposer=false, hasWorkflowFeedback=false, imageFit=contain
pageErrors: [], unknownRoutes: [], ok=true
```

Screenshots:

- `D:/.dsh/tmp/spec86/t22-1440-chat.png` (85,003 bytes)
- `D:/.dsh/tmp/spec86/t22-768-drawer.png` (61,855 bytes)
- `D:/.dsh/tmp/spec86/t22-320-drawer.png` (14,598 bytes)
- `D:/.dsh/tmp/spec86/t22-1440-results.png` (40,954 bytes)

## Acceptance mapping

- **FE3-AC-01**: pass. The workspace uses the confirmed own brand 电商生图工作台 and 智能生图助手 signature, a scoped light gray-purple/white/purple palette, and local CSS/assets only. Login and account pages keep their existing design.
- **FE3-AC-02**: pass. Top bar, message stream and bottom composer form one body; WorkflowPanel is an in-stream card, not a permanent right column. 768-1023 uses one drawer trigger and results has no composer, so no permanent chat/workflow dual column or four-page required navigation is restored.
- **FE3-AC-14**: pass for 320/768/1440 virtual viewports. No whole-page horizontal overflow; body, send, reference and results controls are reachable; drawer focus/background scroll and focus restore work; helper labels are visible; input growth only compresses the message area; reduced-motion behaviour is preserved.

## Calls and isolation

All checks use the existing frontend HTTP seam with mocked responses. Model text calls: 0; model vision calls: 0; image-generation calls: 0; provider network calls: 0. No account, owner, project, workflow or generation route was changed by T22.

## Residuals

- Real physical mobile devices, soft-keyboard occlusion and OS safe-area values were not available. Only virtual viewports and CSS `env(safe-area-inset-bottom)` were exercised; these items remain for final V3 release re-verification.
- The browser check uses mocked HTTP, not a live backend or provider.
- Firefox and Safari were not run; Edge Chromium is the only real browser.
- No backend suite was rerun because this ticket changes only frontend presentation, CSS and shell routing.
- Screenshots are implementation evidence, not a substitute for a real-device visual review.
