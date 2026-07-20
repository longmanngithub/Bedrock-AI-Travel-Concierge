"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Logo from "./Logo.jsx";
import Skeleton from "./Skeleton.jsx";
import AuthControls from "./auth/AuthControls.jsx";
import { ChatBubbleIcon, PlusIcon, TrashIcon } from "./icons.jsx";

// Bucket a conversation by how recently it was touched, so the list reads like
// the reference's "Your conversations / Last 7 Days" grouping.
function bucketOf(iso) {
  const now = new Date();
  const then = new Date(iso);
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const t = then.getTime();
  if (t >= startOfToday) return "Today";
  if (t >= startOfToday - 6 * 86400000) return "Last 7 days";
  return "Older";
}

const BUCKET_ORDER = ["Today", "Last 7 days", "Older"];

function ConversationItem({ conversation, isActive, isGenerating, onSelect, onDelete }) {
  const [confirming, setConfirming] = useState(false);

  if (confirming) {
    return (
      <div className="flex items-center gap-1.5 rounded-xl bg-danger/10 px-3 py-2">
        <span className="flex-1 truncate text-xs text-ink">Delete this chat?</span>
        <button
          type="button"
          onClick={() => onDelete(conversation.id)}
          className="rounded-lg bg-danger/15 px-2 py-1 text-xs font-medium text-danger transition-colors hover:bg-danger/25"
        >
          Delete
        </button>
        <button
          type="button"
          onClick={() => setConfirming(false)}
          className="rounded-lg px-2 py-1 text-xs text-muted transition-colors hover:text-ink"
        >
          Cancel
        </button>
      </div>
    );
  }

  return (
    <div
      className={`group/item flex items-center gap-2 rounded-xl px-2.5 py-2 transition-colors ${
        isActive ? "bg-brand-soft" : "hover:bg-surface-2"
      }`}
    >
      <button
        type="button"
        onClick={() => onSelect(conversation.id)}
        className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
      >
        {isGenerating ? (
          <div className="h-4 w-4 shrink-0 flex items-center justify-center">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-brand opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-brand"></span>
            </span>
          </div>
        ) : (
          <ChatBubbleIcon className={`h-4 w-4 shrink-0 ${isActive ? "text-brand" : "text-muted"}`} />
        )}
        <span className={`truncate text-sm ${isActive ? "font-medium text-brand" : "text-ink-soft"}`}>
          {conversation.title}
        </span>
      </button>

      <div className={`flex shrink-0 items-center gap-0.5 ${isActive ? "" : "opacity-100 md:opacity-0 md:group-hover/item:opacity-100"}`}>
        <button
          type="button"
          onClick={() => setConfirming(true)}
          aria-label="Delete chat"
          className="rounded-md p-1 text-muted transition-colors hover:text-danger"
        >
          <TrashIcon className="h-3.5 w-3.5" />
        </button>
        {isActive && <span className="ml-0.5 h-2 w-2 rounded-full bg-brand" />}
      </div>
    </div>
  );
}

function SidebarSkeleton({ count = 3 }) {
  const widths = ["w-3/5", "w-4/5", "w-2/3", "w-1/2", "w-3/4"];
  const skeletonCount = count > 0 ? count : 3;
  return (
    <ul className="space-y-1.5 px-3">
      {Array.from({ length: skeletonCount }).map((_, i) => {
        const w = widths[i % widths.length];
        return (
          <li key={i} className="flex items-center gap-2.5 px-2.5 py-2">
            <Skeleton className="h-4 w-4 rounded" />
            <Skeleton className={`h-3.5 ${w}`} />
          </li>
        );
      })}
    </ul>
  );
}

