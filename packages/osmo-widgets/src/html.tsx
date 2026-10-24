import type { HtmlProps } from "./schema";

/**
 * Generated HTML runs in an opaque-origin iframe: scripts yes, same-origin access no.
 * chat-sdk's ArtifactWidget previews files as text/images only, so live HTML stays local.
 */
export function HtmlWidget({ html }: HtmlProps) {
  return (
    <section className="osmo-html">
      <iframe title="Generated widget" sandbox="allow-scripts allow-forms" srcDoc={html} referrerPolicy="no-referrer" />
    </section>
  );
}
