"use client";

// Server-backed chat history, scoped to the signed-in account. Replaces the
// old `lib/conversations.js` localStorage store, which had no per-user
// namespacing at all — every account signed into the same browser saw the
// exact same conversation list. The backend `/conversations` API already
// existed (correctly user-scoped) but nothing in the UI ever called it.
//
// A one-time migration folds in whatever was left in the legacy localStorage
// key on first load after sign-in, then clears it — see `migrateLocalHistory`.
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { conversations as api } from "./api.js";
import { useAuth } from "./AuthContext.jsx";

const ConversationsCtx = createContext(null);

const ACTIVE_ID_KEY = "bedrock:active_id";
const SESSION_ACTIVE_KEY = "bedrock:session_active";
const LEGACY_LOCAL_KEY = "bedrock:conversations";
const TITLE_MAX_LEN = 40;

function deriveTitle(firstUserMessage) {
  if (!firstUserMessage) return "New chat";
  const text = firstUserMessage.trim().replace(/\s+/g, " ");
  return text.length > TITLE_MAX_LEN ? `${text.slice(0, TITLE_MAX_LEN)}…` : text;
}

function toClientMessage(m) {
  return {
    id: m.id,
    role: m.role,
    content: m.content,
    kind: m.kind,
    seq: m.seq,
    itinerary: m.itinerary || undefined,
    tripRequest: m.trip_request || undefined,
    elapsedSeconds: m.elapsed_seconds ?? undefined,
    recordId: m.trip_record_id ?? undefined,
    quickReplies: m.quick_replies || undefined,
    // Only set while kind === "job" — a still-running (or orphaned)
    // background job the caller can reconnect to. See ChatWindow's reconnect
    // effect.
    jobId: m.job_id || undefined,
  };
}

