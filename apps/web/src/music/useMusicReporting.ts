import { useEffect } from "react";
import type { OsmoSession } from "@osmo/osmo-composer";
import { musicPlayer, reportable } from "@/music/player";

/** The server's music_status reads what the player last reported: report changes, and what plays when a call connects. Ducking follows Osmo's voice. */
export function useMusicReporting(reportMusic: OsmoSession["reportMusic"], connected: boolean, speaking: boolean): void {
  useEffect(() => {
    let last = musicPlayer.getSnapshot();
    return musicPlayer.subscribe(() => {
      const next = musicPlayer.getSnapshot();
      if (reportable(last, next)) reportMusic(musicPlayer.state());
      last = next;
    });
  }, [reportMusic]);
  useEffect(() => musicPlayer.duck(speaking), [speaking]);
  useEffect(() => {
    if (connected) reportMusic(musicPlayer.state());
  }, [connected, reportMusic]);
}
