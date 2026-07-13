"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  appendMessage,
  getConversation,
  truncateConversation,
  updateMessage,
} from "../../lib/conversations.js";
import { streamChat } from "../../lib/api.js";
import MessageList from "./MessageList.jsx";
import Composer from "./Composer.jsx";

// Maps the streamed meta type onto the message `kind` the UI renders by.
const KIND_FOR_TYPE = { clarify: "clarify", refusal: "refusal", result: "result" };

// A completed itinerary's stored message content is just the short, warm
// confirmation line Bedrock voiced while building it (never the facts
// themselves — see the hard rule in conversation.py). That's fine for display,
// but it means a plain history replay loses the actual trip details. Splice a
// compact factual summary in for API calls only, so follow-ups ("make it 6
// days instead", "what about the food budget?") have something concrete to
// reference instead of re-deriving everything from the original free-text ask.
function historyContent(m) {
  if (m.kind !== "result" || !m.tripRequest) return m.content;
  const t = m.tripRequest;
  const days = m.itinerary?.num_days ?? m.itinerary?.daily_plans?.length;
  const facts = [
    `destination ${t.destination}`,
    t.start_date && t.end_date ? `${t.start_date} to ${t.end_date}` : null,
    days ? `${days} days` : null,
    `budget ${t.currency || "USD"} ${t.budget}`,
    `${t.travelers} traveler(s)`,
    `${t.pace} pace`,
    t.interests?.length ? `interests: ${t.interests.join(", ")}` : null,
    // record_id lets the backend fetch the exact previous itinerary on a
    // confirmed replan, so it can preserve everything not directly touched
    // by the request instead of regenerating the whole trip from scratch.
    m.recordId != null ? `record_id: ${m.recordId}` : null,
  ]
    .filter(Boolean)
    .join(", ");
  return `${m.content}\n\n[Itinerary already built for the traveller — ${facts}.]`;
}

