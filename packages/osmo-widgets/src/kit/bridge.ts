import { z } from "zod";

export type KitTheme = "light" | "dark";

/**
 * The html widget's click bridge. The frame gets `osmo.action(id, value?)`, and any element with
 * `data-osmo-action="<id>"` (optionally `data-osmo-value`) calls it on click; a form with the
 * attribute sends its fields as JSON on submit. The message goes to the parent, which accepts it
 * only from this frame's window and only for an id the widget declared in `actions`.
 * The parent sets the frame's starting theme and sends changes as `{osmo: "theme"}`, checked against `parent`.
 */
export function bridgeScript(theme: KitTheme): string {
  return `(() => {
  document.documentElement.dataset.theme = "${theme}";
  const send = (id, value) => parent.postMessage({ osmo: "action", id: String(id), value: value === undefined ? undefined : String(value) }, "*");
  window.osmo = Object.freeze({ action: send });
  addEventListener("message", (event) => {
    if (event.source !== parent || !event.data || event.data.osmo !== "theme") return;
    document.documentElement.dataset.theme = event.data.theme === "light" ? "light" : "dark";
  });
  addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target.closest("[data-osmo-action]") : null;
    if (!target || target instanceof HTMLFormElement) return;
    send(target.getAttribute("data-osmo-action"), target.getAttribute("data-osmo-value") ?? undefined);
  });
  addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-osmo-action")) return;
    event.preventDefault();
    send(form.getAttribute("data-osmo-action"), JSON.stringify(Object.fromEntries(new FormData(form))));
  });
})();`;
}

export const VALUE_MAX = 2000;

/** What the frame may post: an action id and an optional value. */
export const bridgeMessageSchema = z
  .object({
    osmo: z.literal("action"),
    id: z.string().min(1).max(64),
    value: z.string().max(VALUE_MAX).optional(),
  })
  .strict();
