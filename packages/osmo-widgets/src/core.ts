import type { ComponentType } from "react";
import type { Widget, WidgetType } from "./schema";

/**
 * The slice of chat SDK core the widget renderers rely on: a renderer gets
 * validated props plus a callback that reports a tap back to the agent.
 */
export type WidgetActionHandler = (action: string, value?: string) => void;

export type WidgetRendererProps<T extends WidgetType> = {
  id: string;
  props: Extract<Widget, { type: T }>["props"];
  onAction: WidgetActionHandler;
};

export type WidgetRegistry = { [T in WidgetType]: ComponentType<WidgetRendererProps<T>> };
