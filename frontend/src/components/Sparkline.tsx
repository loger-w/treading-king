interface Props {
  values: number[];
  width?: number;
  height?: number;
}

export function Sparkline({ values, width = 80, height = 24 }: Props) {
  if (values.length < 2) return <span className="text-ink-dim">—</span>;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const dx = width / (values.length - 1);
  const points = values
    .map((v, i) => `${(i * dx).toFixed(1)},${(height - ((v - min) / range) * height).toFixed(1)}`)
    .join(" ");
  const up = values[values.length - 1] >= values[0];
  const stroke = up ? "var(--color-bull, #e85a4f)" : "var(--color-bear, #7fc99a)";
  return (
    <svg width={width} height={height} aria-hidden>
      <polyline points={points} fill="none" stroke={stroke} strokeWidth="1.5" />
    </svg>
  );
}
