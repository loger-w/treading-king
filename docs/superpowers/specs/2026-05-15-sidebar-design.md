# Sidebar + Multi-page Shell

**Date**: 2026-05-15
**Status**: Implemented(commit `81945cb`)

## ⚠ Revision · 2026-05-15

實作驗收時發現 push 模式在 1960px+ 寬螢幕會讓 Monitor 的 `mx-auto max-w-[1960px]` 置中位置飄移。改用 **overlay drawer** pattern:

- Sidebar 改 `position: absolute` + `z-10`,**浮在內容上方**而非推擠
- 內容區固定 `ml-[56px]`,永遠保留 icon rail 寬度,**Monitor 位置永遠不動**
- Sidebar 展開(56→220)時覆蓋內容最左 164px;收合後內容完整露出
- `useSidebarState` 預設改為 collapsed(配合 overlay 預設 UX)
- 展開時 sidebar 加上 `shadow-[2px_0_12px_rgba(0,0,0,0.5)]` 強化「浮層」視覺

以下章節描述的 push 模式為早期方向,Architecture / State 兩節以本 Revision 為準。

## Summary

加入一個可收合的左側 sidebar,把目前單頁的 frontend(只有 Monitor)變成 multi-page shell。
本次只放兩個導覽項:**即時監控**(沿用現有 `Monitor.tsx`)、**微型台指當沖回測**(空殼 placeholder,內容後續)。

實作上不引入 `react-router-dom`,用 `useState` 切頁;Monitor 採 always-mount + CSS hide,避免切換時斷 WS 重連。

## Goals

- Sidebar 漢堡按鈕點擊可在 220px(展開)↔ 56px(收合 icon rail)之間切換,寬度過渡 200ms ease-out
- 展開/收合狀態以 `localStorage` 持久化,跨 reload 保留
- 兩個 nav item,active 狀態以 2px accent 左邊條 + 背景微染呈現
- 全程沿用既有 Editorial Dark 色票與字體系統,不引入新色或新字
- Monitor 在頁面切換時不卸載,WS 連線與 watchlist 狀態保持

## Non-goals

- 微型台指當沖回測的回測引擎本身(後續另一輪 spec)
- URL routing / 深層連結(目前無需求,日後若需要再升級到 react-router)
- 頁面狀態持久化(reload 後預設回 Monitor 即可)
- Custom tooltip 元件 — 收合時的 affordance 用瀏覽器原生 `title`

## Architecture

```
frontend/src/
├── App.tsx                              [改] 包 Layout、page 切換 state、always-mount
├── components/
│   └── Sidebar.tsx                      [新] ~120 行
├── pages/
│   ├── Monitor.tsx                      [不動]
│   └── MicroFutBacktest.tsx             [新] ~30 行 placeholder
└── hooks/
    └── useSidebarState.ts               [新] ~20 行,localStorage 持久化
```

**Layout 結構**:

```tsx
<div className="h-screen flex">
  <Sidebar current={page} onNavigate={setPage} />
  <div className="flex-1 min-w-0 overflow-hidden relative">
    <div hidden={page !== 'monitor'} className="h-full"><Monitor /></div>
    <div hidden={page !== 'backtest'} className="h-full"><MicroFutBacktest /></div>
  </div>
</div>
```

**為什麼用 `hidden` 不用三元**:Monitor 內部有 WebSocket 連線、watchlist、active signals stream 等 hooks。切到 backtest 再切回會重連、重抓,造成數百 ms 延遲與閃爍。`hidden` 讓 Monitor 永遠 mount,切換 0ms。

## Sidebar 元件規格

### 尺寸與容器

| 屬性 | 值 |
|---|---|
| 寬:展開 | `220px` |
| 寬:收合 | `56px` |
| 高 | `100vh` |
| 背景 | `bg-bg-deep`(`#0d0a07`,比主底深一階) |
| 右邊框 | `border-r border-line`(`#2e2a22`) |
| Width transition | `transition-[width] duration-200 ease-out` |
| Color transitions(hover) | `transition-colors duration-150 ease-out`(套在漢堡 + nav button) |

### 漢堡按鈕區塊

- 位置:sidebar 頂部獨立區塊
- Padding:展開 `p-[18px]`,收合 `py-[18px] flex justify-center`
- 下邊框:`border-b border-line`
- Icon:inline SVG,16×16,三條 1.5px 線
- Icon 色:default `text-ink-muted` (`#d4c8b0`),hover `text-ink` (`#ede4d3`)
- `aria-label="切換選單"`、`aria-expanded={expanded}`
- 點擊 → toggle expanded → 觸發 useEffect 寫入 localStorage

### Nav items

兩項:
1. **即時監控** — id: `'monitor'`
2. **微型台指當沖回測** — id: `'backtest'`

每項:

```tsx
<button
  type="button"
  onClick={() => onNavigate(item.id)}
  title={item.label}        // 收合時的原生 tooltip
  aria-current={isActive ? 'page' : undefined}
  className="flex items-center gap-3 ..."
>
  <Icon className="w-4 h-4 stroke-[1.5]" />
  <span className={expanded ? '' : 'sr-only'}>{item.label}</span>
</button>
```