export default function ChatWindow({
  activeId,
  ensureActiveConversation,
  bumpVersion,
  mounted,
  initialHasConversations,
  initialActiveStructure,
  streaming,
  stagingType,
  isBusy,
  startedAt,
  updateStreamState,
  registerStreamController,
  clearStreamController,
}) {
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [editingMessageId, setEditingMessageId] = useState(null);
  // How much bottom clearance the message list needs to keep the last message
  // scrollable clear of the floating composer — measured live rather than
  // guessed, since the composer's real height varies with quick replies, the
  // edit banner, and the textarea's own auto-grow.
  const [composerHeight, setComposerHeight] = useState(140);
  const [isAtBottom, setIsAtBottom] = useState(true);

  const activeIdRef = useRef(activeId);
  const composerRef = useRef(null);
  const composerWrapRef = useRef(null);
  const scrollContainerRef = useRef(null);

  const handleScrollStateChange = useCallback((atBottom) => {
    setIsAtBottom(atBottom);
  }, []);

  function scrollToBottom() {
    const el = scrollContainerRef.current;
    if (el) {
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    }
  }

  useEffect(() => {
    const el = composerWrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      setComposerHeight(entries[0].contentRect.height);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (activeIdRef.current === activeId) return;
    activeIdRef.current = activeId;
    setEditingMessageId(null);
    setMessages(activeId ? getConversation(activeId)?.messages || [] : []);
  }, [activeId]);

  function finalize(convId, msg) {
    const persisted = appendMessage(convId, msg);
    if (activeIdRef.current === convId && persisted) {
      setMessages((prev) => [...prev, persisted]);
    }
    bumpVersion();
  }

  async function runTurn(convId, historyForApi) {
    updateStreamState(convId, {
      isBusy: true,
      stagingType: null,
      streaming: { role: "assistant", content: "", kind: undefined },
      startedAt: Date.now(),
    });

    const acc = { content: "", type: null };
    const controller = new AbortController();
    registerStreamController(convId, controller);
    let settled = false;

    await streamChat(historyForApi, {
      signal: controller.signal,
      onMeta: (type) => {
        acc.type = type;
        updateStreamState(convId, {
          stagingType: type,
          streaming: { role: "assistant", content: acc.content, kind: KIND_FOR_TYPE[type] }
        });
      },
      onToken: (text) => {
        acc.content += text;
        updateStreamState(convId, {
          streaming: { role: "assistant", content: acc.content, kind: KIND_FOR_TYPE[acc.type] }
        });
      },
      onDone: (payload) => {
        settled = true;
        const content = payload.message ?? acc.content;
        if (payload.type === "result") {
          finalize(convId, {
            role: "assistant",
            kind: "result",
            content,
            itinerary: payload.itinerary || undefined,
            tripRequest: payload.trip_request || undefined,
            elapsedSeconds: payload.elapsed_seconds ?? undefined,
            recordId: payload.record_id ?? undefined,
          });
        } else if (payload.type === "clarify") {
          finalize(convId, {
            role: "assistant",
            kind: "clarify",
            content,
            quickReplies: (payload.quick_replies || []).map((q) => ({ label: q.label, value: q.value })),
          });
        } else {
          finalize(convId, { role: "assistant", kind: "refusal", content });
        }
        updateStreamState(convId, null);
        clearStreamController(convId);
      },
      onError: ({ message, networkError }) => {
        settled = true;
        finalize(convId, {
          role: "assistant",
          kind: "error",
          content: networkError
            ? "I couldn't reach the planning service — please check your connection and try again."
            : message || "Something went wrong. Please try again.",
        });
        updateStreamState(convId, null);
        clearStreamController(convId);
      },
    });

    // Stream closed without a terminal event (e.g. server dropped): surface it
    if (!settled && !controller.signal.aborted) {
      finalize(convId, {
        role: "assistant",
        kind: "error",
        content: "The connection closed before I finished — please try again.",
      });
      updateStreamState(convId, null);
      clearStreamController(convId);
    }
  }

  function handleSend(text) {
    if (isBusy) return;
    const convId = ensureActiveConversation();
    // Adopt a brand-new conversation immediately, before the `activeId` prop
    // has even re-rendered in — otherwise the prop-change effect below sees
    // it a moment later and aborts the turn this call is about to start.
    activeIdRef.current = convId;

    // Resending an edited message: only now — at send time, not at the
    // moment the pencil icon was clicked — do we actually drop the old tail.
    // Cancelling (Esc) before this point leaves everything untouched.
    if (editingMessageId) {
      truncateConversation(convId, editingMessageId);
      setEditingMessageId(null);
    }

    appendMessage(convId, { role: "user", content: text });
    setMessages(getConversation(convId)?.messages || []);
    setDraft("");
    bumpVersion();

    const history = (getConversation(convId)?.messages || []).map((m) => ({
      role: m.role,
      content: historyContent(m),
    }));
    runTurn(convId, history);
  }

  function handlePickQuickReply(messageId, value) {
    if (activeId) {
      updateMessage(activeId, messageId, { quickRepliesConsumed: true });
      setMessages((prev) => prev.map((m) => (m.id === messageId ? { ...m, quickRepliesConsumed: true } : m)));
    }
    handleSend(value);
  }

  // Edit a past user message: load its text back into the composer to revise.
  // Nothing is dropped yet — that only happens if the edit is actually sent
  // (see handleSend); pressing Esc or navigating away leaves history intact.
  function handleEditMessage(messageId) {
    if (!activeId || isBusy) return;
    const target = getConversation(activeId)?.messages.find((m) => m.id === messageId);
    if (!target) return;
    setEditingMessageId(messageId);
    setDraft(target.content);
    composerRef.current?.focus();
  }

  function handleCancelEdit() {
    setEditingMessageId(null);
    setDraft("");
  }

  // Regenerate: drop the given assistant reply and replay the preceding history.
  function handleRegenerate(messageId) {
    if (!activeId || isBusy) return;
    const conv = getConversation(activeId);
    if (!conv) return;
    const idx = conv.messages.findIndex((m) => m.id === messageId);
    if (idx === -1) return;
    const historyBefore = conv.messages.slice(0, idx).map((m) => ({ role: m.role, content: historyContent(m) }));
    truncateConversation(activeId, messageId);
    setMessages(getConversation(activeId)?.messages || []);
    bumpVersion();
    runTurn(activeId, historyBefore);
  }

  // Thumbs up/down — a local-only preference toggle; clicking the active choice
  // again clears it.
  function handleFeedback(messageId, value) {
    if (!activeId) return;
    const current = getConversation(activeId)?.messages.find((m) => m.id === messageId)?.feedback;
    const next = current === value ? null : value;
    updateMessage(activeId, messageId, { feedback: next });
    setMessages((prev) => prev.map((m) => (m.id === messageId ? { ...m, feedback: next } : m)));
  }

  // At most one clarify prompt is ever "live": the trailing assistant turn,
  // and only until its quick replies are consumed or streaming resumes.
  const lastMessage = messages[messages.length - 1];
  const activeClarify =
    !streaming && !isBusy && lastMessage?.role === "assistant" && lastMessage?.kind === "clarify" && !lastMessage?.quickRepliesConsumed
      ? lastMessage
      : null;

  return (
    // Composer floats over the message list (absolute, bottom-anchored) so
    // it can read as a translucent glass bar with content scrolling behind it
    // rather than a boxed panel that pushes the list up.
    <div className="relative flex h-full flex-1 flex-col overflow-hidden">
      <MessageList
        messages={messages}
        streamingMessage={streaming}
        stagingType={stagingType}
        startedAt={startedAt}
        disabled={isBusy}
        editingMessageId={editingMessageId}
        bottomClearance={composerHeight + 24}
        onPickStarter={handleSend}
        onEditMessage={handleEditMessage}
        onRegenerate={handleRegenerate}
        onFeedback={handleFeedback}
        scrollContainerRef={scrollContainerRef}
        onScrollStateChange={handleScrollStateChange}
        mounted={mounted}
        initialHasConversations={initialHasConversations}
        initialActiveStructure={initialActiveStructure}
      />
      <div ref={composerWrapRef} className="pointer-events-none absolute inset-x-0 bottom-0">
        <Composer
          value={draft}
          onChange={setDraft}
          onSend={handleSend}
          disabled={isBusy}
          inputRef={composerRef}
          isEditing={Boolean(editingMessageId)}
          onCancelEdit={handleCancelEdit}
          quickReplies={activeClarify?.quickReplies || []}
          onPickQuickReply={(value) => activeClarify && handlePickQuickReply(activeClarify.id, value)}
          isAtBottom={isAtBottom}
          onScrollToBottom={scrollToBottom}
        />
      </div>
    </div>
  );
}
