// Streaming API client for the FastAPI backend. Consumes the /chat/stream
// Server-Sent-Events endpoint, which emits (in order):
//   {kind:"meta",  type:"clarify"|"refusal"|"result"}  → pick the placeholder
//   {kind:"token", text:"..."}                          → reply, streamed
//   {kind:"done",  ...settled turn...}                  → final payload
//   {kind:"error", message:"..."}                       → turn-level failure
const BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const CLIENT_ID_KEY = "bedrock:client_id";

// A per-browser, unguessable (not secret — just unpredictable) id, generated
// once and reused for every request. The backend uses it only to scope "look
// up my own previously-built itinerary" replan lookups to this browser, so
// one client can't read/revise another client's trip by guessing a numeric
// record id — see backend/app/main.py:_fetch_previous_itinerary. Not an auth
// credential: it grants no privilege beyond "this is the same browser that
// built record X".
function getClientId() {
  if (typeof window === "undefined") return null;
  try {
    let id = window.localStorage.getItem(CLIENT_ID_KEY);
    if (!id) {
      id = crypto.randomUUID();
      window.localStorage.setItem(CLIENT_ID_KEY, id);
    }
    return id;
  } catch {
    return null; // storage unavailable (private mode, etc.) — degrade to no ownership scoping
  }
}

export async function streamChat(messages, { signal, onMeta, onToken, onDone, onError } = {}) {
  let res;
  try {
    res = await fetch(`${BASE_URL}/chat/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        // Only sent when the deployment opts into the backend's optional
        // API_KEY gate (unset by default — see .env.example).
        ...(process.env.NEXT_PUBLIC_API_KEY
          ? { "X-API-Key": process.env.NEXT_PUBLIC_API_KEY }
          : {}),
      },
      body: JSON.stringify({
        messages: messages.map(({ role, content }) => ({ role, content })),
        client_id: getClientId(),
      }),
      signal,
    });
  } catch (e) {
    if (e?.name === "AbortError") return;
    onError?.({ message: "network", networkError: true });
    return;
  }

  if (!res.ok || !res.body) {
    let detail = "";
    try {
      detail = (await res.json())?.detail || "";
    } catch {
      /* non-JSON error body — fall through to the status code message */
    }
    onError?.({ message: detail || `Request failed (${res.status})` });
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const dispatch = (evt) => {
    switch (evt.kind) {
      case "meta":
        onMeta?.(evt.type);
        break;
      case "token":
        onToken?.(evt.text || "");
        break;
      case "done":
        onDone?.(evt);
        break;
      case "error":
        onError?.({ message: evt.message || "Something went wrong." });
        break;
      default:
        break;
    }
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // SSE frames are separated by a blank line.
      let sep;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const dataLine = frame.split("\n").find((l) => l.startsWith("data:"));
        if (!dataLine) continue;
        const payload = dataLine.slice(5).trim();
        if (!payload) continue;
        try {
          dispatch(JSON.parse(payload));
        } catch {
          /* ignore a malformed frame rather than aborting the whole stream */
        }
      }
    }
  } catch (e) {
    if (e?.name === "AbortError") return;
    onError?.({ message: e?.message || "The connection was interrupted." });
  }
}
