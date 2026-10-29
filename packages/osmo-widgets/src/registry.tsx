import { MessageContent, QuestionWidget, defineWidget, type WidgetComponentProps } from "@sarchauhan/chat";
import type { AgentWidgetPart } from "@sarchauhan/protocol";
import { useContext } from "react";
import { AsciiArt } from "./asciiArt";
import { BrowserWidget } from "./browser";
import { BrowserControlsContext } from "./controls";
import { ChartWidget } from "./chart";
import { HtmlWidget } from "./html";
import { browserPropsSchema, cardPropsSchema, chartPropsSchema, htmlPropsSchema, optionsPropsSchema, widgetSchema } from "./schema";

/** Action ids reported back as `widget.action`. */
export const CARD_PRESS = "press";
export const OPTION_SELECT = "select";

// chat-sdk hands every widget an untyped props bag; each renderer re-parses it with Osmo's schema.

function CardBody({ widget, ...raw }: WidgetComponentProps) {
  const parsed = cardPropsSchema.safeParse(raw);
  if (!parsed.success) return null;
  const { body, image, actions, art } = parsed.data;
  return (
    <>
      {art && !image && <AsciiArt name={art} className="osmo-card-art" label={art} />}
      {image && <img className="osmo-card-image" src={image} alt="" />}
      <MessageContent parts={[{ type: "text", text: body }]} />
      {actions && (
        <div className="osmo-card-actions">
          {actions.map((action) => (
            <button
              key={action.id}
              type="button"
              disabled={widget.disabled}
              onClick={() => void widget.respond(action.id, action.label, CARD_PRESS)}
            >
              {action.label}
            </button>
          ))}
        </div>
      )}
    </>
  );
}

function Options({ widget, ...raw }: WidgetComponentProps) {
  const parsed = optionsPropsSchema.safeParse(raw);
  if (!parsed.success) return null;
  const { prompt, choices } = parsed.data;
  return (
    <QuestionWidget
      prompt={prompt}
      description="Say it or tap"
      options={choices.map((choice) => ({ label: choice.label, value: choice.id, description: choice.detail }))}
      disabled={widget.disabled}
      onSubmit={async (answer) => {
        const id = answer.values[0];
        const choice = choices.find((c) => c.id === id);
        if (choice) await widget.respond(choice.id, choice.label, OPTION_SELECT);
      }}
    />
  );
}

function Chart({ widget: _widget, ...raw }: WidgetComponentProps) {
  const parsed = chartPropsSchema.safeParse(raw);
  return parsed.success ? <ChartWidget {...parsed.data} /> : null;
}

function Html({ widget: _widget, ...raw }: WidgetComponentProps) {
  const parsed = htmlPropsSchema.safeParse(raw);
  return parsed.success ? <HtmlWidget {...parsed.data} /> : null;
}

function Browser({ widget, ...raw }: WidgetComponentProps) {
  const parsed = browserPropsSchema.safeParse(raw);
  const controls = useContext(BrowserControlsContext);
  if (!parsed.success) return null;
  const id = widget.widgetId;
  if (controls === undefined || id === undefined || widget.disabled) return <BrowserWidget {...parsed.data} />;
  return <BrowserWidget {...parsed.data} onInput={(input) => controls.input(id, input)} onClose={() => controls.close(id)} />;
}

/**
 * Osmo's widget registry for chat-sdk's WidgetProvider. card and options are
 * chat-sdk components (BaseWidget, MessageContent, QuestionWidget); chart and
 * html are the Osmo-only pieces chat-sdk does not ship.
 */
export const osmoWidgets = [
  defineWidget("card", CardBody, {
    label: "",
    title: (props) => cardPropsSchema.safeParse(props).data?.title ?? "",
    className: "osmo-card",
  }),
  defineWidget("options", Options, { shell: false }),
  defineWidget("chart", Chart, { shell: false }),
  defineWidget("html", Html, { shell: false }),
  defineWidget("browser", Browser, { shell: false }),
];

/** Validates a `widget.render` payload and turns it into a chat-sdk widget part; invalid widgets are dropped. */
export function toWidgetPart(id: string, type: string, props: unknown): AgentWidgetPart | null {
  const parsed = widgetSchema.safeParse({ type, props });
  if (!parsed.success) return null;
  return {
    type: "widget",
    id,
    name: parsed.data.type,
    props: parsed.data.props,
    interactive: parsed.data.type === "options" || parsed.data.type === "card",
  };
}
