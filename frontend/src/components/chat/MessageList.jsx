"use client";

import { useCallback, useEffect, useRef } from "react";
import Logo from "../Logo.jsx";
import MessageRow from "./MessageRow.jsx";
import Skeleton from "../Skeleton.jsx";
import { ChevronDownIcon, UserIcon } from "../icons.jsx";

const STARTERS = [
  "Plan me 4 days in Tokyo, around $2,500",
  "A relaxed long weekend in Lisbon next month",
  "5 days in Barcelona for 2, food & architecture",
  "Budget trip to Bangkok, I love street food",
];

function WelcomeSkeleton() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center px-6 py-10 text-center animate-pulse">
      {/* Logo placeholder */}
      <Skeleton className="h-14 w-14 rounded-full mb-6" />
      {/* Title placeholder */}
      <Skeleton className="h-7 w-48 mb-3" />
      {/* Subtitle placeholders */}
      <Skeleton className="h-4 w-72 mb-2" />
      <Skeleton className="h-4 w-56 mb-8" />
      {/* Starters grid placeholder */}
      <div className="grid w-full max-w-xl grid-cols-1 gap-2.5 sm:grid-cols-2">
        <Skeleton className="h-12 w-full rounded-2xl" />
        <Skeleton className="h-12 w-full rounded-2xl" />
        <Skeleton className="h-12 w-full rounded-2xl" />
        <Skeleton className="h-12 w-full rounded-2xl" />
      </div>
    </div>
  );
}

