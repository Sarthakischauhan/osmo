import { FilePlus2, RotateCw, Search } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { MarkdownEditor } from "@/components/MarkdownEditor";
import { type Note, type NoteEntry, type NoteHit, type VaultApi, type VaultInfo, frontmatterTags, withFrontmatterTags, notePath, noteReferences, snippetRuns, vaultApi } from "@/lib/vault";

export interface NotesViewProps {
  api?: VaultApi;
  /** Open this note first (screenshots, links from a thread). */
  initialPath?: string;
  onOpenThread?: (id: string) => void;
  onOpenAutomation?: (id: string) => void;
  onOpenEvent?: (id: string) => void;
}

type Listed = { kind: "notes"; notes: NoteEntry[] } | { kind: "hits"; hits: NoteHit[] };

const DAY = { month: "short", day: "numeric" } as const;

/** The vault: its notes (or search hits) on the left, the open note in a CodeMirror editor on the right. */
export function NotesView({ api: given, initialPath, onOpenThread, onOpenAutomation, onOpenEvent }: NotesViewProps) {
  const api = useMemo(() => given ?? vaultApi(), [given]);
  const [vaults, setVaults] = useState<VaultInfo[]>([]);
  const [vault, setVault] = useState<string>();
  const [listed, setListed] = useState<Listed>({ kind: "notes", notes: [] });
  const [query, setQuery] = useState("");
  const [selectedTag, setSelectedTag] = useState("");
  const [tagDraft, setTagDraft] = useState("");
  const [note, setNote] = useState<Note>();
  const [draft, setDraft] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string>();
  const [saving, setSaving] = useState(false);
  const dirty = note !== undefined && draft !== note.text;

  const fail = (cause: unknown) => setError(cause instanceof Error ? cause.message : String(cause));

  useEffect(() => {
    api.vaults().then((found) => {
      setVaults(found);
      setVault((current) => current ?? found[0]?.name);
    }, fail);
  }, [api]);

  const refresh = useCallback(async () => {
    if (vault === undefined) return;
    try {
      setListed(query.trim() ? { kind: "hits", hits: await api.search(vault, query.trim()) } : { kind: "notes", notes: await api.notes(vault) });
      setError(undefined);
    } catch (cause) {
      fail(cause);
    }
  }, [api, vault, query]);

  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), query ? 150 : 0);
    return () => window.clearTimeout(timer);
  }, [refresh, query]);

  const open = useCallback(
    async (path: string) => {
      if (vault === undefined) return;
      if (dirty && !window.confirm("Discard unsaved changes?")) return;
      try {
        const opened = await api.note(vault, path);
        setNote(opened);
        setDraft(opened.text);
        setError(undefined);
      } catch (cause) {
        fail(cause);
      }
    },
    [api, vault, dirty],
  );

  useEffect(() => {
    if (initialPath && vault !== undefined && note === undefined) void open(initialPath);
  }, [initialPath, vault, note, open]);

  const save = useCallback(async () => {
    if (vault === undefined || note === undefined || saving) return;
    setSaving(true);
    try {
      const saved = await api.save(vault, note.path, draft, "replace");
      setNote(saved);
      setDraft(saved.text);
      setError(undefined);
      void refresh();
    } catch (cause) {
      fail(cause);
    } finally {
      setSaving(false);
    }
  }, [api, vault, note, draft, saving, refresh]);

  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const typed = new FormData(event.currentTarget).get("path");
    const path = notePath(typeof typed === "string" ? typed : "");
    if (vault === undefined || path === undefined) return setError("Name the note, e.g. Ideas/walking-desk");
    const title = path.split("/").pop()?.replace(/\.md$/i, "") ?? path;
    try {
      const made = await api.save(vault, path, `# ${title}\n\n`, "create");
      setCreating(false);
      setNote(made);
      setDraft(made.text);
      setQuery("");
      void refresh();
    } catch (cause) {
      fail(cause);
    }
  };

  const allRows = listed.kind === "notes" ? listed.notes : listed.hits;
  const tags = [...new Set(allRows.flatMap((row) => row.tags))].sort((a, b) => a.localeCompare(b));
  const rows = selectedTag ? allRows.filter((row) => row.tags.includes(selectedTag)) : allRows;
  const editTags = (next: string[]) => setDraft((current) => withFrontmatterTags(current, next));
  const addTag = () => {
    const tag = tagDraft.trim().replace(/^#/, "");
    if (!/^[\p{L}\p{N}_/-]{1,64}$/u.test(tag)) {
      if (tag) setError("Tags can contain letters, numbers, /, - and _ (up to 64 characters).");
      return;
    }
    editTags([...frontmatterTags(draft), tag]);
    setTagDraft("");
    setError(undefined);
  };

  return (
    <div className="osmo-notes">
      <header className="osmo-view-head">
        <h1>Notes</h1>
        {vaults.length > 1 && (
          <select className="osmo-view-select" aria-label="Vault" value={vault} onChange={(event) => { setVault(event.target.value); setNote(undefined); }}>
            {vaults.map((v) => <option key={v.name} value={v.name}>{v.name}</option>)}
          </select>
        )}
        <div className="osmo-view-tools">
          <select className="osmo-view-select osmo-note-filter" aria-label="Filter notes by tag" value={selectedTag}
            onChange={(event) => setSelectedTag(event.target.value)}>
            <option value="">All tags</option>
            {tags.map((tag) => <option key={tag} value={tag}>#{tag}</option>)}
          </select>
          <label className="osmo-notes-search">
            <Search size={14} aria-hidden />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search notes" aria-label="Search notes" />
          </label>
          <button type="button" className="osmo-btn osmo-btn-ghost osmo-btn-sm" onClick={() => setCreating((on) => !on)}>
            <FilePlus2 size={14} aria-hidden /> New note
          </button>
        </div>
      </header>
      {creating && (
        <form className="osmo-notes-create" onSubmit={(event) => void create(event)}>
          <input name="path" autoFocus placeholder="Folder/note name" aria-label="New note path" />
          <button type="submit" className="osmo-btn osmo-btn-primary osmo-btn-sm">Create</button>
        </form>
      )}
      {error && <p className="osmo-notes-error" role="alert">{error}</p>}
      <div className="osmo-notes-pane">
        <nav className="osmo-notes-list" aria-label={listed.kind === "hits" ? "Search results" : "Notes"}>

          {rows.length === 0 && <p className="osmo-notes-empty">{selectedTag ? "No notes with that tag." : listed.kind === "hits" ? "No notes match." : "No notes yet. Osmo keeps them as markdown files in your vault folder."}</p>
          {rows.map((row) => (
            <button
              key={row.path}
              type="button"
              className="osmo-notes-row"
              data-active={note?.path === row.path}
              onClick={() => void open(row.path)}
            >
              <span className="osmo-notes-row-title">{row.title}</span>
              {row.tags.length > 0 && (
                <span className="osmo-note-row-tags">
                  {row.tags.slice(0, 3).map((tag) => <span key={tag} className="osmo-note-tag">#{tag}</span>)}
                </span>
              )}
              {"snippet" in row ? (
                <span className="osmo-notes-row-snippet">
                  {snippetRuns(row.snippet).map((run, i) => (run.hit ? <mark key={i}>{run.text}</mark> : <span key={i}>{run.text}</span>))}
                </span>
              ) : (
                <span className="osmo-notes-row-meta">
                  <span className="mono">{row.path}</span>
                  <span>{new Date(row.modified * 1000).toLocaleDateString("en-US", DAY)}</span>
                </span>
              )}
            </button>
          ))}
        </nav>
        <section className="osmo-notes-doc" aria-label="Note">
          {note === undefined ? (
            <p className="osmo-notes-empty">Pick a note, or ask Osmo to find one.</p>
          ) : (
            <>
              <div className="osmo-notes-doc-head">
                <span className="mono osmo-notes-path">{note.vault}/{note.path}</span>
                <span className="osmo-notes-state">{saving ? "Saving…" : dirty ? "Edited" : "Saved"}</span>
                <button type="button" className="osmo-icon-btn" aria-label="Reload from disk" onClick={() => void open(note.path)}>
                  <RotateCw size={14} />
                </button>
                <button type="button" className="osmo-btn osmo-btn-primary osmo-btn-sm" disabled={!dirty || saving} onClick={() => void save()}>
                  Save <kbd>⌘S</kbd>
                </button>
              </div>
              <div className="osmo-note-tags" aria-label="Note tags">
                {frontmatterTags(draft).map((tag) => (
                  <button key={tag} type="button" className="osmo-note-tag" title="Remove tag" aria-label={`Remove tag ${tag}`}
                    onClick={() => editTags(frontmatterTags(draft).filter((item) => item !== tag))}>#{tag} ×</button>
                ))}
                <input className="osmo-note-tags-input" aria-label="New tag" placeholder="Add a tag…"
                  value={tagDraft} onChange={(event) => setTagDraft(event.target.value)}
                  onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addTag(); } }} />
                <button type="button" className="osmo-note-tags-add" onClick={addTag} disabled={!tagDraft.trim()}>Add tag</button>
              </div>
              <MarkdownEditor docKey={`${note.vault}/${note.path}@${note.modified}`} value={note.text} onChange={setDraft} onSave={() => void save()} />
              {noteReferences(draft).length > 0 && (
                <footer className="osmo-notes-backlinks osmo-notes-references">
                  <span>References</span>
                  {noteReferences(draft).map((ref) => (
                    <button key={ref.kind + ":" + ref.id} type="button" className="osmo-notes-link"
                      onClick={() => {
                        if (ref.kind === "chat") onOpenThread?.(ref.id);
                        else if (ref.kind === "automation") onOpenAutomation?.(ref.id);
                        else onOpenEvent?.(ref.id);
                      }}>
                      {ref.kind === "chat" ? "Chat" : ref.kind === "automation" ? "Automation" : "Event"} · {ref.id.slice(0, 14)}
                    </button>
                  ))}
                </footer>
              )}
              {note.backlinks.length > 0 && (
                <footer className="osmo-notes-backlinks">
                  <span>Linked from</span>
                  {note.backlinks.map((path) => (
                    <button key={path} type="button" className="osmo-notes-link" onClick={() => void open(path)}>{path.replace(/\.md$/i, "")}</button>
                  ))}
                </footer>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
