// API client for the FastAPI backend.
//
// Two transports:
//   * streamChat()   — POST + ReadableStream reader for /chat/stream. A
//     plannable turn now ends in a {kind:"job"} frame (the crew runs in the
//     background); the caller then subscribes to the job's progress stream.
//   * subscribeJob() — EventSource on /jobs/{id}/events. Resumable: the browser
//     auto-sends Last-Event-ID on reconnect, so closing the tab and coming back
//     re-attaches to a running job with zero client bookkeeping.
//
// Auth is cookie-based (httpOnly), so every request sends credentials and every
// state-changing request echoes the double-submit CSRF token.
const BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export function assetUrl(path) {
  if (!path || /^https?:\/\//i.test(path)) return path || null;
  return `${BASE_URL}${path}`;
}
const CLIENT_ID_KEY = "bedrock:client_id";

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
    return null;
  }
}

// Opt-in, reverse-geocoded "City, Country" set by AuthContext once the user
// has enabled location sharing and the browser has granted permission (see
// AuthContext.jsx). A module-level setter rather than a param threaded
// through every call site, mirroring setAuthLostHandler below — AuthContext
// can't import this module's React-consuming callers, so it pushes state in.
let _locationHint = null;
export function setLocationHint(hint) {
  _locationHint = hint || null;
}

// Read the non-httpOnly CSRF cookie the backend set, to echo in a header.
function csrfToken() {
  if (typeof document === "undefined") return "";
  const m = document.cookie.match(/(?:^|;\s*)ac_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

// Turn a backend error payload ({code, message, retryable, request_id}) into a
// single safe display string. The backend never leaks exception text; we show
// its safe message plus the code + request id for support.
export function formatError(err) {
  if (!err) return "Something went wrong. Please try again.";
  const base = err.message || "Something went wrong. Please try again.";
  const bits = [];
  if (err.code) bits.push(err.code);
  if (err.request_id) bits.push(`req ${String(err.request_id).slice(0, 8)}`);
  return bits.length ? `${base} (${bits.join(" · ")})` : base;
}

// ---- Silent auth refresh ----------------------------------------------------
// The access cookie is short-lived (10 min) by design; the refresh cookie
// (30 days) exists precisely so the app never has to force a full re-login
// just because the user was reading an itinerary for a while. AuthContext
// registers a handler here (avoids a circular import) so a refresh that
// itself fails — the refresh token expired or was revoked, the actual
// "you're logged out" case — can drop the session and re-show the gate.
let _onAuthLost = null;
export function setAuthLostHandler(fn) {
  _onAuthLost = fn;
}

const _AUTH_ERROR_CODES = new Set(["E_AUTH_REQUIRED", "E_AUTH_EXPIRED"]);
// Never intercept these — refreshing to retry a login/register/refresh call
// itself would loop.
const _NO_REFRESH_PATHS = new Set(["/auth/refresh", "/auth/login", "/auth/register", "/auth/logout"]);

// De-duplicated: concurrent 401s from several in-flight requests trigger ONE
// refresh call, not one each.
let _refreshInFlight = null;
function refreshOnce() {
  if (!_refreshInFlight) {
    _refreshInFlight = rawFetch("/auth/refresh", { method: "POST" })
      .then(() => true)
      .catch(() => false)
      .finally(() => {
        _refreshInFlight = null;
      });
  }
  return _refreshInFlight;
}

// The actual fetch, with no retry logic — refreshOnce() calls this directly
// so refreshing never recurses into the retry path above it.
async function rawFetch(path, { method = "GET", body, signal } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (method !== "GET" && method !== "HEAD") headers["X-CSRF-Token"] = csrfToken();
  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    headers,
    credentials: "include",
    body: body ? JSON.stringify(body) : undefined,
    signal,
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* empty/non-JSON body */
  }
  if (!res.ok) {
    const err = data && data.code ? data : { code: "E_INTERNAL", message: "Something went wrong. Please try again." };
    err.status = res.status;
    throw err;
  }
  return data;
}

async function apiFetch(path, opts = {}, _retried = false) {
  try {
    return await rawFetch(path, opts);
  } catch (err) {
    const canRetry = !_retried && _AUTH_ERROR_CODES.has(err?.code) && !_NO_REFRESH_PATHS.has(path);
    if (!canRetry) {
      if (_AUTH_ERROR_CODES.has(err?.code)) _onAuthLost?.();
      throw err;
    }
    const refreshed = await refreshOnce();
    if (!refreshed) {
      _onAuthLost?.();
      throw err;
    }
    return apiFetch(path, opts, true);
  }
}

async function apiFormFetch(path, formData) {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "X-CSRF-Token": csrfToken() },
    body: formData,
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty/non-JSON */ }
  if (!res.ok) {
    const err = data && data.code ? data : { code: "E_INTERNAL", message: "Something went wrong. Please try again." };
    err.status = res.status;
    throw err;
  }
  return data;
}

