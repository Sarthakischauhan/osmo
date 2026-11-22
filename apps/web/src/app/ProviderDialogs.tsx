import { useState } from "react";
import { AgentModelDialog } from "@/components/AgentModelDialog";
import type { useDesktopSupport } from "@/desktopSupport";
import { ConnectProviderDialog, type ProviderInfo } from "@/ConnectProvider";

/** Which provider dialog is open: connecting a provider, or the agent's model settings (which a key connect returns to). */
export function useProviderDialogs() {
  const [connecting, setConnecting] = useState<ProviderInfo>();
  const [agentModelOpen, setAgentModelOpen] = useState(false);
  const [returnToAgentSettings, setReturnToAgentSettings] = useState(false);
  return { connecting, setConnecting, agentModelOpen, setAgentModelOpen, returnToAgentSettings, setReturnToAgentSettings };
}

type ProviderDialogsProps = {
  dialogs: ReturnType<typeof useProviderDialogs>;
  providers: ProviderInfo[];
  /** Reload the catalog after credentials change. */
  refresh: () => void;
  desktop: ReturnType<typeof useDesktopSupport>;
};

/** The connect-a-provider dialog and the agent model settings. */
export function ProviderDialogs({ dialogs, providers, refresh, desktop }: ProviderDialogsProps) {
  const { connecting, setConnecting, agentModelOpen, setAgentModelOpen, returnToAgentSettings, setReturnToAgentSettings } = dialogs;
  const closeConnect = () => {
    setConnecting(undefined);
    if (returnToAgentSettings) setAgentModelOpen(true);
    setReturnToAgentSettings(false);
  };
  return (
    <>
      <ConnectProviderDialog
        provider={connecting}
        onOpenChange={(open) => {
          if (!open) closeConnect();
        }}
        onConnected={() => {
          closeConnect();
          refresh();
        }}
      />
      <AgentModelDialog
        open={agentModelOpen}
        onOpenChange={setAgentModelOpen}
        providers={providers}
        onCredentialsChanged={refresh}
        onConnectKey={(provider) => {
          setReturnToAgentSettings(true);
          setAgentModelOpen(false);
          setConnecting(provider);
        }}
        desktop={desktop}
      />
    </>
  );
}
