"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { streamChat, formatError } from "../../lib/api.js";
import { useAuth } from "../../lib/AuthContext.jsx";
import { useConversations } from "../../lib/ConversationsContext.jsx";
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
  initialHasConversations,
  initialActiveStructure,
  streaming,
  stagingType,
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
      conversationId: convId,
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
      onJob: (payload) => {
        // A plannable turn: the crew runs in the BACKGROUND, and the backend
        // has already persisted a placeholder Message (kind="job") for it —
        // keep the stream "settled" (the SSE POST is done) and hand off to
        // attachJob, the same code path a reconnect-after-reload uses.
        settled = true;
        attachJob(convId, payload.job_id, payload.message ?? acc.content);
      },
      onDone: async () => {
        settled = true;
        // The backend owns completed assistant messages so suggested replies,
        // notification delivery, and reload behavior all share one durable row.
        // Silent: this reconciles a transcript already on screen, so it must
        // not flip the loading flags and blink the UI back to skeletons.
        await Promise.all([
          loadMessages(convId, { silent: true }),
          refreshList({ silent: true }),
        ]);
        updateStreamState(convId, null);
        clearStreamController(convId);
      },
      onError: async (err) => {
        settled = true;
        // Session expired mid-conversation (e.g. the access cookie lapsed
        // between page load and send): drop the session so the app-wide gate
        // re-covers the screen, rather than dropping a scary error bubble
        // into the transcript.
        if (err?.code === "E_AUTH_REQUIRED") {
          forceReauth();
        } else {
          await appendMessage(convId, {
            role: "assistant",
            kind: "error",
            content: err?.networkError
              ? "I couldn't reach the planning service — please check your connection and try again."
              : formatError(err),
          });
        }
        updateStreamState(convId, null);
        clearStreamController(convId);
      },
    });

    // Stream closed without a terminal event (e.g. server dropped): surface it
    if (!settled && !controller.signal.aborted) {
      await appendMessage(convId, {
        role: "assistant",
        kind: "error",
        content: "The connection closed before I finished — please try again.",
      });
      updateStreamState(convId, null);
      clearStreamController(convId);
    }
  }

  async function handleSend(text) {
    if (isBusy) return;
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

    const convId = await ensureActiveConversation();

    // Resending an edited message: only now — at send time, not at the
    // moment the pencil icon was clicked — do we actually drop the old tail.
    // Cancelling (Esc) before this point leaves everything untouched.
    if (editingMessageId) {
      await truncateConversation(convId, editingMessageId);
      const idx = baseMessages.findIndex((m) => m.id === editingMessageId);
      if (idx !== -1) baseMessages = baseMessages.slice(0, idx);
      setEditingMessageId(null);
    }

    setDraft("");
    const userMsg = await appendMessage(convId, { role: "user", content: text });

    const history = [...baseMessages, userMsg].map((m) => ({
      role: m.role,
      content: historyContent(m),
    }));
    runTurn(convId, history);
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
    if (!activeId || isBusy) return;
    // Same stale-closure concern as handleSend — this history feeds the crew
    // the same way, so it needs the freshest message set too.
    const current = messagesRef.current;
    const idx = current.findIndex((m) => m.id === messageId);
    if (idx === -1) return;
    const historyBefore = current.slice(0, idx).map((m) => ({ role: m.role, content: historyContent(m) }));
    await truncateConversation(activeId, messageId);
    runTurn(activeId, historyBefore);
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
    !streaming && !isBusy && lastMessage?.role === "assistant" && Array.isArray(lastMessage?.quickReplies) &&
    lastMessage.quickReplies.length > 0 && !lastMessage?.quickRepliesConsumed
      ? lastMessage
      : null;

  async function handleStop() {
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
