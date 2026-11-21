/**
 * The Osmo UI kit every html widget gets: Geist-style tokens (gray scale, 1px alpha borders,
 * 6/8/12 radii, a type scale) and a few classes, so the model writes short semantic HTML
 * instead of inline CSS. Plain CSS, no React. No font file: an opaque-origin frame cannot fetch
 * the app's fonts, so Geist is used when installed and the system stack otherwise.
 * Dark unless the frame's root says `data-theme="light"` (the app's appearance, set by the bridge).
 */
export const OSMO_UI_CSS = `
:root {
  color-scheme: dark;
  --bg-100: #0a0a0a; --bg-200: #000;
  --gray-100: #1a1a1a; --gray-200: #1f1f1f; --gray-300: #292929; --gray-400: #2e2e2e;
  --gray-500: #454545; --gray-600: #878787; --gray-700: #8f8f8f; --gray-900: #a1a1a1; --gray-1000: #ededed;
  --gray-alpha-100: rgb(255 255 255 / .06); --gray-alpha-200: rgb(255 255 255 / .09); --gray-alpha-400: rgb(255 255 255 / .14);
  --blue: #52a8ff; --green: #3ecf8e; --amber: #f5a524; --red: #f87171;
  --radius-sm: 6px; --radius-md: 8px; --radius-lg: 12px;
  --font-sans: Geist, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  --font-mono: "Geist Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
}
:root[data-theme="light"] {
  color-scheme: light;
  --bg-100: #fff; --bg-200: #fafafa;
  --gray-100: #f2f2f2; --gray-200: #ebebeb; --gray-300: #e6e6e6; --gray-400: #ebebeb;
  --gray-500: #c9c9c9; --gray-600: #a8a8a8; --gray-700: #8f8f8f; --gray-900: #666; --gray-1000: #171717;
  --gray-alpha-100: rgb(0 0 0 / .05); --gray-alpha-200: rgb(0 0 0 / .08); --gray-alpha-400: rgb(0 0 0 / .08);
  --blue: #006bff; --green: #18794e; --amber: #a35200; --red: #da2f35;
}
*, *::before, *::after { box-sizing: border-box; }
body { margin: 0; padding: 16px; background: var(--bg-100); color: var(--gray-1000); font: 14px/20px var(--font-sans); -webkit-font-smoothing: antialiased; }
h1 { margin: 0 0 8px; font-size: 20px; line-height: 28px; font-weight: 600; letter-spacing: -.02em; }
h2 { margin: 0 0 8px; font-size: 16px; line-height: 24px; font-weight: 600; letter-spacing: -.01em; }
h3 { margin: 0 0 4px; font-size: 14px; line-height: 20px; font-weight: 600; }
p { margin: 0 0 8px; }
.muted { color: var(--gray-900); }
.mono, code { font-family: var(--font-mono); font-size: 13px; }
.grid { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); }
.row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.card { padding: 16px; border: 1px solid var(--gray-alpha-400); border-radius: var(--radius-lg); background: var(--bg-200); }
.stat { display: grid; gap: 4px; }
.stat > .label { color: var(--gray-900); font-size: 12px; line-height: 16px; }
.stat > .value { font-size: 24px; line-height: 32px; font-weight: 600; letter-spacing: -.02em; font-variant-numeric: tabular-nums; }
.table { width: 100%; border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; }
.table th { padding: 8px 12px; text-align: left; font-weight: 500; color: var(--gray-900); border-bottom: 1px solid var(--gray-alpha-400); }
.table td { padding: 8px 12px; border-bottom: 1px solid var(--gray-alpha-200); }
.badge { display: inline-flex; align-items: center; height: 20px; padding: 0 8px; border-radius: 999px; background: var(--gray-alpha-200); font-size: 12px; font-weight: 500; }
.badge.green { color: var(--green); } .badge.amber { color: var(--amber); } .badge.red { color: var(--red); } .badge.blue { color: var(--blue); }
.kbd { display: inline-block; min-width: 20px; padding: 0 4px; border: 1px solid var(--gray-alpha-400); border-radius: var(--radius-sm); font: 12px/18px var(--font-mono); text-align: center; color: var(--gray-900); }
.button { display: inline-flex; align-items: center; justify-content: center; gap: 6px; height: 32px; padding: 0 12px; border: 1px solid var(--gray-alpha-400); border-radius: var(--radius-md); background: var(--bg-100); color: var(--gray-1000); font: 500 14px/20px var(--font-sans); cursor: pointer; transition: background .15s ease; }
.button:hover { background: var(--gray-alpha-100); }
.button.primary { border-color: transparent; background: var(--gray-1000); color: var(--bg-100); }
.button.primary:hover { opacity: .9; }
.button:disabled { opacity: .5; cursor: not-allowed; }
.input { height: 36px; width: 100%; padding: 0 12px; border: 1px solid var(--gray-alpha-400); border-radius: var(--radius-sm); background: var(--bg-100); color: var(--gray-1000); font: 14px var(--font-sans); }
:focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; }
.tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--gray-alpha-400); }
.tabs > * { padding: 8px 12px; border: 0; background: none; color: var(--gray-900); font: 14px var(--font-sans); cursor: pointer; }
.tabs > [aria-selected="true"] { color: var(--gray-1000); box-shadow: inset 0 -2px var(--gray-1000); }
.list { margin: 0; padding: 0; list-style: none; border: 1px solid var(--gray-alpha-400); border-radius: var(--radius-md); }
.list > li { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 16px; }
.list > li + li { border-top: 1px solid var(--gray-alpha-200); }
.empty { display: grid; place-items: center; gap: 4px; padding: 32px 16px; border: 1px dashed var(--gray-alpha-400); border-radius: var(--radius-md); color: var(--gray-900); text-align: center; }
.progress { height: 8px; overflow: hidden; border-radius: 999px; background: var(--gray-alpha-200); }
.progress > span { display: block; height: 100%; border-radius: inherit; background: var(--gray-1000); }
`;
