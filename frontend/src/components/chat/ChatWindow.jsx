"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { streamChat, formatError } from "../../lib/api.js";
import { createReplyReveal } from "../../lib/replyReveal.mjs";
import { useAuth } from "../../lib/AuthContext.jsx";
import { useConversations } from "../../lib/ConversationsContext.jsx";
import MessageList from "./MessageList.jsx";
import Composer from "./Composer.jsx";

// Maps the streamed meta type onto the message `kind` the UI renders by.
const KIND_FOR_TYPE = { clarify: "clarify", refusal: "refusal", result: "result" };
const UNSAVED_ERROR_ID = "unsaved-chat-error";

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
  streaming,
  stagingType,
  streamError,
  submissionError,
  isBusy,
  startedAt,
  updateStreamState,
  registerStreamController,
  clearStreamController,
  attachJob,
  onCancel,
}) {
  const { user, loading: authLoading, forceReauth } = useAuth();
  const {
    activeId,
    activeMessages: messages,
    activeMessagesLoading,
    loadingList,
    ensureActiveConversation,
    appendMessage,
    updateMessageLocal,
    truncateConversation,
    loadMessages,
    refreshList,
  } = useConversations();
  const [draft, setDraft] = useState("");
  const [editingMessageId, setEditingMessageId] = useState(null);
  // How much bottom clearance the message list needs to keep the last message
  // scrollable clear of the floating composer — measured live rather than
  // guessed, since the composer's real height varies with quick replies, the
  // edit banner, and the textarea's own auto-grow.
  const [composerHeight, setComposerHeight] = useState(140);
  const [isAtBottom, setIsAtBottom] = useState(true);

  const composerRef = useRef(null);
  const composerWrapRef = useRef(null);
  const scrollContainerRef = useRef(null);
  const pendingActions = useRef(new Set());
  const completedReveals = useRef(new Map());

  // handleSend is a plain function, recreated every render, closing over
  // whatever `messages` that render saw. A click always *fires* against the
  // freshest render's onClick handler in practice, but a background job's
  // onDone can append a message and immediately re-render while a user
  // click is already in flight, or the click can land on a handler from a
  // render that hasn't picked up the very latest context update yet — either
  // way, reading a stale snapshot of `messages` here means the confirmation-
  // gate marker (embedded in the result message's history text — see
  // historyContent) silently isn't in the history sent to /chat/stream, so
  // "yes, finalize it" gets misread as a fresh, itinerary-less turn instead
  // of a confirmed replan. A ref sidesteps closure staleness entirely: it's
  // mutated directly, not tied to which render's closure got invoked.
  const messagesRef = useRef(messages);
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

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
    setEditingMessageId(null);
  }, [activeId]);

  async function runTurn(convId, historyForApi, idempotencyKey = globalThis.crypto?.randomUUID?.()) {
    updateStreamState(convId, {
      isBusy: true,
      stagingType: null,
      error: null,
      submissionError: null,
      streaming: { role: "assistant", content: "", kind: undefined },
      startedAt: Date.now(),
    });

    const acc = { content: "", type: null };
    const controller = new AbortController();
    const reveal = createReplyReveal((content) => {
      updateStreamState(convId, {
        streaming: { role: "assistant", content, kind: KIND_FOR_TYPE[acc.type] },
      });
    }, controller.signal);
    registerStreamController(convId, controller);
    let terminal;
    let handedOff = false;
    let unsavedError = null;

    try {
      await streamChat(historyForApi, {
        signal: controller.signal,
        conversationId: convId,
        idempotencyKey,
        onMeta: (type) => {
          acc.type = type;
          updateStreamState(convId, {
            stagingType: type,
            streaming: { role: "assistant", content: acc.content, kind: KIND_FOR_TYPE[type] },
          });
        },
        onToken: (text) => {
          acc.content += text;
          reveal.append(text, acc.type === "result");
        },
        onJob: (payload) => { terminal ??= { kind: "job", payload }; },
        onDone: () => {
          terminal ??= { kind: "done" };
          if (terminal.kind === "done") completedReveals.current.set(convId, reveal);
        },
        onError: (payload) => { terminal ??= { kind: "error", payload }; },
      });

      if (controller.signal.aborted) return;
      if (terminal?.kind === "job") {
        // A plannable turn: the crew runs in the BACKGROUND, and the backend
        // has already persisted its placeholder message.
        clearStreamController(convId, controller);
        attachJob(convId, terminal.payload.job_id, terminal.payload.message ?? acc.content);
        handedOff = true;
        return;
      }
      if (terminal?.kind === "done") {
        await reveal.drain();
        if (controller.signal.aborted) return;
        // The backend owns completed assistant messages so suggested replies,
        // notification delivery, and reload behavior all share one durable row.
        // Silent: this reconciles a transcript already on screen, so it must
        // not flip the loading flags and blink the UI back to skeletons.
        await Promise.all([
          loadMessages(convId, { silent: true }),
          refreshList({ silent: true }),
        ]);
        return;
      }

      reveal.dispose();
      const err = terminal?.payload;
      if (err?.code === "E_AUTH_REQUIRED" || err?.code === "E_AUTH_EXPIRED") {
        forceReauth();
        return;
      }
      const message = {
        role: "assistant",
        kind: "error",
        content: !terminal
          ? "The connection closed before I finished. Please try again."
          : err?.networkError
            ? "I couldn't reach the planning service. Please check your connection and try again."
            : formatError(err),
      };
      try {
        await appendMessage(convId, message);
      } catch {
        unsavedError = { message: { ...message, id: UNSAVED_ERROR_ID }, historyForApi, idempotencyKey };
      }
    } finally {
      reveal.dispose();
      if (completedReveals.current.get(convId) === reveal) {
        completedReveals.current.delete(convId);
      }
      if (!handedOff && !controller.signal.aborted) {
        clearStreamController(convId, controller);
        updateStreamState(convId, unsavedError ? {
          isBusy: false,
          startedAt: null,
          stagingType: null,
          streaming: null,
          error: unsavedError,
        } : null);
      }
    }
  }

  async function handleSend(text) {
    const action = activeId ?? null;
    if (isBusy || pendingActions.current.has(action)) return;
    // Defense in depth only — the app-wide gate in AuthProvider already
    // makes this unreachable while signed out (a non-dismissible dialog
    // covers the whole screen until login/register succeeds).
    if (authLoading || !user) return;

    // Snapshot before any await — `ensureActiveConversation` either returns
    // the still-current activeId (this snapshot stays valid) or creates a
    // brand-new one (in which case there were no prior messages anyway).
    // Read from the ref, not the closed-over `messages` — see messagesRef's
    // own comment for why the closure can be stale.
    let baseMessages = messagesRef.current;
    let convId = activeId;
    pendingActions.current.add(action);
    try {
      convId = await ensureActiveConversation();
      pendingActions.current.add(convId);

      // Drop the old tail only when the edited message is submitted.
      if (editingMessageId) {
        await truncateConversation(convId, editingMessageId);
        const idx = baseMessages.findIndex((m) => m.id === editingMessageId);
        if (idx !== -1) baseMessages = baseMessages.slice(0, idx);
        setEditingMessageId(null);
      }

      const userMsg = await appendMessage(convId, { role: "user", content: text });
      setDraft("");
      const history = [...baseMessages, userMsg].map((m) => ({
        role: m.role,
        content: historyContent(m),
      }));
      await runTurn(convId, history);
    } catch {
      updateStreamState(convId ?? null, {
        submissionError: "Couldn't send your message. Please try again.",
      });
    } finally {
      pendingActions.current.delete(action);
      pendingActions.current.delete(convId);
    }
  }

  function handlePickQuickReply(messageId, value) {
    if (activeId) {
      updateMessageLocal(activeId, messageId, { quickRepliesConsumed: true });
    }
    handleSend(value);
  }

  // Edit a past user message: load its text back into the composer to revise.
  // Nothing is dropped yet — that only happens if the edit is actually sent
  // (see handleSend); pressing Esc or navigating away leaves history intact.
  function handleEditMessage(messageId) {
    if (!activeId || isBusy) return;
    const target = messages.find((m) => m.id === messageId);
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
  async function handleRegenerate(messageId) {
    if (!activeId || isBusy || pendingActions.current.has(activeId)) return;
    pendingActions.current.add(activeId);
    try {
      if (messageId === UNSAVED_ERROR_ID && streamError) {
        await runTurn(activeId, streamError.historyForApi, streamError.idempotencyKey);
        return;
      }
      const current = messagesRef.current;
      const idx = current.findIndex((m) => m.id === messageId);
      if (idx === -1) return;
      const historyBefore = current.slice(0, idx).map((m) => ({ role: m.role, content: historyContent(m) }));
      await truncateConversation(activeId, messageId);
      await runTurn(activeId, historyBefore);
    } catch {
      updateStreamState(activeId, { submissionError: "Couldn't retry this response. Please try again." });
    } finally {
      pendingActions.current.delete(activeId);
    }
  }

  // Thumbs up/down — a local-only preference toggle; clicking the active choice
  // again clears it.
  function handleFeedback(messageId, value) {
    if (!activeId) return;
    const current = messages.find((m) => m.id === messageId)?.feedback;
    const next = current === value ? null : value;
    updateMessageLocal(activeId, messageId, { feedback: next });
  }

  const lastMessage = messages[messages.length - 1];
  const activeSuggestions =
    !streaming && !streamError && !isBusy && lastMessage?.role === "assistant" && Array.isArray(lastMessage?.quickReplies) &&
    lastMessage.quickReplies.length > 0 && !lastMessage?.quickRepliesConsumed
      ? lastMessage
      : null;

  async function handleStop() {
    const reveal = completedReveals.current.get(activeId);
    if (reveal) {
      reveal.finish();
      return;
    }
    // Direct HTTP stream aborts do not reach a terminal backend frame. Persist
    // the requested feedback locally; queued jobs persist their own stopped
    // placeholder through the cancellation endpoint.
    if (activeId && !streaming?.jobId) {
      await appendMessage(activeId, {
        role: "assistant",
        kind: "stopped",
        content: "You stopped this response.",
      });
    }
    onCancel?.();
  }

  // "Mounted" here means: we know the real conversation list AND, if one is
  // active, its messages have finished loading — otherwise MessageList would
  // briefly flash the Welcome screen before a non-empty history pops in.
  const mounted = !authLoading && !loadingList && !activeMessagesLoading;

  return (
    // Composer floats over the message list (absolute, bottom-anchored) so
    // it can read as a translucent glass bar with content scrolling behind it
    // rather than a boxed panel that pushes the list up.
    <div className="relative flex h-full flex-1 flex-col overflow-hidden">
      <MessageList
        messages={messages}
        streamingMessage={streaming || streamError?.message}
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
      />
      <div ref={composerWrapRef} className="pointer-events-none absolute inset-x-0 bottom-0">
        {submissionError && (
          <p role="alert" className="mx-auto max-w-3xl px-4 text-sm text-ink">
            {submissionError}
          </p>
        )}
        <Composer
          value={draft}
          onChange={setDraft}
          onSend={handleSend}
          disabled={isBusy}
          inputRef={composerRef}
          isEditing={Boolean(editingMessageId)}
          onCancelEdit={handleCancelEdit}
          quickReplies={activeSuggestions?.quickReplies || []}
          onPickQuickReply={(value) => activeSuggestions && handlePickQuickReply(activeSuggestions.id, value)}
          isAtBottom={isAtBottom}
          onScrollToBottom={scrollToBottom}
          onCancel={handleStop}
        />
      </div>
    </div>
  );
}
