// 張數快捷:快捷鈕單點=填入、再點同一顆=累加、點不同顆或手動輸入=重置累加鏈。
export const QTY_PRESETS = [1, 3, 5, 10] as const;

export interface QtyState {
  qty: number;
  lastPreset: number | null; // 上一次按的快捷值;手動輸入後為 null(下次點快捷=填入)
}

export function initialQtyState(qty = 1): QtyState {
  return { qty: Math.max(1, qty), lastPreset: null };
}

export function pressQuick(s: QtyState, preset: number): QtyState {
  if (s.lastPreset === preset) return { qty: s.qty + preset, lastPreset: preset };
  return { qty: preset, lastPreset: preset };
}

export function manualQty(s: QtyState, qty: number): QtyState {
  return { qty: Math.max(1, Math.floor(qty)), lastPreset: null };
}
