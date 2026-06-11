// 閃電階梯純函式:吃 現價/平盤價/五檔/我的活單 → 回列陣列(高價在前)。
// 元件只負責渲染 — 對齊「無 hook 測試環境就抽 lib」慣例。
import { limitDown, limitUp, roundToTick, tickSize } from "./tick";

export interface MyOrderLot {
  price: number;
  buySell: "B" | "S";
  lots: number; // active=未成交張數、fills=成交張數(看用在哪個輸入)
}

// CapitalOrder 的結構子集 — lib 不 import api 型別,保持零依賴可測
export interface MyOrderSource {
  price: number | null;
  buy_sell: string | null;
  order_qty: number;   // 顯示單位(張)
  filled_qty: number;
  actionable: boolean;
}

/** 該檔今日全部委託 → 方格(active=活單未成交)與黃括號(fills=成交紀錄,含已完成單)。
 *  成交歸屬委託價(券商慣例);市價單無價不上階梯。 */
export function splitMyLots(orders: MyOrderSource[]): { active: MyOrderLot[]; fills: MyOrderLot[] } {
  const active: MyOrderLot[] = [];
  const fills: MyOrderLot[] = [];
  for (const o of orders) {
    if (o.price == null || (o.buy_sell !== "B" && o.buy_sell !== "S")) continue;
    const side = o.buy_sell;
    const remaining = o.order_qty - o.filled_qty;
    if (o.actionable && remaining > 0) active.push({ price: o.price, buySell: side, lots: remaining });
    if (o.filled_qty > 0) fills.push({ price: o.price, buySell: side, lots: o.filled_qty });
  }
  return { active, fills };
}

export interface LadderRow {
  price: number;
  buyVol: number | null;   // 該價位委買量(五檔範圍外 = null)
  sellVol: number | null;
  myBuyLots: number;       // 我在該價位的活單張數
  mySellLots: number;
  myBuyFills: number;      // 我今日在該價位的成交張數(黃括號;含已完成單)
  mySellFills: number;
  isCenter: boolean;
  clickable: boolean;      // 離現價 ±5% 內才可點(fat-finger 灰區)
}

const CLICK_BAND = 0.05;

function stepUp(price: number): number {
  // 下一檔向上 = 加當前價位的 tick;跨級距時對新價位重新對齊
  return roundToTick(Math.round((price + tickSize(price)) * 100) / 100, "down");
}
function stepDown(price: number): number {
  // 50.0/100.0 往下一檔要用低一級距的 tick(100→99.9 而非 99.5);
  // tickSize 是純查表,輕推值不可先 round 到分(0.001 會被吃掉)
  const t = tickSize(price - 0.001);
  return roundToTick(Math.round((price - t) * 100) / 100, "up");
}

/** 各價位張數按買賣邊聚合 — 活單與成交紀錄同構,共用一份累加邏輯 */
function aggregateLotsBySide(lots: MyOrderLot[]): { B: Map<number, number>; S: Map<number, number> } {
  const B = new Map<number, number>();
  const S = new Map<number, number>();
  for (const o of lots) {
    const m = o.buySell === "B" ? B : S;
    m.set(o.price, (m.get(o.price) ?? 0) + o.lots);
  }
  return { B, S };
}

export function buildLadder(opts: {
  center: number;
  reference: number | null;
  bids: Array<{ price: number; size: number }>;
  asks: Array<{ price: number; size: number }>;
  myOrders: MyOrderLot[];
  myFills?: MyOrderLot[];
  rows?: number;
}): LadderRow[] {
  const { center, reference, bids, asks, myOrders } = opts;
  // 預設 60:100 元股向下 tick=0.1,30 檔只蓋 ±3%、蓋不滿 ±5% 可點區;
  // 60 檔在各價位帶都 ≥5%,過多的部分由漲跌停夾界自然截斷
  const half = opts.rows ?? 60;
  const ref = reference ?? center; // 缺平盤價 → 以現價估(面板標「估」)
  const up = limitUp(ref);
  const down = limitDown(ref);

  const c = roundToTick(center, "down");
  const prices: number[] = [c];
  let p = c;
  for (let i = 0; i < half && p < up; i++) {
    p = stepUp(p);
    if (p <= up) prices.unshift(p);
  }
  p = c;
  for (let i = 0; i < half && p > down; i++) {
    p = stepDown(p);
    if (p >= down) prices.push(p);
  }

  const bidMap = new Map(bids.map((b) => [b.price, b.size]));
  const askMap = new Map(asks.map((a) => [a.price, a.size]));
  const my = aggregateLotsBySide(myOrders);
  const fills = aggregateLotsBySide(opts.myFills ?? []);

  return prices.map((price) => ({
    price,
    buyVol: bidMap.get(price) ?? null,
    sellVol: askMap.get(price) ?? null,
    myBuyLots: my.B.get(price) ?? 0,
    mySellLots: my.S.get(price) ?? 0,
    myBuyFills: fills.B.get(price) ?? 0,
    mySellFills: fills.S.get(price) ?? 0,
    isCenter: price === c,
    clickable: Math.abs(price - center) / center <= CLICK_BAND + 1e-9,
  }));
}