**Padding**:展開 `px-4 py-2.5`,收合 `px-0 py-3 justify-center`

**狀態色**:

| State | Icon | Label | 背景 | 左邊條 |
|---|---|---|---|---|
| Default | `text-ink-dim` (`#8a8273`) | `text-ink-muted` (`#d4c8b0`) | 透明 | — |
| Hover | `text-ink-muted` | `text-ink` (`#ede4d3`) | `bg-white/[0.02]` | — |
| Active | `text-accent` (`#e85a4f`) | `text-ink` | `bg-accent/[0.05]` | 2px accent(`::before`) |

### Icon SVG paths

兩個都用 `viewBox="0 0 24 24"`、`fill="none" stroke="currentColor" stroke-width="1.5"`。

| Item | SVG path |
|---|---|
| 即時監控 | `M3 12h3l3-8 4 16 3-10 2 6h3` — 心電圖波形 |
| 回測 | `M3 3v18h18 M7 14l4-4 3 3 5-7` — 軸線 + 上升趨勢 |

### 無障礙

- 漢堡:`<button>` + `aria-label` + `aria-expanded`
- Nav:`<nav>` 包,內部 `<button>` 陣列(不用 `<a>`,沒換 URL)
- Active item:`aria-current="page"`
- 鍵盤:Tab 走得到,Enter / Space 觸發
- 收合時 label 給 `sr-only` 而非 `display:none`,讓 screen reader 仍能讀到

## State & Persistence

### `useSidebarState.ts`

```ts
import { useEffect, useState } from 'react';

const KEY = 'sidebar:expanded';

export function useSidebarState() {
  const [expanded, setExpanded] = useState<boolean>(() => {
    const raw = localStorage.getItem(KEY);
    return raw === null ? true : raw === '1';   // 預設展開
  });

  useEffect(() => {
    localStorage.setItem(KEY, expanded ? '1' : '0');
  }, [expanded]);

  return [expanded, setExpanded] as const;
}
```

### App.tsx 頁面 state

```tsx
type Page = 'monitor' | 'backtest';
const [page, setPage] = useState<Page>('monitor');
```

不持久化 — reload 預設回 Monitor。等回測有實際功能再考慮持久化。

## `MicroFutBacktest.tsx` placeholder 內容

```tsx
export function MicroFutBacktest() {
  return (
    <div className="h-full flex flex-col items-center justify-center px-8">
      <span className="label-tiny mb-3">Module · Upcoming</span>
      <h1 className="h-display text-4xl text-ink mb-4">微型台指當沖回測</h1>
      <p className="text-ink-muted max-w-md text-center leading-relaxed">
        策略回測引擎開發中。<br />
        日內 1 分 / 5 分 K、bid/ask 推方向、規則編輯器、績效報表 — 後續實作。
      </p>
      <div className="mt-12 label-small">待規劃</div>
    </div>
  );
}
```

完全沿用既有 `.label-tiny` / `.h-display` / `.label-small` utility classes。

## Verification(手動)

跑 `npm run dev` 後在 http://localhost:5173 驗收:

1. ✅ Sidebar 預設展開,Monitor 顯示正常
2. ✅ 點漢堡 → 寬度動畫順,Monitor 內容區自動長出
3. ✅ 重整頁面 → 收合狀態保留
4. ✅ 點「微型台指當沖回測」item → 切到 placeholder,active 指示移動
5. ✅ 切回「即時監控」→ Monitor 立即顯示,WS 連線、自選股、active signals 全部維持(因 always-mount)
   - ⚠️ 另查:`IntradayChart` 在 `display:none` 期間 ResizeObserver 不會觸發。切回時若圖表沒自動重 fit,需在 Monitor 切回時手動觸發 chart 的 resize(`api.timeScale().fitContent()` 或 `api.resize()`)。實作時若觀察到問題再處理。
6. ✅ 收合時 hover icon → 瀏覽器跳原生 tooltip 顯示 label
7. ✅ Tab 鍵能走到漢堡 + 兩個 nav item,Enter 觸發
8. ✅ 既有 Monitor 4 欄 grid 在 sidebar 切換時不變形(`flex-1 min-w-0`)

不寫 Vitest — 這是 UI shell,自動化測試對視覺驗收幫助有限。

## Out of scope(後續可能擴充)

- 微型台指當沖回測的回測引擎、策略編輯、績效報表
- URL routing 與深層連結
- 第三、第四個 nav 項目
- 自訂 tooltip(若原生 `title` UX 不夠好)
- Mobile breakpoint(目前桌面 only)

## Brainstorming artifacts

設計過程的視覺 mockup 保留在:
- `.superpowers/brainstorm/1729-1778816294/content/sidebar-collapse.html` — 收合行為三選,選定 A(Icon rail)
- `.superpowers/brainstorm/1729-1778816294/content/item-style.html` — Item 樣式三選,選定 V2(Line icons)