function ConversationSkeleton({ messages = [] }) {
  // Use static message mockups during pre-hydration/mount to ensure the server-rendered
  // HTML and client-rendered HTML match exactly. Once mounted is true, the actual
  // message list is rendered instantly.
  const list = messages.length > 0 ? messages : [
    { role: "user", content: "Short query starter..." },
    { role: "assistant", content: "A longer assistant response detailing slots and clarify options..." },
    { role: "user", content: "Another prompt follow-up..." }
  ];

  return (
    <div className="w-full flex-1 overflow-y-auto">
      {list.map((m, i) => {
        const isUser = m.role === "user";
        const len = (m.content || "").length;
        const lineCount = Math.max(1, Math.ceil(len / 80));
        
        return (
          <div key={m.id || i} className="group w-full px-4 py-4 sm:px-6">
            <div className={`mx-auto flex max-w-3xl items-start gap-3 ${isUser ? "flex-row-reverse" : ""}`}>
              {isUser ? (
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-2 text-ink-soft/20">
                  <UserIcon className="h-[18px] w-[18px]" />
                </div>
              ) : (
                <Logo size={32} className="mt-0.5 shrink-0 opacity-20" />
              )}
              
              {isUser ? (
                <div className="flex min-w-0 max-w-[85%] flex-col items-end sm:max-w-[70%]">
                  <div className="mb-1 h-6 w-6" />
                  <div
                    className="rounded-[22px] rounded-tr-md bg-brand-soft/30 px-4 py-2.5 animate-pulse"
                    style={{ width: `${Math.min(100, Math.max(30, 20 + len * 2))}%` }}
                  >
                    <div className="space-y-2">
                      {Array.from({ length: lineCount }).map((_, idx) => (
                        <Skeleton
                          key={idx}
                          className="h-3.5 rounded opacity-40"
                          style={{
                            width: idx === lineCount - 1 && lineCount > 1 ? "50%" : "100%"
                          }}
                        />
                      ))}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="min-w-0 flex-1">
                  <div className="mb-1 flex items-center gap-1.5">
                    <span className="text-sm font-semibold text-brand/30">Bedrock</span>
                    <ChevronDownIcon className="h-3.5 w-3.5 text-muted/20" />
                  </div>
                  
                  <div className="space-y-2.5 w-[85%] animate-pulse">
                    {Array.from({ length: lineCount }).map((_, idx) => (
                      <Skeleton
                        key={idx}
                        className="h-3.5 rounded opacity-40"
                        style={{
                          width: idx === lineCount - 1 && lineCount > 1 ? "60%" : "100%"
                        }}
                      />
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function parseStructure(structureStr) {
  if (!structureStr) return [];
  return structureStr.split(",").map((item, idx) => {
    const [roleChar, lenStr] = item.split(":");
    return {
      id: `skeleton-${idx}`,
      role: roleChar === "u" ? "user" : "assistant",
      content: " ".repeat(parseInt(lenStr || "0", 10)),
    };
  });
}

function Welcome({ onPick }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center px-6 py-10 text-center">
      <Logo size={56} className="mb-5" />
      <h2 className="text-2xl font-bold tracking-tight text-ink sm:text-[28px]">Where to next?</h2>
      <p className="mt-2 max-w-md text-[0.95rem] text-muted">
        Tell me a destination, rough dates, and a budget — I&apos;ll ask about anything I&apos;m missing,
        then build you a full boarding-pass itinerary.
      </p>
      <div className="mt-7 grid w-full max-w-xl grid-cols-1 gap-2.5 sm:grid-cols-2">
        {STARTERS.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onPick(s)}
            className="rounded-2xl border border-line bg-surface px-4 py-3 text-left text-sm text-ink-soft shadow-sm transition-all hover:border-brand/50 hover:bg-brand-soft/40 hover:text-ink"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function MessageList({
  messages,
  streamingMessage,
  stagingType,
  startedAt,
  disabled,
  editingMessageId,
  bottomClearance = 164,
  onPickStarter,
  onEditMessage,
  onRegenerate,
  onFeedback,
  scrollContainerRef,
  onScrollStateChange,
  mounted,
  initialHasConversations,
  initialActiveStructure,
}) {
  const lastRowRef = useRef(null);
  const scrollRef = useRef(null);
  const isAtBottomRef = useRef(true);
  const prevUserMsgRef = useRef(null);
  const resultScrolledRef = useRef(false);

  // Merge the internal scroll ref with the one exposed to ChatWindow.
  const setScrollRef = useCallback(
    (el) => {
      scrollRef.current = el;
      if (scrollContainerRef) scrollContainerRef.current = el;
    },
    [scrollContainerRef],
  );

  // Track whether the user is near the bottom of the scroll container.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    function onScroll() {
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 15;
      if (atBottom !== isAtBottomRef.current) {
        isAtBottomRef.current = atBottom;
        onScrollStateChange?.(atBottom);
      }
    }
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [onScrollStateChange]);

  // Keep scroll at bottom if it was at the bottom when clearance (composer height) changes
  // (e.g. when quick replies render, which expands the bottom spacing).
  useEffect(() => {
    const el = scrollRef.current;
    if (el && isAtBottomRef.current) {
      requestAnimationFrame(() => {
        el.scrollTop = el.scrollHeight;
      });
    }
  }, [bottomClearance]);

  // Editing a past message temporarily hides (never deletes) everything after
  // it, so the composer's "resend from here" preview matches what will
  // actually happen — Esc brings it straight back since nothing was removed.
  const editIdx = editingMessageId ? messages.findIndex((m) => m.id === editingMessageId) : -1;
  const visibleMessages = editIdx === -1 ? messages : messages.slice(0, editIdx + 1);

  const lastMessage = messages[messages.length - 1];
  const isStreamingNow = Boolean(streamingMessage);
  const isResultType = stagingType === "result";

  // ── Scroll rule 1: user sends a message → scroll to bottom ──
  useEffect(() => {
    if (lastMessage?.role === "user" && lastMessage.id !== prevUserMsgRef.current) {
      prevUserMsgRef.current = lastMessage.id;
      const el = scrollRef.current;
      if (el) {
        requestAnimationFrame(() => {
          el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
          isAtBottomRef.current = true;
          onScrollStateChange?.(true);
        });
      }
    }
  }, [lastMessage?.id, lastMessage?.role, onScrollStateChange]);

  // ── Scroll rule 2: non-result streaming → keep at bottom as tokens arrive ──
  useEffect(() => {
    if (!isStreamingNow || isResultType) return;
    const el = scrollRef.current;
    if (el && isAtBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  });

  // ── Scroll rule 3: result streaming → scroll to START of the response once ──
  useEffect(() => {
    if (isStreamingNow && isResultType) {
      if (!resultScrolledRef.current && lastRowRef.current) {
        resultScrolledRef.current = true;
        lastRowRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    } else {
      resultScrolledRef.current = false;
    }
  }, [isStreamingNow, isResultType]);

  // Read initial messages from localStorage before mount (only on client)
  let initialMessages = [];
  if (typeof window !== "undefined") {
    try {
      const raw = window.localStorage.getItem("bedrock:conversations");
      if (raw) {
        const parsed = JSON.parse(raw);
        const activeConv = parsed.conversations.find((c) => c.id === parsed.activeId);
        if (activeConv) {
          initialMessages = activeConv.messages;
        }
      }
    } catch (e) {}
  }

  // The composer floats over the bottom of this list (see ChatWindow), so the
  // last message needs enough clearance to scroll fully clear of it. That
  // clearance is measured live (ResizeObserver in ChatWindow) rather than
  // guessed, since the composer's real height varies with quick replies, the
  // edit banner, and the textarea's own auto-grow — a static guess is either
  // too tight (content collides with the floating bar) or wastes space.
  if (!mounted) {
    let structureList = [];
    if (initialActiveStructure) {
      structureList = parseStructure(initialActiveStructure);
    } else if (initialMessages.length > 0) {
      structureList = initialMessages;
    }

    return (
      <div
        ref={setScrollRef}
        className="thin-scroll flex flex-1 overflow-y-auto"
        style={{ paddingBottom: bottomClearance }}
      >
        {structureList.length > 0 ? (
          <ConversationSkeleton messages={structureList} />
        ) : (
          <WelcomeSkeleton />
        )}
      </div>
    );
  }

  if (visibleMessages.length === 0 && !streamingMessage) {
    return (
      <div ref={setScrollRef} className="thin-scroll flex flex-1 overflow-y-auto" style={{ paddingBottom: bottomClearance }}>
        <Welcome onPick={onPickStarter} />
      </div>
    );
  }

  return (
    <div ref={setScrollRef} className="thin-scroll flex-1 overflow-y-auto" style={{ paddingBottom: bottomClearance }}>
      {visibleMessages.map((m, i) => (
        <div key={m.id} ref={i === visibleMessages.length - 1 && !streamingMessage ? lastRowRef : undefined}>
          <MessageRow
            message={m}
            isLast={i === visibleMessages.length - 1 && !streamingMessage}
            disabled={disabled}
            onEditMessage={onEditMessage}
            onRegenerate={onRegenerate}
            onFeedback={onFeedback}
          />
        </div>
      ))}
      {streamingMessage && (
        <div ref={lastRowRef}>
          <MessageRow
            message={streamingMessage}
            isLast
            streaming
            stagingType={stagingType}
            startedAt={startedAt}
            disabled={disabled}
            onEditMessage={onEditMessage}
            onRegenerate={onRegenerate}
            onFeedback={onFeedback}
          />
        </div>
      )}
    </div>
  );
}