// ---- Auth -----------------------------------------------------------------
export const auth = {
  me: () => apiFetch("/auth/me"),
  register: (email, password, display_name) =>
    apiFetch("/auth/register", { method: "POST", body: { email, password, display_name } }),
  verifyEmail: (email, code) => apiFetch("/auth/verify-email", { method: "POST", body: { email, code } }),
  resendVerification: (email) => apiFetch("/auth/verify-email/resend", { method: "POST", body: { email } }),
  login: (email, password) => apiFetch("/auth/login", { method: "POST", body: { email, password } }),
  changePassword: (current_password, new_password, confirm_new_password) =>
    apiFetch("/auth/password", { method: "POST", body: { current_password, new_password, confirm_new_password } }),
  logout: () => apiFetch("/auth/logout", { method: "POST" }),
  refresh: () => apiFetch("/auth/refresh", { method: "POST" }),
  googleStartUrl: (intent = "login") => `${BASE_URL}/auth/google/start?intent=${intent}`,
  unlinkGoogle: () => apiFetch("/auth/google/unlink", { method: "POST" }),
};

// ---- Conversations --------------------------------------------------------
export const conversations = {
  list: () => apiFetch("/conversations"),
  create: (title = "") => apiFetch("/conversations", { method: "POST", body: { title } }),
  messages: (id) => apiFetch(`/conversations/${id}/messages`),
  appendMessage: (id, msg) => apiFetch(`/conversations/${id}/messages`, { method: "POST", body: msg }),
  // Deletes fromMessageId and everything after it (by seq) — backs edit/regenerate.
  truncateFrom: (id, messageId) => apiFetch(`/conversations/${id}/messages/${messageId}`, { method: "DELETE" }),
  rename: (id, title) => apiFetch(`/conversations/${id}`, { method: "PATCH", body: { title } }),
  remove: (id) => apiFetch(`/conversations/${id}`, { method: "DELETE" }),
  clearAll: () => apiFetch("/conversations", { method: "DELETE" }),
  import: (payload) => apiFetch("/conversations/import", { method: "POST", body: payload }),
};

// ---- Jobs -----------------------------------------------------------------
export const jobs = {
  list: (status) => apiFetch(`/jobs${status ? `?status=${status}` : ""}`),
  get: (id) => apiFetch(`/jobs/${id}`),
  cancel: (id) => apiFetch(`/jobs/${id}/cancel`, { method: "POST" }),
};

// ---- Itineraries (delivery) ------------------------------------------------
export const itineraries = {
  deliver: (recordId, channel) =>
    apiFetch(`/itineraries/${recordId}/deliver`, { method: "POST", body: { channel } }),
  deliveryStatus: (recordId) => apiFetch(`/itineraries/${recordId}/delivery-status`),
};

// ---- Account settings (memory, Telegram) -----------------------------------
export const settings = {
  setMemoryOptIn: (optIn) => apiFetch("/me/memory", { method: "PATCH", body: { opt_in: optIn } }),
  setCompletionEmailOptIn: (optIn) => apiFetch("/me/completion-email", { method: "PATCH", body: { opt_in: optIn } }),
  setLocationSharingOptIn: (optIn) => apiFetch("/me/location-sharing", { method: "PATCH", body: { opt_in: optIn } }),
  deleteMemory: () => apiFetch("/me/memory", { method: "DELETE" }),
  uploadAvatar: (file) => {
    const body = new FormData();
    body.append("file", file);
    return apiFormFetch("/me/avatar", body);
  },
  deleteAvatar: () => apiFetch("/me/avatar", { method: "DELETE" }),
  telegramLinkCode: () => apiFetch("/me/telegram/link-code", { method: "POST" }),
  telegramUnlink: () => apiFetch("/me/telegram/unlink", { method: "POST" }),
};

