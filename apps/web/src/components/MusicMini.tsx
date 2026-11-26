import { Pause, Play } from "lucide-react";
import { useEffect, useState, useSyncExternalStore } from "react";
import { z } from "zod";
import type { MusicPlayer } from "@/music/store";

const statusSchema = z.object({
  localTracks: z.number(),
  localReady: z.boolean(),
  spotifyConfigured: z.boolean(),
  spotifyConnected: z.boolean(),
  spotifyClientIdEditable: z.boolean().default(false),
  spotifyRedirectUri: z.string().default(""),
});
const errorSchema = z.object({ detail: z.union([z.string(), z.array(z.object({ msg: z.string() }))]) });

async function problem(response: Response): Promise<string> {
  const body = errorSchema.safeParse(await response.json().catch(() => undefined));
  if (!body.success) return `Spotify setup failed (HTTP ${response.status})`;
  return typeof body.data.detail === "string" ? body.data.detail : body.data.detail.map((item) => item.msg).join("; ");
}

/**
 * Connecting Spotify without OSMO_SPOTIFY_CLIENT_ID: the user's own Spotify app (PKCE, so only its public client id),
 * with the redirect URI it must list. Saving it starts the sign-in.
 */
function SpotifySetup({ redirectUri, onSaved, onCancel }: { redirectUri: string; onSaved: (status: MusicStatus) => void; onCancel: () => void }) {
  const [clientId, setClientId] = useState("");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const save = async () => {
    setBusy(true);
    setError(undefined);
    try {
      const response = await fetch("/api/music/spotify/client", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ clientId: clientId.trim() }),
      });
      if (!response.ok) return setError(await problem(response));
      onSaved(statusSchema.parse(await response.json()));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };
  const copy = () => {
    void navigator.clipboard.writeText(redirectUri).then(() => setCopied(true), () => setCopied(false));
  };
  return (
    <form className="osmo-music-setup" onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <p>
        1. Create an app at{" "}
        <a href="https://developer.spotify.com/dashboard" target="_blank" rel="noopener noreferrer">developer.spotify.com</a>{" "}
        with the Web API.
      </p>
      <p>2. Add this Redirect URI:</p>
      <div className="osmo-music-setup-row">
        <code title={redirectUri}>{redirectUri}</code>
        <button type="button" className="osmo-music-connect" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
      </div>
      <label htmlFor="osmo-spotify-client-id">3. Paste its Client ID:</label>
      <input
        id="osmo-spotify-client-id"
        value={clientId}
        onChange={(event) => setClientId(event.target.value)}
        placeholder="32-character Client ID"
        autoComplete="off"
        spellCheck={false}
        required
        autoFocus
      />
      {error && <p className="osmo-music-sub" data-error>{error}</p>}
      <div className="osmo-music-setup-row">
        <button type="submit" className="osmo-music-connect" disabled={busy || clientId.trim() === ""}>{busy ? "Saving…" : "Save & connect"}</button>
        <button type="button" className="osmo-music-connect" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}
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
  const [setup, setSetup] = useState(false);
  const [connectError, setConnectError] = useState<string>();
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
    setConnectError(undefined);
    const response = await fetch("/api/music/spotify/login");
    if (!response.ok) return setConnectError(await problem(response));
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
          <div className="osmo-music-actions">
            <a className="osmo-music-connect" href="https://open.spotify.com/" target="_blank" rel="noopener noreferrer">Open Spotify</a>
            <button type="button" className="osmo-music-connect" onClick={() => void connect()}>Connect for voice control</button>
          </div>
        ) : status && !status.spotifyConfigured && status.spotifyClientIdEditable && !setup ? (
          <div className="osmo-music-actions">
            <a className="osmo-music-connect" href="https://open.spotify.com/" target="_blank" rel="noopener noreferrer">Open Spotify</a>
            <button type="button" className="osmo-music-connect" onClick={() => setSetup(true)}>Connect Spotify</button>
          </div>
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
      {connectError && !track && <p className="osmo-music-sub osmo-music-wide" data-error>{connectError}</p>}
      {setup && !track && status && !status.spotifyConfigured && (
        <SpotifySetup
          redirectUri={status.spotifyRedirectUri}
          onCancel={() => setSetup(false)}
          onSaved={(next) => { setStatus(next); setSetup(false); void connect(); }}
        />
      )}
    </div>
  );
}
