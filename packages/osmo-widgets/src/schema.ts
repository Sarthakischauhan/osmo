import { z } from "zod";

/**
 * Prop schemas for the five widget types. Symphony validates every
 * render_widget call against these before anything reaches the canvas,
 * and the registry re-checks them on the client.
 */

export const widgetActionSchema = z.object({
  id: z.string().min(1).max(64),
  label: z.string().min(1).max(80),
});

/** A card image: an http(s) link, or a picture create_image drew, inlined as a data: URL (same rule as Symphony). */
export const cardImageSchema = z.union([
  z.string().url().regex(/^https?:\/\//),
  z.string().regex(/^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/]+={0,2}$/),
]);

export const cardPropsSchema = z
  .object({
    title: z.string().min(1).max(120),
    body: z.string().max(4000),
    image: cardImageSchema.optional(),
    art: z.enum(["sun", "moon", "cloud", "rain", "snow", "storm", "wind", "stars"]).optional(),
    actions: z.array(widgetActionSchema).max(4).optional(),
  })
  .strict();

export const optionsPropsSchema = z
  .object({
    prompt: z.string().min(1).max(200),
    choices: z
      .array(
        z
          .object({
            id: z.string().min(1).max(64),
            label: z.string().min(1).max(120),
            detail: z.string().max(60).optional(),
          })
          .strict(),
      )
      .min(1)
      .max(8),
  })
  .strict();

export const chartPropsSchema = z
  .object({
    kind: z.enum(["line", "bar"]),
    series: z
      .array(
        z
          .object({
            name: z.string().min(1).max(60),
            points: z
              .array(z.object({ x: z.string().max(40), y: z.number().finite() }).strict())
              .min(1)
              .max(400),
          })
          .strict(),
      )
      .min(1)
      .max(5),
  })
  .strict();

export const htmlPropsSchema = z
  .object({
    html: z.string().min(1).max(100_000),
    title: z.string().min(1).max(80).optional(),
  })
  .strict();

/** The agent browser: the newest screencast frame (a data: URL, as card images) and the page it shows. */
export const browserPropsSchema = z
  .object({
    status: z.enum(["live", "ended"]),
    url: z.string().optional(),
    frame: z.string().regex(/^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/]+={0,2}$/).optional(),
  })
  .strict();

export const widgetSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("card"), props: cardPropsSchema }),
  z.object({ type: z.literal("options"), props: optionsPropsSchema }),
  z.object({ type: z.literal("chart"), props: chartPropsSchema }),
  z.object({ type: z.literal("html"), props: htmlPropsSchema }),
  z.object({ type: z.literal("browser"), props: browserPropsSchema }),
]);

export type Widget = z.infer<typeof widgetSchema>;
export type WidgetType = Widget["type"];
export type CardProps = z.infer<typeof cardPropsSchema>;
export type OptionsProps = z.infer<typeof optionsPropsSchema>;
export type ChartProps = z.infer<typeof chartPropsSchema>;
export type HtmlProps = z.infer<typeof htmlPropsSchema>;
export type BrowserProps = z.infer<typeof browserPropsSchema>;

export const WIDGET_TYPES: readonly WidgetType[] = ["card", "options", "chart", "html", "browser"];

/** One-line description of a validated widget, returned to the model as `shown`. */
export function describeWidget(widget: Widget): string {
  switch (widget.type) {
    case "card":
      return `card "${widget.props.title}"${widget.props.actions ? ` with ${widget.props.actions.length} buttons` : ""}`;
    case "options":
      return `options "${widget.props.prompt}", ${widget.props.choices.length} choices: ${widget.props.choices
        .map((c, i) => `${i + 1}. ${c.label}`)
        .join("; ")}`;
    case "chart": {
      const first = widget.props.series[0];
      return `${widget.props.kind} chart, ${widget.props.series.map((s) => s.name).join(", ")}${
        first ? `, ${first.points.length} points` : ""
      }`;
    }
    case "browser":
      return "agent browser";
    case "html":
      return widget.props.title ? `interactive html widget "${widget.props.title}"` : "interactive html widget";
  }
}

/** True when a widget is waiting on the user to choose: any options list, or a card with buttons. */
export function isActionableWidget(type: string, props: unknown): boolean {
  const parsed = widgetSchema.safeParse({ type, props });
  if (!parsed.success) return false;
  if (parsed.data.type === "options") return true;
  return parsed.data.type === "card" && (parsed.data.props.actions?.length ?? 0) > 0;
}
