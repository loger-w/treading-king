// 委託列 view-model:把後端聚合的 CapitalOrder 轉成可渲染欄位。純函式,可測。
export interface CapitalOrder {
  seq_no: string; stock_no: string | null; name: string; market: string | null;
  buy_sell: string | null; flag_label: string | null; book_no: string | null;
  status_raw: string | null; status_label: string | null;
  price: number | null; avg_fill_price: number | null;
  order_qty: number; filled_qty: number; unit: string;
  time: string | null; pre_order: boolean; error_msg: string | null;
  actionable: boolean;   // 後端 _RANK 算好下發;前端不自己抄狀態表(label 改字不會讓鈕無聲消失)
  raw: string;
}

export interface OrderRowVM {
  seqNo: string;
  title: string;            // "3357 臺慶科"(無名稱時只代號)
  sideLabel: string;        // 買/賣/—
  sideClass: string;        // text-bull / text-bear / text-ink-dim
  flagLabel: string | null; // 現股/融資/融券…
  statusLabel: string;
  statusClass: string;      // 失敗類紅字
  priceText: string;        // 委託價
  qtyText: string;          // "3/4 張"
  avgText: string | null;   // "均 83.65"(有成交才有)
  timeText: string | null;
  preOrder: boolean;
  errorMsg: string | null;
  unit: string;             // 張/股/口 — 減量輸入與確認文案用,不可寫死「張」(零股單位是股)
  actionable: boolean;      // 活單可刪/改(後端決定)
}

const FAILED = new Set(["失敗", "逾時", "退單"]);   // 純顯示:紅字樣式

export function buildOrderRow(o: CapitalOrder): OrderRowVM {
  const title = o.name ? `${o.stock_no ?? ""} ${o.name}`.trim() : (o.stock_no ?? "—");
  const isBuy = o.buy_sell === "B";
  const isSell = o.buy_sell === "S";
  const status = o.status_label ?? "—";
  return {
    seqNo: o.seq_no,
    title,
    sideLabel: isBuy ? "買" : isSell ? "賣" : "—",
    sideClass: isBuy ? "text-bull" : isSell ? "text-bear" : "text-ink-dim",
    flagLabel: o.flag_label,
    statusLabel: status,
    statusClass: FAILED.has(status) ? "text-bear" : "text-ink-muted",
    priceText: o.price != null ? o.price.toFixed(2) : "—",
    qtyText: `${o.filled_qty}/${o.order_qty} ${o.unit}`,
    avgText: o.avg_fill_price != null && o.filled_qty > 0 ? `均 ${o.avg_fill_price.toFixed(2)}` : null,
    timeText: o.time,
    preOrder: o.pre_order,
    errorMsg: o.error_msg,
    unit: o.unit,
    actionable: o.actionable,
  };
}
