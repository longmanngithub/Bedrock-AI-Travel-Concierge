// Client-side conversation persistence for the Bedrock chat UI. No accounts,
// no server session — every conversation and its full message history lives in
// localStorage, keyed by a generated id. The backend /chat endpoints are
// stateless and simply receive the full transcript on every turn.
//
// Every accessor is SSR-safe: during Next.js server rendering there is no
// `window`, so reads return empty state and writes no-op until hydration.
const STORAGE_KEY = "bedrock:conversations";
const TITLE_MAX_LEN = 40;

const hasWindow = () => typeof window !== "undefined";

function updateConversationsCookies(conversations, activeId) {
  if (typeof document !== "undefined") {
    const hasConv = conversations && conversations.length > 0;
    const count = conversations ? conversations.length : 0;
    const activeConv = conversations ? conversations.find((c) => c.id === activeId) : null;
    const structureStr = activeConv
      ? activeConv.messages.map((m) => `${m.role[0]}:${(m.content || "").length}`).join(",")
      : "";

    document.cookie = `bedrock_has_conversations=${hasConv}; path=/; SameSite=Lax`;
    document.cookie = `bedrock_conversation_count=${count}; path=/; SameSite=Lax`;
    document.cookie = `bedrock_active_conv_structure=${structureStr}; path=/; SameSite=Lax`;
  }
}

function readState() {
  if (!hasWindow()) return { conversations: [], activeId: null };
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return { conversations: [], activeId: null };
    const parsed = JSON.parse(raw);
    const state = {
      conversations: Array.isArray(parsed.conversations) ? parsed.conversations : [],
      activeId: parsed.activeId ?? null,
    };
    updateConversationsCookies(state.conversations, state.activeId);
    return state;
  } catch {
    return { conversations: [], activeId: null };
  }
}

function writeState(state) {
  if (!hasWindow()) return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  updateConversationsCookies(state.conversations, state.activeId);
}

function makeId(prefix) {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function deriveTitle(firstUserMessage) {
  if (!firstUserMessage) return "New chat";
  const text = firstUserMessage.trim().replace(/\s+/g, " ");
  return text.length > TITLE_MAX_LEN ? `${text.slice(0, TITLE_MAX_LEN)}…` : text;
}

export function listConversations() {
  return readState().conversations.slice().sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

export function getConversation(id) {
  return readState().conversations.find((c) => c.id === id) || null;
}

export function getActiveId() {
  return readState().activeId;
}

export function setActiveId(id) {
  const state = readState();
  state.activeId = id;
  writeState(state);
}

export function createConversation() {
  const now = new Date().toISOString();
  const conversation = { id: makeId("conv"), title: "New chat", createdAt: now, updatedAt: now, messages: [] };
  const state = readState();
  state.conversations.push(conversation);
  state.activeId = conversation.id;
  writeState(state);
  return conversation;
}

export function appendMessage(id, message) {
  const state = readState();
  const conversation = state.conversations.find((c) => c.id === id);
  if (!conversation) return null;
  const withId = { id: message.id || makeId("msg"), ...message };
  conversation.messages.push(withId);
  conversation.updatedAt = new Date().toISOString();
  if (conversation.title === "New chat" && message.role === "user") {
    conversation.title = deriveTitle(message.content);
  }
  writeState(state);
  return withId;
}

export function updateMessage(conversationId, messageId, patch) {
  const state = readState();
  const conversation = state.conversations.find((c) => c.id === conversationId);
  if (!conversation) return;
  const message = conversation.messages.find((m) => m.id === messageId);
  if (!message) return;
  Object.assign(message, patch);
  writeState(state);
}

export function truncateConversation(id, fromMessageId) {
  const state = readState();
  const conversation = state.conversations.find((c) => c.id === id);
  if (!conversation) return;
  const idx = conversation.messages.findIndex((m) => m.id === fromMessageId);
  if (idx === -1) return;
  conversation.messages = conversation.messages.slice(0, idx);
  conversation.updatedAt = new Date().toISOString();
  writeState(state);
}

export function deleteConversation(id) {
  const state = readState();
  state.conversations = state.conversations.filter((c) => c.id !== id);
  if (state.activeId === id) {
    if (state.conversations.length > 0) {
      const sorted = state.conversations.slice().sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
      state.activeId = sorted[0].id;
    } else {
      state.activeId = null;
    }
  }
  writeState(state);
}

export function clearAllConversations() {
  writeState({ conversations: [], activeId: null });
}
