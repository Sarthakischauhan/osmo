import type { ChartProps } from "./schema";

/** Line/bar chart. chat-sdk has no chart widget, so this stays local to Osmo. */
const PALETTE = ["#a5b4fc", "#6ee7b7", "#fca5a5", "#fcd34d", "#c4b5fd"];
const W = 440;
const H = 160;
const PAD = 8;

export function ChartWidget({ kind, series }: ChartProps) {
  const values = series.flatMap((s) => s.points.map((p) => p.y));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const longest = Math.max(...series.map((s) => s.points.length));
  const x = (i: number) => PAD + (longest <= 1 ? 0 : (i / (longest - 1)) * (W - PAD * 2));
  const y = (v: number) => H - PAD - ((v - min) / span) * (H - PAD * 2);
  const first = series[0];
  const lastPoint = first?.points[first.points.length - 1];
  const firstPoint = first?.points[0];
  const change = firstPoint && lastPoint ? ((lastPoint.y - firstPoint.y) / firstPoint.y) * 100 : 0;

  return (
    <section className="osmo-chart">
      <header className="osmo-chart-head">
        {series.map((s, i) => (
          <span key={s.name} className="osmo-legend">
            <i style={{ background: PALETTE[i % PALETTE.length] }} />
            {s.name}
          </span>
        ))}
        {lastPoint && (
          <span className="osmo-chart-last">
            {lastPoint.y.toLocaleString()}{" "}
            <em className={change >= 0 ? "up" : "down"}>
              {change >= 0 ? "+" : ""}
              {change.toFixed(1)}%
            </em>
          </span>
        )}
      </header>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={`${kind} chart`}>
        {series.map((s, si) => {
          const color = PALETTE[si % PALETTE.length];
          if (kind === "bar") {
            const band = (W - PAD * 2) / longest / series.length;
            return s.points.map((p, i) => (
              <rect
                key={`${si}-${i}`}
                x={PAD + i * band * series.length + si * band}
                y={y(p.y)}
                width={Math.max(1, band - 2)}
                height={H - PAD - y(p.y)}
                fill={color}
                rx={2}
              />
            ));
          }
          const d = s.points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.y).toFixed(1)}`).join(" ");
          return <path key={si} d={d} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />;
        })}
      </svg>
      {first && (
        <footer className="osmo-chart-axis">
          <span>{first.points[0]?.x}</span>
          <span>{first.points[first.points.length - 1]?.x}</span>
        </footer>
      )}
    </section>
  );
}
