import type { WidgetActionHandler, WidgetRegistry } from "./core";
import { defaultWidgetRegistry } from "./renderers";
import { type Widget, widgetSchema } from "./schema";

export interface WidgetViewProps {
  id: string;
  type: string;
  props: unknown;
  onAction: (id: string, action: string, value?: string) => void;
  registry?: WidgetRegistry;
}

/** Renders one widget from the registry; anything that fails the schema is dropped. */
export function WidgetView({ id, type, props, onAction, registry = defaultWidgetRegistry }: WidgetViewProps) {
  const parsed = widgetSchema.safeParse({ type, props });
  if (!parsed.success) return null;
  const handler: WidgetActionHandler = (action, value) => onAction(id, action, value);
  return renderWidget(parsed.data, id, handler, registry);
}

function renderWidget(widget: Widget, id: string, onAction: WidgetActionHandler, registry: WidgetRegistry) {
  switch (widget.type) {
    case "card":
      return <registry.card id={id} props={widget.props} onAction={onAction} />;
    case "options":
      return <registry.options id={id} props={widget.props} onAction={onAction} />;
    case "chart":
      return <registry.chart id={id} props={widget.props} onAction={onAction} />;
    case "html":
      return <registry.html id={id} props={widget.props} onAction={onAction} />;
  }
}