// ---- Location sharing (opt-in flight-origin detection) --------------------
export const geo = {
  reverseGeocode: (lat, lon) => apiFetch("/geo/reverse", { method: "POST", body: { lat, lon } }),
};

// Subscribe to a job's resumable progress stream. Returns an unsubscribe fn.
export function subscribeJob(jobId, { onSnapshot, onStep, onActivity, onDone, onError } = {}) {
  const es = new EventSource(`${BASE_URL}/jobs/${jobId}/events`, { withCredentials: true });
  let settled = false;
  es.onmessage = (ev) => {
    let frame;
    try {
      frame = JSON.parse(ev.data);
    } catch {
      return;
    }
    switch (frame.kind) {
      case "snapshot":
        onSnapshot?.(frame);
        break;
      case "step":
        onStep?.(frame);
        break;
      case "activity":
        onActivity?.(frame);
        break;
      case "done":
        settled = true;
        es.close();
        onDone?.(frame);
        break;
      case "cancelled":
        settled = true;
        es.close();
        onError?.({ code: "E_JOB_CANCELLED", message: "This plan was cancelled." });
        break;
      case "error":
        settled = true;
        es.close();
        onError?.(frame);
        break;
      default:
        break;
    }
  };
  es.onerror = () => {
    // EventSource auto-reconnects with Last-Event-ID; only surface an error if
    // the connection is permanently closed and the job never settled.
    if (es.readyState === EventSource.CLOSED && !settled) {
      onError?.({ code: "E_INTERNAL", message: "The connection was interrupted. Reconnecting…" });
    }
  };
  return () => {
    settled = true;
    es.close();
  };
}

// ---- Chat stream ----------------------------------------------------------
function _postChatStream(messages, conversationId, idempotencyKey, signal) {
  return fetch(`${BASE_URL}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
    credentials: "include",
    body: JSON.stringify({
      messages: messages.map(({ role, content }) => ({ role, content })),
      client_id: getClientId(),
      conversation_id: conversationId || null,
      idempotency_key: idempotencyKey || (typeof crypto !== "undefined" ? crypto.randomUUID() : null),
      location_hint: _locationHint,
    }),
    signal,
  });
}

export async function streamChat(
  messages,
  { signal, conversationId, idempotencyKey, onMeta, onToken, onDone, onJob, onError } = {}
) {
  let res;
  try {
    res = await _postChatStream(messages, conversationId, idempotencyKey, signal);
    // The access cookie is short-lived by design (see api.js's silent-refresh
    // comment) — a turn started after 10+ idle minutes shouldn't force a full
    // re-login. Refresh once and retry the POST before giving up.
    if (res.status === 401) {
      const refreshed = await refreshOnce();
      if (refreshed) res = await _postChatStream(messages, conversationId, idempotencyKey, signal);
    }
  } catch (e) {
    if (e?.name === "AbortError") return;
    onError?.({ code: "E_INTERNAL", message: "Something went wrong. Please try again.", networkError: true });
    return;
  }

  if (!res.ok || !res.body) {
    let data = null;
    try {
      data = await res.json();
    } catch {
      /* non-JSON */
    }
    const err = data && data.code ? data : { code: "E_INTERNAL", message: "Something went wrong. Please try again." };
    if (_AUTH_ERROR_CODES.has(err.code)) _onAuthLost?.();
    onError?.(err);
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
      case "job":
        onJob?.(evt);
        break;
      case "done":
        onDone?.(evt);
        break;
      case "error":
        onError?.(evt);
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
          /* ignore a malformed frame */
        }
      }
    }
  } catch (e) {
    if (e?.name === "AbortError") return;
    onError?.({ code: "E_INTERNAL", message: "The connection was interrupted." });
  }
}
