import { ConversationFeed, type DecideApproval } from "./ConversationFeed";
import { compactMessages } from "./compact";
import type { Turn } from "./feed";

/** The latest spoken lines of the conversation, streaming as they arrive, rendered by the workspace's own feed. */
export function CompactTranscript({ turns }: { turns: readonly Turn[] }) {
  const noDecisions: DecideApproval = async () => undefined;
  return (
    <div className="osmo-compact-transcript" aria-live="polite" aria-label="Conversation">
      <ConversationFeed turns={compactMessages(turns)} runs={{}} onDecide={noDecisions} compact />
    </div>
  );
}
