import { DurableObject } from "cloudflare:workers";

// How many frames a viewer can fall behind before we start skipping frames for them.
// Viewers acknowledge every ACK_EVERY frames (see public/index.html), so this must be larger.
const WINDOW = 10;

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (pathname !== "/ws" && pathname !== "/ingest") {
      return new Response("Not found", { status: 404 });
    }
    if (request.headers.get("Upgrade") !== "websocket") {
      return new Response("Expected a WebSocket", { status: 426 });
    }
    // Only the Pi may publish frames, otherwise anyone could take over the stream
    if (pathname === "/ingest" && !(await isCamera(request, env))) {
      return new Response("Forbidden", { status: 403 });
    }
    // Every connection goes to the same Durable Object, which copies frames to all viewers
    return env.SHOW.get(env.SHOW.idFromName("show")).fetch(request);
  },
};

async function isCamera(request, env) {
  if (!env.INGEST_KEY) return false;
  const given = request.headers.get("Authorization") ?? "";
  // Hash both sides so they're the same length, then compare in constant time
  const encoder = new TextEncoder();
  const [a, b] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(given)),
    crypto.subtle.digest("SHA-256", encoder.encode(`Bearer ${env.INGEST_KEY}`)),
  ]);
  return crypto.subtle.timingSafeEqual(a, b);
}

export class Show extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.latest = null;
  }

  async fetch(request) {
    const role = new URL(request.url).pathname === "/ingest" ? "camera" : "viewer";
    const [client, server] = Object.values(new WebSocketPair());

    if (role === "camera") {
      for (const old of this.ctx.getWebSockets("camera")) {
        old.close(1000, "Replaced by a new camera connection");
      }
    }
    // The hibernation API lets Cloudflare sleep the object while nothing is happening
    this.ctx.acceptWebSocket(server, [role]);
    if (role === "viewer") {
      server.serializeAttachment({ unacked: 0 });
      if (this.latest) this.sendFrame(server, this.latest);
    }
    this.broadcastStatus();
    return new Response(null, { status: 101, webSocket: client });
  }

  webSocketMessage(ws, message) {
    if (this.ctx.getTags(ws).includes("camera")) {
      if (typeof message === "string") return;
      this.latest = message;
      for (const viewer of this.ctx.getWebSockets("viewer")) {
        if (viewer.deserializeAttachment().unacked < WINDOW) this.sendFrame(viewer, message);
      }
      return;
    }
    // Viewers acknowledge frames as they arrive, so a slow connection skips frames
    // instead of piling them up in memory here
    const state = ws.deserializeAttachment();
    state.unacked = Math.max(0, state.unacked - (parseInt(message, 10) || 0));
    ws.serializeAttachment(state);
  }

  webSocketClose(ws) {
    try {
      ws.close(1000);
    } catch {
      // already closed
    }
    this.broadcastStatus(ws);
  }

  webSocketError(ws) {
    this.broadcastStatus(ws);
  }

  sendFrame(viewer, frame) {
    try {
      viewer.send(frame);
      const state = viewer.deserializeAttachment();
      state.unacked += 1;
      viewer.serializeAttachment(state);
    } catch {
      // the viewer disconnected; webSocketClose cleans up
    }
  }

  broadcastStatus(closing = null) {
    const open = (tag) => this.ctx.getWebSockets(tag).filter((ws) => ws !== closing);
    const viewers = open("viewer");
    const live = open("camera").length > 0;
    if (!live) this.latest = null;
    const message = JSON.stringify({ live, viewers: viewers.length });
    for (const viewer of viewers) {
      try {
        viewer.send(message);
      } catch {
        // the viewer disconnected; webSocketClose cleans up
      }
    }
  }
}
