import { AsciiArt } from "@osmo/osmo-widgets";
import type { Theme } from "./appearance";
export function AppearanceSwitch({ theme, setTheme }: { theme: Theme; setTheme: (theme: Theme) => void }) {
  return <div className="osmo-appearance" role="group" aria-label="Appearance">
    {(["light", "dark"] as const).map(value => <button key={value} type="button" aria-label={`${value === "light" ? "Light" : "Dark"} appearance`}
      aria-pressed={theme === value} onClick={() => setTheme(value)}>
      <AsciiArt name={value === "light" ? "sun" : "moon"} /><span>{value === "light" ? "Day" : "Night"}</span>
    </button>)}
  </div>;
}
