import type { WidgetRegistry, WidgetRendererProps } from "./core";
import { Markdown } from "./markdown";

export function CardWidget({ props, onAction }: WidgetRendererProps<"card">) {
  return (
    <section className="w-card">
      {props.image && <img className="w-card-image" src={props.image} alt="" />}
      <h3 className="w-title">{props.title}</h3>
      <div className="w-body">
        <Markdown source={props.body} />
      </div>
      {props.actions && (
        <div className="w-actions">
          {props.actions.map((action) => (
            <button key={action.id} type="button" className="w-button" onClick={() => onAction("press", action.id)}>
              {action.label}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

export function OptionsWidget({ props, onAction }: WidgetRendererProps<"options">) {
  return (
    <section className="w-card w-options">
      <header className="w-options-head">
        <h3 className="w-title">{props.prompt}</h3>
        <span className="w-hint">say it or tap</span>
      </header>
      <ol className="w-choices">
        {props.choices.map((choice, i) => (
          <li key={choice.id}>
            <button type="button" className="w-choice" onClick={() => onAction("select", choice.id)}>
              <span className="w-choice-index">{i + 1}</span>
              <span className="w-choice-label">{choice.label}</span>
              {choice.detail && <span className="w-choice-detail">{choice.detail}</span>}
            </button>
          </li>
        ))}
      </ol>
    </section>
  );
}

const PALETTE = ["#a5b4fc", "#6ee7b7", "#fca5a5", "#fcd34d", "#c4b5fd"];
const W = 440;
const H = 160;
const PAD = 8;

export function ChartWidget({ props }: WidgetRendererProps<"chart">) {
  const values = props.series.flatMap((s) => s.points.map((p) => p.y));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const longest = Math.max(...props.series.map((s) => s.points.length));
  const x = (i: number) => PAD + (longest <= 1 ? 0 : (i / (longest - 1)) * (W - PAD * 2));
  const y = (v: number) => H - PAD - ((v - min) / span) * (H - PAD * 2);
  const first = props.series[0];
  const lastPoint = first?.points[first.points.length - 1];
  const firstPoint = first?.points[0];
  const change = firstPoint && lastPoint ? ((lastPoint.y - firstPoint.y) / firstPoint.y) * 100 : 0;

  return (
    <section className="w-card w-chart">
      <header className="w-chart-head">
        {props.series.map((s, i) => (
          <span key={s.name} className="w-legend">
            <i style={{ background: PALETTE[i % PALETTE.length] }} />
            {s.name}
          </span>
        ))}
        {lastPoint && (
          <span className="w-chart-last">
            {lastPoint.y.toLocaleString()} <em className={change >= 0 ? "up" : "down"}>{change >= 0 ? "+" : ""}{change.toFixed(1)}%</em>
          </span>
        )}
      </header>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={`${props.kind} chart`}>
        {props.series.map((s, si) => {
          const color = PALETTE[si % PALETTE.length];
          if (props.kind === "bar") {
            const band = (W - PAD * 2) / longest / props.series.length;
            return s.points.map((p, i) => (
              <rect
                key={`${si}-${i}`}
                x={PAD + i * band * props.series.length + si * band}
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
        <footer className="w-chart-axis">
          <span>{first.points[0]?.x}</span>
          <span>{first.points[first.points.length - 1]?.x}</span>
        </footer>
      )}
    </section>
  );
}

/** Generated HTML runs in an opaque-origin iframe: scripts yes, same-origin access no. */
export function HtmlWidget({ props }: WidgetRendererProps<"html">) {
  return (
    <section className="w-card w-html">
      <iframe title="Generated widget" sandbox="allow-scripts allow-forms" srcDoc={props.html} referrerPolicy="no-referrer" />
    </section>
  );
}

export const defaultWidgetRegistry: WidgetRegistry = {
  card: CardWidget,
  options: OptionsWidget,
  chart: ChartWidget,
  html: HtmlWidget,
};
