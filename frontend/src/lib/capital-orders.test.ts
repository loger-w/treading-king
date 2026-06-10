import { describe, expect, it } from "vitest";
import { buildOrderRow, type CapitalOrder } from "./capital-orders";

const base: CapitalOrder = {
  seq_no: "2313091595225", stock_no: "3357", name: "臺慶科", market: "TS",
  buy_sell: "B", flag_label: "現股", book_no: null,
  status_raw: "C", status_label: "已刪單",
  price: 293, avg_fill_price: null, order_qty: 1, filled_qty: 0, unit: "張",
  time: "14:59:48", pre_order: true, error_msg: null, actionable: false, raw: "",
};

describe("buildOrderRow", () => {
  it("組標題與買賣樣式", () => {
    const r = buildOrderRow(base);
    expect(r.title).toBe("3357 臺慶科");
    expect(r.sideLabel).toBe("買");
    expect(r.sideClass).toBe("text-bull");
    expect(r.flagLabel).toBe("現股");
    expect(r.preOrder).toBe(true);
  });

  it("名稱缺值只顯代號", () => {
    const r = buildOrderRow({ ...base, name: "" });
    expect(r.title).toBe("3357");
  });

  it("賣單綠色", () => {
    const r = buildOrderRow({ ...base, buy_sell: "S" });
    expect(r.sideLabel).toBe("賣");
    expect(r.sideClass).toBe("text-bear");
  });

  it("量文字 = 成交/委託 + 單位;均價有成交才出現", () => {
    const r = buildOrderRow({ ...base, status_label: "部分成交", order_qty: 4, filled_qty: 3, avg_fill_price: 83.65 });
    expect(r.qtyText).toBe("3/4 張");
    expect(r.avgText).toBe("均 83.65");
    expect(buildOrderRow(base).avgText).toBeNull();
  });

  it("活單可刪改由後端決定,前端只透傳", () => {
    // 哪些狀態算活單歸後端 _RANK 單一擁有(test_capital_store 蓋);前端抄一份狀態表
    // 的話,後端改 label 字樣會讓刪/改鈕無聲消失
    expect(buildOrderRow({ ...base, actionable: true }).actionable).toBe(true);
    expect(buildOrderRow({ ...base, actionable: false }).actionable).toBe(false);
  });

  it("單位透傳(零股是股,不可寫死張)", () => {
    const r = buildOrderRow({ ...base, market: "TC", unit: "股", order_qty: 500, filled_qty: 0 });
    expect(r.unit).toBe("股");
    expect(r.qtyText).toBe("0/500 股");
  });

  it("失敗紅字+錯誤訊息", () => {
    const r = buildOrderRow({ ...base, status_label: "失敗", error_msg: "超過漲跌停" });
    expect(r.statusClass).toBe("text-bear");
    expect(r.errorMsg).toBe("超過漲跌停");
  });
});