export default function Sidebar({
  activeId,
  conversations,
  onSelectConversation,
  onNewChat,
  onDeleteConversation,
  onClearAll,
  onClose,
  mounted,
  initialConversationCount,
  activeStreams,
}) {
  const [confirmClear, setConfirmClear] = useState(false);

  // The footer floats absolutely over the conversation list (same treatment
  // as the composer floating over the message list) so scrolled items dissolve
  // behind a translucent, blurred panel instead of pushing the list up. Its
  // height is measured live — not a fixed guess — because it changes with the
  // account-linking notice banner and the auth menu's own content.
  const footerRef = useRef(null);
  const [footerHeight, setFooterHeight] = useState(96);

  useEffect(() => {
    const el = footerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      setFooterHeight(entries[0].contentRect.height);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const groups = useMemo(() => {
    const map = new Map();
    for (const c of conversations) {
      const b = bucketOf(c.updated_at);
      if (!map.has(b)) map.set(b, []);
      map.get(b).push(c);
    }
    return BUCKET_ORDER.filter((b) => map.has(b)).map((b) => [b, map.get(b)]);
  }, [conversations]);

  function handleSelect(id) {
    onSelectConversation(id);
    onClose?.();
  }

  const initialCount = initialConversationCount ?? 3;

  return (
    <aside className="relative flex h-full w-full flex-col bg-surface rounded-r-[28px] md:w-[300px] md:rounded-[28px] md:shadow-[0_2px_20px_rgba(30,30,60,0.06)]">
      {/* Brand */}
      <div className="flex items-center gap-2.5 px-5 pt-6 pb-5">
        <Logo size={30} />
        <span className="text-[17px] font-bold tracking-tight text-ink">
          Bedrock
        </span>
        <span className="ml-1 rounded-md bg-brand-soft px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-brand uppercase">
          Trips
        </span>
      </div>

      {/* New chat */}
      <div className="px-4 pb-4">
        <button
          type="button"
          onClick={onNewChat}
          className="flex w-full items-center justify-center gap-2 rounded-full bg-brand px-4 py-3 text-sm font-semibold text-on-brand shadow-sm transition-all hover:bg-brand-strong active:scale-[0.98]"
        >
          <PlusIcon className="h-4 w-4" />
          New chat
        </button>
      </div>

      {/* Section header */}
      <div className="flex items-center justify-between px-6 pb-2">
        <span className="text-xs font-medium text-muted">Your conversations</span>
        {mounted && conversations.length > 0 && (
          confirmClear ? (
            // Destructive action gets the same inline confirm as single-chat
            // delete — previously Clear All fired immediately with no guard.
            <span className="flex items-center gap-2 text-xs">
              <span className="text-muted">Clear all?</span>
              <button
                type="button"
                onClick={() => { setConfirmClear(false); onClearAll(); }}
                className="font-medium text-danger hover:opacity-70"
              >
                Yes
              </button>
              <button
                type="button"
                onClick={() => setConfirmClear(false)}
                className="font-medium text-muted hover:opacity-70"
              >
                No
              </button>
            </span>
          ) : (
            <button
              type="button"
              onClick={() => setConfirmClear(true)}
              className="text-xs font-medium text-brand transition-opacity hover:opacity-70"
            >
              Clear All
            </button>
          )
        )}
      </div>

      {/* List */}
      <nav
        className="thin-scroll flex-1 overflow-y-auto px-1"
        style={{ paddingBottom: footerHeight + 8 }}
      >
        {!mounted ? (
          <SidebarSkeleton count={initialCount} />
        ) : conversations.length === 0 ? (
          <p className="px-5 py-6 text-center text-xs text-muted">Your trip conversations will show up here.</p>
        ) : (
          groups.map(([bucket, items]) => (
            <div key={bucket} className="mb-1">
              {bucket !== "Today" && (
                <p className="px-4 pt-3 pb-1.5 text-[11px] font-medium tracking-wide text-muted">{bucket}</p>
              )}
              <ul className="space-y-0.5 px-2">
                {items.map((c) => (
                  <li key={c.id}>
                    <ConversationItem
                      conversation={c}
                      isActive={c.id === activeId}
                      isGenerating={Boolean(activeStreams?.[c.id]?.isBusy)}
                      onSelect={handleSelect}
                      onDelete={onDeleteConversation}
                    />
                  </li>
                ))}
              </ul>
            </div>
          ))
        )}
      </nav>

      {/* The profile control floats above the conversation scroller. A
          transparent, masked backdrop blur preserves the hierarchy while
          allowing the scrolled content to remain visible behind it. */}
      <div ref={footerRef} className="pointer-events-none absolute inset-x-0 bottom-0 z-10">
        <div className="pointer-events-auto relative px-3 pb-3 pt-8">
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-x-0 bottom-0 h-28"
            style={{
              WebkitBackdropFilter: "blur(8px)",
              backdropFilter: "blur(8px)",
              WebkitMaskImage: "linear-gradient(to top, rgba(0,0,0,1) 45%, rgba(0,0,0,0) 100%)",
              maskImage: "linear-gradient(to top, rgba(0,0,0,1) 45%, rgba(0,0,0,0) 100%)",
            }}
          />
          <div className="relative">
            <AuthControls />
          </div>
        </div>
      </div>
    </aside>
  );
}
