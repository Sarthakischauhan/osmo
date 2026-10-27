import type { Thread } from "@osmo/osmo-composer";
import { History, MoreHorizontal, PanelLeft, Plus } from "lucide-react";
import { type ReactNode, type KeyboardEvent, useEffect, useRef, useState } from "react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { ContextMenu, ContextMenuContent, ContextMenuItem, ContextMenuSeparator, ContextMenuTrigger } from "@/components/ui/context-menu";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { groupThreads } from "@/lib/threads";

export interface ThreadSidebarProps {
  threads: readonly Thread[];
  activeId: string | undefined;
  /** Open a thread, or Home when `undefined`. */
  onSelect: (id: string | undefined) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => void;
  /** Fork a thread; resolves to the copy, which the sidebar then offers to rename. */
  onFork: (id: string) => Promise<Thread | undefined>;
  onDelete: (id: string) => void;
  /** How many messages a thread holds, for the delete confirmation. */
  countMessages: (id: string) => Promise<number>;
  /** Start a thread in rename mode (screenshots and tests). */
  initialRenaming?: string;
  now?: number;
  footer?: ReactNode;
}

/** Osmo's thread list: date groups, inline rename, fork, delete with confirmation. */
export function ThreadSidebar({ threads, activeId, onSelect, onNew, onRename, onFork, onDelete, countMessages, initialRenaming, now, footer }: ThreadSidebarProps) {
  const [renaming, setRenaming] = useState<string | undefined>(initialRenaming);
  const [deleting, setDeleting] = useState<{ thread: Thread; messages: number }>();
  const { state, toggleSidebar } = useSidebar();
  const collapsed = state === "collapsed";
  const groups = groupThreads(threads, now ?? Date.now());

  const fork = async (id: string) => {
    const copy = await onFork(id);
    if (copy) setRenaming(copy.id);
  };
  const askDelete = async (thread: Thread) => setDeleting({ thread, messages: await countMessages(thread.id) });

  return (
    <Sidebar collapsible="icon" className="osmo-sidebar">
      <SidebarHeader className="osmo-sidebar-header">
        {!collapsed && <strong className="osmo-sidebar-brand">Osmo</strong>}
        <IconButton label={collapsed ? "Expand sidebar" : "Collapse sidebar"} hint="⌘B" onClick={toggleSidebar}>
          <PanelLeft size={16} />
        </IconButton>
      </SidebarHeader>
      <SidebarContent>
        {collapsed ? (
          <div className="osmo-sidebar-icons">
            <IconButton label="New thread" hint="⌘K" onClick={onNew}>
              <Plus size={16} />
            </IconButton>
            <IconButton label="History" onClick={toggleSidebar}>
              <History size={16} />
            </IconButton>
          </div>
        ) : (
          <>
            <button type="button" className="osmo-new-thread" onClick={onNew}>
              <Plus size={16} aria-hidden />
              <span>New thread</span>
              <kbd>⌘K</kbd>
            </button>
            {groups.length === 0 ? (
              <div className="osmo-sidebar-empty">
                <p className="osmo-sidebar-empty-title">No threads yet</p>
                <p>Conversations appear here.</p>
              </div>
            ) : (
              groups.map(({ group, threads: rows }) => (
                <SidebarGroup key={group} className="osmo-thread-group">
                  <SidebarGroupLabel className="osmo-thread-group-label">{group}</SidebarGroupLabel>
                  <SidebarMenu>
                    {rows.map((thread) => (
                      <ThreadRow
                        key={thread.id}
                        thread={thread}
                        active={thread.id === activeId}
                        renaming={renaming === thread.id}
                        onSelect={() => onSelect(thread.id)}
                        onStartRename={() => setRenaming(thread.id)}
                        onRename={(title) => {
                          setRenaming(undefined);
                          if (title !== undefined) onRename(thread.id, title);
                        }}
                        onFork={() => void fork(thread.id)}
                        onDelete={() => void askDelete(thread)}
                      />
                    ))}
                  </SidebarMenu>
                </SidebarGroup>
              ))
            )}
          </>
        )}
      </SidebarContent>
      {footer && <SidebarFooter className="osmo-desktop-footer">{footer}</SidebarFooter>}
      <SidebarRail />
      <AlertDialog open={deleting !== undefined} onOpenChange={(open) => !open && setDeleting(undefined)}>
        <AlertDialogContent className="osmo-delete-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>Delete thread?</AlertDialogTitle>
            <AlertDialogDescription>
              {deleting && `"${deleting.thread.title}" and its ${deleting.messages} ${deleting.messages === 1 ? "message" : "messages"} will be permanently deleted.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel autoFocus className="osmo-btn osmo-btn-ghost">
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              className="osmo-btn osmo-btn-destructive"
              onClick={() => {
                if (deleting) onDelete(deleting.thread.id);
                setDeleting(undefined);
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Sidebar>
  );
}

function IconButton({ label, hint, onClick, children }: { label: string; hint?: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button type="button" className="osmo-icon-btn" aria-label={label} onClick={onClick}>
          {children}
        </button>
      </TooltipTrigger>
      <TooltipContent side="right">
        {label}
        {hint && <kbd className="osmo-tooltip-kbd">{hint}</kbd>}
      </TooltipContent>
    </Tooltip>
  );
}

interface ThreadRowProps {
  thread: Thread;
  active: boolean;
  renaming: boolean;
  onSelect: () => void;
  onStartRename: () => void;
  /** The new title, or `undefined` when the rename was cancelled or left empty. */
  onRename: (title: string | undefined) => void;
  onFork: () => void;
  onDelete: () => void;
}

function ThreadRow({ thread, active, renaming, onSelect, onStartRename, onRename, onFork, onDelete }: ThreadRowProps) {
  if (renaming) {
    return (
      <SidebarMenuItem>
        <RenameInput title={thread.title} onDone={onRename} />
      </SidebarMenuItem>
    );
  }
  return (
    <SidebarMenuItem className="osmo-thread-row" data-active={active}>
      <ContextMenu>
        <ContextMenuTrigger asChild>
          <SidebarMenuButton isActive={active} aria-current={active ? "page" : undefined} onClick={onSelect} className="osmo-thread-button">
            <span className="osmo-thread-title">{thread.title}</span>
          </SidebarMenuButton>
        </ContextMenuTrigger>
        <ContextMenuContent className="osmo-menu">
          <ContextMenuItem onSelect={onStartRename}>Rename</ContextMenuItem>
          <ContextMenuItem onSelect={onFork}>Fork</ContextMenuItem>
          <ContextMenuSeparator />
          <ContextMenuItem variant="destructive" onSelect={onDelete}>
            Delete
          </ContextMenuItem>
        </ContextMenuContent>
      </ContextMenu>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <SidebarMenuAction showOnHover aria-label={`Thread actions for ${thread.title}`} className="osmo-thread-action">
            <MoreHorizontal size={16} />
          </SidebarMenuAction>
        </DropdownMenuTrigger>
        <DropdownMenuContent side="right" align="start" className="osmo-menu">
          <DropdownMenuItem onSelect={onStartRename}>Rename</DropdownMenuItem>
          <DropdownMenuItem onSelect={onFork}>Fork</DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" onSelect={onDelete}>
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </SidebarMenuItem>
  );
}

function RenameInput({ title, onDone }: { title: string; onDone: (title: string | undefined) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const done = useRef(false);
  useEffect(() => {
    // After the menu closes and returns focus, so the input keeps it.
    const timer = window.setTimeout(() => {
      input.current?.focus();
      input.current?.select();
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);
  const finish = (value: string | undefined) => {
    if (done.current) return;
    done.current = true;
    const trimmed = value?.trim();
    onDone(trimmed && trimmed !== title ? trimmed : undefined);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") finish(event.currentTarget.value);
    else if (event.key === "Escape") finish(undefined);
  };
  return (
    <input
      ref={input}
      className="osmo-rename"
      aria-label="Thread title"
      defaultValue={title}
      onKeyDown={onKeyDown}
      onBlur={(event) => finish(event.currentTarget.value)}
    />
  );
}
