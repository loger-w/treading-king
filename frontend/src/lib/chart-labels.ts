export interface LabelInput {
  originalY: number;
  text: string;
  color: string;
}

export interface LabelOutput extends LabelInput {
  y: number;
}

/**
 * 把一組落在不同 y 的 label 撐開到彼此距離 >= minGap。
 *
 * Pass 1:依 originalY 升序,從上往下推 — y[i] = max(y[i], y[i-1] + minGap)
 * Pass 2:若 y[last] > yRange[1],從下往上回彈 — y[i] = min(y[i], y[i+1] - minGap)
 *
 * 回傳陣列保證按 y 升序;對 input 順序不敏感(內部會排)。
 */
export function resolveCollisions(
  items: LabelInput[],
  minGap: number,
  yRange: [number, number],
): LabelOutput[] {
  if (items.length === 0) return [];

  const sorted = [...items]
    .sort((a, b) => a.originalY - b.originalY)
    .map((it) => ({ ...it, y: it.originalY }));

  for (let i = 1; i < sorted.length; i++) {
    const minY = sorted[i - 1].y + minGap;
    if (sorted[i].y < minY) sorted[i].y = minY;
  }

  const last = sorted[sorted.length - 1].y;
  if (last > yRange[1]) {
    sorted[sorted.length - 1].y = yRange[1];
    for (let i = sorted.length - 2; i >= 0; i--) {
      const maxY = sorted[i + 1].y - minGap;
      if (sorted[i].y > maxY) sorted[i].y = maxY;
    }
  }

  return sorted;
}
