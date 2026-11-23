import { Pause, Play } from "lucide-react";
import { useEffect, useState, useSyncExternalStore } from "react";
import { z } from "zod";
import type { MusicPlayer } from "@/music/store";

const statusSchema = z.object({ localTracks: z.number(), localReady: z.boolean(), spotifyConfigured: z.boolean(), spotifyConnected: z.boolean() });
type MusicStatus = z.infer<typeof statusSchema>;

/** `The xx` → `xx`, `Bonobo` → `BO`: what the art square shows without a cover (as in the mockup). */
export function initials(artist: string, title: string): string {
  const words = (artist || title).replace(/^the\s+/i, "").split(/\s+/).filter(Boolean);
  if (words.length === 0) return "♪";
  const first = words[0] ?? "";
  if (words.length === 1) return first.length <= 3 ? first : first.slice(0, 2).toUpperCase();
  return words.slice(0, 2).map((word) => word[0]?.toUpperCase() ?? "").join("");
}

/**
 * The sidebar footer player from GUI's music-mini mockup: cover (or initials), title, artist and a round play button.
 * It reads the shared `MusicPlayer`; with nothing playing it says how to start, and offers Spotify when it is set up
 * but not connected.
 */
export function MusicMini({ player, collapsed = false }: { player: MusicPlayer; collapsed?: boolean }) {
  const music = useSyncExternalStore(player.subscribe, player.getSnapshot);
  const [status, setStatus] = useState<MusicStatus>();
  useEffect(() => {
    let mounted = true;
    const refresh = () => {
      void fetch("/api/music/status")
        .then(async (response) => response.ok ? statusSchema.parse(await response.json()) : undefined)
        .then((value) => { if (mounted) setStatus(value); }, () => { if (mounted) setStatus(undefined); });
    };
    refresh();
    // The Spotify OAuth callback opens in a separate tab; refresh on return.
    window.addEventListener("focus", refresh);
    const poll = window.setInterval(refresh, 15_000);
    return () => { mounted = false; window.removeEventListener("focus", refresh); window.clearInterval(poll); };
  }, []);
  const connect = async () => {
    const response = await fetch("/api/music/spotify/login");
    const body = z.object({ url: z.string().url() }).safeParse(await response.json().catch(() => undefined));
    if (body.success) window.open(body.data.url, "_blank", "noopener");
  };
  const track = music.track;
  const searchLink = `https://open.spotify.com/search/${encodeURIComponent(music.handoffQuery ?? track?.title ?? "music")}`;
  if (collapsed) {
    return track ? (
      <div className="osmo-music-mini" data-collapsed>
        <button type="button" className="osmo-music-play" aria-label={music.playing ? "Pause" : "Play"} onClick={() => void player.toggle()}>
          {music.playing ? <Pause size={10} fill="currentColor" /> : <Play size={10} fill="currentColor" />}
        </button>
      </div>
    ) : null;
  }
  const sub = music.error ?? (track && music.ducked && music.playing ? "Ducked while Osmo speaks" : track?.artist);
  return (
    <div className="osmo-music-mini" aria-label="Music">
      <div className="osmo-music-art" aria-hidden>
        {track?.art ? <img src={track.art} alt="" /> : track ? initials(track.artist, track.title) : "♪"}
      </div>
      <div className="osmo-music-text">
        <p className="osmo-music-title">{track ? track.title : "Music"}</p>
        {track ? (
          <p className="osmo-music-sub" data-error={music.error !== undefined}>{sub}</p>
        ) : music.handoffQuery ? (
          <a className="osmo-music-connect" href={searchLink} target="_blank" rel="noopener noreferrer">Open Spotify results</a>
        ) : status?.spotifyConfigured && !status.spotifyConnected ? (
          <button type="button" className="osmo-music-connect" onClick={() => void connect()}>Connect Spotify</button>
        ) : (
          <a className="osmo-music-connect" href="https://open.spotify.com/" target="_blank" rel="noopener noreferrer">Open Spotify</a>
        )}
      </div>
      {track?.source === "spotify" && music.error && (
        <a className="osmo-music-connect" href={searchLink} target="_blank" rel="noopener noreferrer">Open Spotify</a>
      )}
      {track && (
        <button type="button" className="osmo-music-play" aria-label={music.playing ? "Pause" : "Play"} onClick={() => void player.toggle()}>
          {music.playing ? <Pause size={10} fill="currentColor" /> : <Play size={10} fill="currentColor" />}
        </button>
      )}
    </div>
  );
}
