import { useOsmoSession } from "@osmo/osmo-composer";
import "./desktopBridge";
import { createDesktopSession } from "./desktopSession";

const bridge = window.osmoDesktop;

/**
 * The voice session a page uses. In the Electron app main owns the socket and audio and both windows share it; in a
 * browser the page owns its own (the standalone web deployment).
 */
export const useSession = bridge ? createDesktopSession(bridge.session) : useOsmoSession;
