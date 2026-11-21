import type { AgendaProps } from "./schema";

/** The Today card from GUI's calendar-widget mockup: a time column, the title, and a done / now tag per row. */
export function AgendaWidget({ title, subtitle, items, footer }: AgendaProps) {
  return (
    <section className="osmo-agenda" aria-label={title}>
      <h2>{title}</h2>
      {subtitle && subtitle !== title && <p className="osmo-agenda-sub">{subtitle}</p>}
      {items.length === 0 ? (
        <p className="osmo-agenda-empty">Nothing on the calendar.</p>
      ) : (
        <ul>
          {items.map((item, i) => (
            <li key={`${item.time}-${i}`} data-state={item.state}>
              <span className="osmo-agenda-time">{item.time}</span>
              <span className="osmo-agenda-title">{item.title}</span>
              <span className="osmo-agenda-tag">
                {item.state === "now" && <i aria-hidden />}
                {item.tag}
              </span>
            </li>
          ))}
        </ul>
      )}
      {footer && (
        <div className="osmo-agenda-next">
          <span className="osmo-agenda-label">{footer.label}</span>
          <span>
            {footer.value} {footer.detail && <span className="osmo-agenda-detail">{footer.detail}</span>}
          </span>
        </div>
      )}
    </section>
  );
}
