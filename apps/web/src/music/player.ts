import { browserEngines } from "./engines";
import { MusicPlayer, type MusicSnapshot } from "./store";

/** The app's one music player: the sidebar mini player shows it, and a chat widget can attach to it later. */
export const musicPlayer = new MusicPlayer(browserEngines);

/** Whether the server needs telling: what plays, whether it plays, the volume or an error changed (not a ramp step). */
export function reportable(before: MusicSnapshot, after: MusicSnapshot): boolean {
  return before.track?.id !== after.track?.id || before.playing !== after.playing || before.volume !== after.volume || before.error !== after.error;
}
