import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { markdown } from "@codemirror/lang-markdown";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { EditorState } from "@codemirror/state";
import { EditorView, keymap, placeholder } from "@codemirror/view";
import { tags } from "@lezer/highlight";
import { useEffect, useRef } from "react";

export interface MarkdownEditorProps {
  /** The note's text when it was opened; a new `docKey` loads a new one. */
  value: string;
  docKey: string;
  onChange: (text: string) => void;
  onSave: () => void;
  readOnly?: boolean;
}

/** Headings, emphasis, links and code as the kit's type scale has them; colours come from the app's tokens. */
const osmoMarkdown = HighlightStyle.define([
  { tag: tags.heading1, fontSize: "20px", fontWeight: "600", lineHeight: "28px" },
  { tag: tags.heading2, fontSize: "16px", fontWeight: "600" },
  { tag: [tags.heading3, tags.heading4, tags.heading5, tags.heading6], fontWeight: "600" },
  { tag: tags.strong, fontWeight: "600" },
  { tag: tags.emphasis, fontStyle: "italic" },
  { tag: tags.link, textDecoration: "underline" },
  { tag: [tags.url, tags.processingInstruction, tags.meta], color: "var(--muted-foreground)" },
  { tag: tags.monospace, fontFamily: "var(--font-mono)", fontSize: "12px" },
  { tag: tags.quote, color: "var(--muted-foreground)" },
]);

const theme = EditorView.theme({
  "&": { height: "100%", fontSize: "14px", backgroundColor: "transparent", color: "var(--foreground)" },
  ".cm-scroller": { fontFamily: "var(--font-sans)", lineHeight: "22px" },
  ".cm-content": { padding: "16px 0", caretColor: "var(--foreground)" },
  ".cm-line": { padding: "0 20px" },
  "&.cm-focused": { outline: "none" },
  ".cm-cursor": { borderLeftColor: "var(--foreground)" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": { backgroundColor: "var(--surface-3)" },
  ".cm-placeholder": { color: "var(--muted-foreground)" },
});

/** CodeMirror 6 on one note: markdown highlighting, undo history, ⌘S / Ctrl+S to save. */
export function MarkdownEditor({ value, docKey, onChange, onSave, readOnly = false }: MarkdownEditorProps) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView>();
  const handlers = useRef({ onChange, onSave });
  handlers.current = { onChange, onSave };

  useEffect(() => {
    if (!host.current) return;
    const editor = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: value,
        extensions: [
          history(),
          markdown(),
          syntaxHighlighting(osmoMarkdown),
          EditorView.lineWrapping,
          placeholder("Write in markdown…"),
          theme,
          EditorState.readOnly.of(readOnly),
          keymap.of([{ key: "Mod-s", preventDefault: true, run: () => { handlers.current.onSave(); return true; } }, indentWithTab, ...defaultKeymap, ...historyKeymap]),
          EditorView.updateListener.of((update) => {
            if (update.docChanged) handlers.current.onChange(update.state.doc.toString());
          }),
        ],
      }),
    });
    view.current = editor;
    return () => {
      editor.destroy();
      view.current = undefined;
    };
    // A new note (docKey) builds a new editor; typing must not rebuild it.
  }, [docKey, readOnly]);

  return <div ref={host} className="osmo-notes-editor" data-testid="markdown-editor" />;
}
