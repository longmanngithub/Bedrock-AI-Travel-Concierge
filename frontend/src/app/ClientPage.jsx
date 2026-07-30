"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import Sidebar from "../components/Sidebar.jsx";
import ChatWindow from "../components/chat/ChatWindow.jsx";
import Logo from "../components/Logo.jsx";
import { MenuIcon } from "../components/icons.jsx";
import { AuthProvider } from "../lib/AuthContext.jsx";
import { ConversationsProvider, useConversations } from "../lib/ConversationsContext.jsx";
import { jobs as jobsApi, subscribeJob } from "../lib/api.js";

function ClientPageInner({ initialHasConversations, initialConversationCount, initialActiveStructure }) {
  const {
    conversations: convList,
    loadingList,
    activeId,
    selectConversation,
    startNewChat,
    deleteConversation,
    clearAll,
    refreshList,
    loadMessages,
  } = useConversations();
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Background stream states mapped by conversation ID
  const [activeStreams, setActiveStreams] = useState({});
  const streamControllersRef = useRef({});

  const updateStreamState = useCallback((convId, state) => {
    setActiveStreams((prev) => {
      if (state === null) {
        const next = { ...prev };
        delete next[convId];
        return next;
      }
      return {
        ...prev,
        [convId]: {
          ...(prev[convId] || {}),
          ...state,
        },
      };
    });
  }, []);

  const registerStreamController = useCallback((convId, controller) => {
    streamControllersRef.current[convId]?.abort();
    streamControllersRef.current[convId] = controller;
  }, []);

  const clearStreamController = useCallback((convId) => {
    delete streamControllersRef.current[convId];
  }, []);

  const abortStream = useCallback((convId) => {
    streamControllersRef.current[convId]?.abort();
    delete streamControllersRef.current[convId];
    updateStreamState(convId, null);
  }, [updateStreamState]);

  // Clean up all requests on unmount
  useEffect(() => {
    return () => {
      Object.values(streamControllersRef.current).forEach((c) => c.abort());
    };
  }, []);

  // Subscribes to a crew job's resumable progress stream for `convId` — used
  // both for a job ChatWindow just enqueued and for reconnecting to one that
  // was already running when the page loaded (the sweep effect below). Lives
  // here rather than in ChatWindow because it must keep running for a
  // conversation the user has since navigated away from — ChatWindow only
  // ever renders the *active* conversation.
  const attachJob = useCallback((convId, jobId, ackContent, { setBusy = false, startedAt } = {}) => {
    const progress = {
      steps: [
        { key: "destination", label: "Researching your destination", state: "running" },
        { key: "food", label: "Finding local food", state: "pending" },
        { key: "personalization", label: "Personalizing your trip", state: "pending" },
        { key: "accommodation", label: "Comparing places to stay", state: "pending" },
        { key: "budget", label: "Balancing your budget", state: "pending" },
      ],
      percent: 0,
      activity: { message: "Starting research" },
    };
    const streamingBase = () => ({ role: "assistant", content: ackContent, kind: "result", progress: { ...progress }, jobId });
    // Set by the abort handler when the user hits Stop: live progress
    // updates stop touching the (already-dismissed) UI, but `settle` below
    // still has to run when the job actually finishes — otherwise the
    // persisted placeholder Message row stays kind="job" (a stale bubble
    // with a mislabeled Regenerate button) until the next full reload,
    // since cancellation itself is only observed at the worker's next step
    // boundary, not synchronously with this call.
    const dismissed = { current: false };

    updateStreamState(convId, {
      ...(setBusy ? { isBusy: true, startedAt: startedAt ?? Date.now() } : {}),
      stagingType: "research",
      streaming: streamingBase(),
    });

    const settle = async () => {
      // The worker already resolved the placeholder Message row in place
      // (kind="job" -> "result"/"error") — reload from the backend rather
      // than appending a second row, so a fresh send and a post-reload
      // reconnect both leave exactly one persisted message for the turn.
      // Silent: the user is watching this conversation right now, so these
      // refetches must reconcile underneath the UI rather than dropping it
      // back to skeletons for the length of a round trip (see the note on
      // refreshList in ConversationsContext).
      const [completedMessages] = await Promise.all([
        loadMessages(convId, { silent: true }),
        refreshList({ silent: true }),
      ]);
      const completedTicket = completedMessages[completedMessages.length - 1];
      const hasCompletedTicket = completedTicket?.kind === "result" && completedTicket.itinerary;

      if (!dismissed.current && hasCompletedTicket) {
        // Keep the checklist and its completed ticket in one live row briefly
        // so the two can overlap and crossfade instead of swapping frames.
        // The ticket is passed through the stream state only for this visual
        // handoff; the durable message remains the source of truth afterward.
        updateStreamState(convId, {
          stagingType: "research-completing",
          streaming: { ...streamingBase(), completionTicket: completedTicket },
        });
        await new Promise((resolve) => setTimeout(resolve, 420));
      }
      if (!dismissed.current) updateStreamState(convId, null);
      clearStreamController(convId);
    };

    subscribeJob(jobId, {
      onSnapshot: (f) => {
        if (dismissed.current) return;
        progress.steps = f.steps || progress.steps;
        progress.percent = f.percent ?? progress.percent;
        updateStreamState(convId, { streaming: streamingBase() });
      },
      onStep: (f) => {
        if (dismissed.current) return;
        progress.steps = progress.steps.map((s) =>
          s.key === f.key ? { ...s, state: f.state, detail: f.detail || s.detail } : s
        );
        if (typeof f.percent === "number") progress.percent = f.percent;
        updateStreamState(convId, { streaming: streamingBase() });
      },
      onActivity: (f) => {
        if (dismissed.current) return;
        progress.activity = { message: f.message };
        updateStreamState(convId, { streaming: streamingBase() });
      },
      onDone: settle,
      onError: settle,
    });
    registerStreamController(convId, {
      abort: () => {
        // Deliberately does NOT close the SSE subscription — only the tab's
        // own live-progress view is dismissed. `settle` still needs to run
        // once the job actually reaches a terminal state so the persisted
        // conversation history ends up correct even though nothing is
        // "watching" it anymore.
        dismissed.current = true;
        updateStreamState(convId, null);
        jobsApi.cancel(jobId).then(() => loadMessages(convId, { silent: true })).catch(() => {});
      },
    });
  }, [updateStreamState, registerStreamController, clearStreamController, loadMessages, refreshList]);

  // "Close the tab, come back" — reconnect to every job still queued/running
  // for this account once on load, not just the conversation the user
  // happens to reopen. Each attach is independent of which conversation is
  // active, so a background run keeps going (and the sidebar dot keeps
  // showing) even before the user revisits it.
  const sweptJobsRef = useRef(false);
  useEffect(() => {
    if (sweptJobsRef.current || loadingList) return;
    sweptJobsRef.current = true;
    (async () => {
      let live = [];
      try {
        const [running, queued] = await Promise.all([jobsApi.list("running"), jobsApi.list("queued")]);
        live = [...(running || []), ...(queued || [])];
      } catch {
        return; // best-effort — a failed sweep just means no reconnect this load
      }
      for (const job of live) {
        if (!job.conversation_id) continue;
        // The real ack line is on the placeholder Message row itself — load
        // it so the reconnected bubble shows the same text a live run would,
        // instead of a generic filler.
        let ackContent = "Building your itinerary…";
        try {
          const msgs = await loadMessages(job.conversation_id);
          const trailing = msgs[msgs.length - 1];
          if (trailing?.jobId === job.id && trailing.content) ackContent = trailing.content;
        } catch {
          /* best-effort — falls back to the generic filler */
        }
        attachJob(job.conversation_id, job.id, ackContent, {
          setBusy: true,
          startedAt: job.created_at ? new Date(job.created_at).getTime() : undefined,
        });
      }
    })();
  }, [loadingList, attachJob]);

  async function handleNewChat() {
    await startNewChat();
    setDrawerOpen(false);
  }

  function handleSelectConversation(id) {
    selectConversation(id);
  }

  async function handleDeleteConversation(id) {
    abortStream(id);
    await deleteConversation(id);
  }

  async function handleClearAll() {
    Object.values(streamControllersRef.current).forEach((c) => c.abort());
    streamControllersRef.current = {};
    setActiveStreams({});
    await clearAll();
  }

  const sidebarProps = {
    activeId,
    conversations: convList,
    onSelectConversation: handleSelectConversation,
    onNewChat: handleNewChat,
    onDeleteConversation: handleDeleteConversation,
    onClearAll: handleClearAll,
    mounted: !loadingList,
    initialConversationCount,
    activeStreams,
  };

  return (
    <div className="flex h-[100dvh] overflow-hidden bg-canvas">
      {/* Desktop sidebar */}
      <div className="hidden shrink-0 py-3 pl-3 md:block">
        <Sidebar {...sidebarProps} />
      </div>

      {/* Mobile drawer — always mounted (never conditionally rendered) so it can
          slide in AND out with a real transition; closed state just parks it
          off-screen and inert rather than unmounting it mid-animation. */}
      <div
        className={`fixed inset-0 z-40 md:hidden ${drawerOpen ? "" : "pointer-events-none"}`}
        aria-hidden={!drawerOpen}
      >
        <div
          className={`absolute inset-0 bg-ink/40 transition-opacity duration-200 ease-out ${
            drawerOpen ? "opacity-100" : "opacity-0"
          }`}
          onClick={(e) => {
            e.preventDefault();
            setDrawerOpen(false);
          }}
        />
        <div
          className={`absolute inset-y-0 left-0 w-[86%] max-w-[320px] isolate overflow-hidden rounded-r-[28px] bg-surface shadow-2xl transition-transform duration-200 ease-out ${
            drawerOpen ? "translate-x-0" : "-translate-x-full"
          }`}
        >
          <Sidebar {...sidebarProps} onClose={() => setDrawerOpen(false)} />
        </div>
      </div>

      {/* Main */}
      <main className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
        {/* Mobile top bar */}
        <header className="flex items-center gap-3 border-b border-line px-4 py-3 md:hidden">
          <button
            type="button"
            // `onClick` only. A previous `onTouchStart` handler here made the
            // drawer open and then instantly shut again on every tap: React
            // attaches touchstart at the root as a PASSIVE listener, so its
            // `preventDefault()` was a no-op (that's the "Unable to
            // preventDefault inside passive event listener" console error).
            // With the default not prevented, the browser still synthesised the
            // follow-up click — but by then this handler had already opened the
            // drawer, so the scrim was mounted and interactive under the
            // pointer, and the click landed on it and closed the drawer again.
            onClick={() => setDrawerOpen(true)}
            aria-label="Open menu"
            className="flex h-11 w-11 items-center justify-center rounded-full text-ink-soft hover:bg-surface-2"
          >
            <MenuIcon />
          </button>
          <div className="flex items-center gap-2">
            <Logo size={26} />
            <span className="text-[15px] font-bold text-ink">Bedrock</span>
          </div>
        </header>

        <ChatWindow
          initialHasConversations={initialHasConversations}
          initialActiveStructure={initialActiveStructure}
          streaming={activeStreams[activeId]?.streaming || null}
          stagingType={activeStreams[activeId]?.stagingType || null}
          isBusy={activeStreams[activeId]?.isBusy || false}
          startedAt={activeStreams[activeId]?.startedAt || null}
          updateStreamState={updateStreamState}
          registerStreamController={registerStreamController}
          clearStreamController={clearStreamController}
          attachJob={attachJob}
          onCancel={() => abortStream(activeId)}
        />
      </main>
    </div>
  );
}

export default function ClientPage(props) {
  return (
    <AuthProvider>
      <ConversationsProvider>
        <ClientPageInner {...props} />
      </ConversationsProvider>
    </AuthProvider>
  );
}
