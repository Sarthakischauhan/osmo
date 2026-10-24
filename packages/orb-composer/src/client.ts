import { type ClientEvent, type ServerEvent, serverEventSchema } from "./protocol";

export interface OrbClientHandlers {
  onEvent: (event: ServerEvent) => void;
  onAudio: (frame: ArrayBuffer) => void;
  onClose: () => void;
}

/** The single browser WebSocket to Symphony: binary = PCM16, text = JSON events. */
export class OrbClient {
  private socket: WebSocket | null = null;

  constructor(
    private readonly url: string,
    private readonly handlers: OrbClientHandlers,
  ) {}

  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      const socket = new WebSocket(this.url);
      socket.binaryType = "arraybuffer";
      socket.onopen = () => resolve();
      socket.onerror = () => reject(new Error(`could not connect to ${this.url}`));
      socket.onclose = () => {
        this.socket = null;
        this.handlers.onClose();
      };
      socket.onmessage = (message: MessageEvent<ArrayBuffer | string>) => {
        if (typeof message.data !== "string") {
          this.handlers.onAudio(message.data);
          return;
        }
        const parsed = serverEventSchema.safeParse(JSON.parse(message.data));
        if (parsed.success) this.handlers.onEvent(parsed.data);
      };
      this.socket = socket;
    });
  }

  send(event: ClientEvent): void {
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(event));
  }

  sendAudio(frame: ArrayBuffer): void {
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(frame);
  }

  close(): void {
    this.socket?.close();
    this.socket = null;
  }
}

/** ws(s)://<host>/ws for the page's own origin. */
export function sameOriginSocketUrl(location: Location): string {
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
}