export function ConversationsProvider({ children }) {
  const { user } = useAuth();
  const [list, setList] = useState([]);
  const [loadingList, setLoadingList] = useState(true);
  const [activeId, setActiveIdState] = useState(null);
  const [messagesByConv, setMessagesByConv] = useState({});
  const [loadingMessagesFor, setLoadingMessagesFor] = useState(null);
  const migratedRef = useRef(false);

  // `silent: true` re-fetches without flipping the loading flag. The flag
  // drives skeleton placeholders, which are right for a first load (there is
  // nothing else to show) but wrong for a background re-sync of data already
  // on screen: `mounted` in ChatWindow/Sidebar is derived from these flags, so
  // toggling one mid-session collapses the whole UI back to skeletons and then
  // restores it — a visible blink for however long the round trip takes. Use
  // it for any refetch that is reconciling state the user is already looking
  // at (see settle() in ClientPage).
  const refreshList = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setLoadingList(true);
    try {
      const rows = await api.list();
      setList(rows);
      return rows;
    } catch {
      if (!silent) setList([]);
      return [];
    } finally {
      if (!silent) setLoadingList(false);
    }
  }, []);

  // One-shot: fold any pre-account localStorage conversations into the new
  // account, then delete the local copy so this never runs again for real.
  // Safe to call every time a user becomes available — a no-op once the
  // legacy key is gone.
  const migrateLocalHistory = useCallback(async () => {
    if (typeof window === "undefined" || migratedRef.current) return;
    migratedRef.current = true;
    let raw;
    try {
      raw = window.localStorage.getItem(LEGACY_LOCAL_KEY);
    } catch {
      return;
    }
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw);
      const convs = Array.isArray(parsed.conversations) ? parsed.conversations : [];
      const payload = convs
        .filter((c) => c.messages?.length)
        .map((c) => ({
          title: c.title || "",
          messages: c.messages.map((m) => ({
            role: m.role,
            content: m.content || "",
            kind: m.kind || "text",
          })),
        }));
      if (payload.length) {
        let clientId = null;
        try {
          clientId = window.localStorage.getItem("bedrock:client_id");
        } catch {
          /* ignore */
        }
        await api.import({ conversations: payload, client_id: clientId });
      }
      window.localStorage.removeItem(LEGACY_LOCAL_KEY);
    } catch {
      // Best-effort — leave the local copy in place if the import failed, so
      // it can be retried on a future load rather than silently losing it.
      migratedRef.current = false;
    }
  }, []);

  const setActiveId = useCallback((id) => {
    setActiveIdState(id);
    if (typeof window === "undefined") return;
    if (id) window.sessionStorage.setItem(ACTIVE_ID_KEY, id);
    else window.sessionStorage.removeItem(ACTIVE_ID_KEY);
  }, []);

  // `silent: true` — same rationale as refreshList above: skip the loading
  // flag when re-reading messages the user is already looking at, so the
  // transcript doesn't blink through the Welcome/skeleton state mid-session.
  const loadMessages = useCallback(async (convId, { silent = false } = {}) => {
    if (!convId) return [];
    if (!silent) setLoadingMessagesFor(convId);
    try {
      const rows = await api.messages(convId);
      const msgs = rows.map(toClientMessage);
      setMessagesByConv((prev) => ({ ...prev, [convId]: msgs }));
      return msgs;
    } catch {
      return [];
    } finally {
      if (!silent) setLoadingMessagesFor((cur) => (cur === convId ? null : cur));
    }
  }, []);

  const selectConversation = useCallback(
    (id) => {
      setActiveId(id);
      if (id && !messagesByConv[id]) loadMessages(id);
    },
    [messagesByConv, loadMessages, setActiveId]
  );

  // Load the account's conversation list whenever we have a signed-in user —
  // covers a fresh login this session and a page reload with an existing one.
  useEffect(() => {
    if (!user) {
      setList([]);
      setMessagesByConv({});
      setActiveIdState(null);
      return;
    }
    (async () => {
      await migrateLocalHistory();
      const rows = await refreshList();

      // Same new-tab-vs-reload rule the old local store used: a brand-new
      // browser tab always starts on the Welcome screen; reloading the same
      // tab restores the last-open conversation — but only if it still
      // belongs to whoever is signed in now (guards against a stale id left
      // over from a different account in the same tab).
      const isSessionActive = window.sessionStorage.getItem(SESSION_ACTIVE_KEY) === "true";
      if (!isSessionActive) {
        window.sessionStorage.setItem(SESSION_ACTIVE_KEY, "true");
        setActiveId(null);
      } else {
        const savedId = window.sessionStorage.getItem(ACTIVE_ID_KEY);
        if (savedId && rows.some((c) => c.id === savedId)) {
          selectConversation(savedId);
        } else {
          setActiveId(null);
        }
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id]);

  const createConversation = useCallback(async (title = "") => {
    const conv = await api.create(title);
    setList((prev) => [conv, ...prev]);
    setMessagesByConv((prev) => ({ ...prev, [conv.id]: [] }));
    return conv;
  }, []);

  // Used when sending a message with no explicit "start a new chat" intent:
  // keep using whatever conversation is already active, and only create one
  // if there truly isn't one yet (fresh app load, Welcome screen).
  const ensureActiveConversation = useCallback(async () => {
    if (activeId) return activeId;
    const conv = await createConversation();
    setActiveId(conv.id);
    return conv.id;
  }, [activeId, createConversation, setActiveId]);

  // Used by the "+ New chat" button specifically: reuse the active
  // conversation if it's already empty (repeated clicks shouldn't pile up
  // empty rows), otherwise always start a fresh one — even if the active
  // conversation already has messages.
  const startNewChat = useCallback(async () => {
    if (activeId && (messagesByConv[activeId]?.length ?? 0) === 0) {
      return activeId;
    }
    const conv = await createConversation();
    setActiveId(conv.id);
    return conv.id;
  }, [activeId, messagesByConv, createConversation, setActiveId]);

  const appendMessage = useCallback(async (convId, msg) => {
    const persisted = await api.appendMessage(convId, {
      role: msg.role,
      content: msg.content || "",
      kind: msg.kind || "text",
      trip_record_id: msg.recordId ?? null,
    });
    const clientMsg = toClientMessage(persisted);
    setMessagesByConv((prev) => ({ ...prev, [convId]: [...(prev[convId] || []), clientMsg] }));
    setList((prev) => {
      const conv = prev.find((c) => c.id === convId);
      const now = new Date().toISOString();
      if (conv && (conv.title === "New chat" || !conv.title) && msg.role === "user") {
        const title = deriveTitle(msg.content);
        api.rename(convId, title).catch(() => {});
        return prev
          .map((c) => (c.id === convId ? { ...c, title, updated_at: now } : c))
          .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
      }
      return prev
        .map((c) => (c.id === convId ? { ...c, updated_at: now } : c))
        .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
    });
    return clientMsg;
  }, []);

  // quickRepliesConsumed / feedback are ephemeral UI state, not persisted —
  // they only ever matter for the trailing live turn in the current session.
  const updateMessageLocal = useCallback((convId, messageId, patch) => {
    setMessagesByConv((prev) => {
      const msgs = prev[convId];
      if (!msgs) return prev;
      return { ...prev, [convId]: msgs.map((m) => (m.id === messageId ? { ...m, ...patch } : m)) };
    });
  }, []);

  const truncateConversation = useCallback(async (convId, fromMessageId) => {
    await api.truncateFrom(convId, fromMessageId);
    setMessagesByConv((prev) => {
      const msgs = prev[convId] || [];
      const idx = msgs.findIndex((m) => m.id === fromMessageId);
      if (idx === -1) return prev;
      return { ...prev, [convId]: msgs.slice(0, idx) };
    });
  }, []);

  const renameConversation = useCallback(async (convId, title) => {
    await api.rename(convId, title);
    setList((prev) => prev.map((c) => (c.id === convId ? { ...c, title } : c)));
  }, []);

  const deleteConversation = useCallback(
    async (convId) => {
      await api.remove(convId).catch(() => {});
      setList((prev) => prev.filter((c) => c.id !== convId));
      setMessagesByConv((prev) => {
        const next = { ...prev };
        delete next[convId];
        return next;
      });
      if (activeId === convId) {
        setActiveId(null);
      }
    },
    [activeId, setActiveId]
  );

  const clearAll = useCallback(async () => {
    await api.clearAll().catch(() => {});
    setList([]);
    setMessagesByConv({});
    setActiveId(null);
  }, [setActiveId]);

  const value = {
    conversations: list,
    loadingList,
    activeId,
    selectConversation,
    activeMessages: activeId ? messagesByConv[activeId] || [] : [],
    activeMessagesLoading: Boolean(activeId) && loadingMessagesFor === activeId,
    ensureActiveConversation,
    startNewChat,
    refreshList,
    loadMessages,
    appendMessage,
    updateMessageLocal,
    truncateConversation,
    renameConversation,
    deleteConversation,
    clearAll,
  };

  return <ConversationsCtx.Provider value={value}>{children}</ConversationsCtx.Provider>;
}

export function useConversations() {
  const ctx = useContext(ConversationsCtx);
  if (!ctx) throw new Error("useConversations must be used within a ConversationsProvider");
  return ctx;
}
