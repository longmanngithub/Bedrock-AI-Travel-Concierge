"use client";

import { useEffect, useRef } from "react";
import { ArrowDownIcon, EditIcon, SendIcon, StopIcon } from "../icons.jsx";
import QuickReplies from "./QuickReplies.jsx";

// Controlled by the parent (value/onChange) rather than owning its own text
// state, so ChatWindow can prefill it when the user edits a previous message.
// ChatWindow renders this absolutely-positioned over the bottom of the
// message list. A CSS gradient-to-canvas background dissolves scrolled content
// smoothly underneath — no separate overlay div.
export default function Composer({
  value,
  onChange,
  onSend,
  disabled,
  inputRef,
  isEditing = false,
  onCancelEdit,
  quickReplies = [],
  onPickQuickReply,
  isAtBottom = true,
  onScrollToBottom,
  onCancel,
}) {
  const internalRef = useRef(null);
  const textareaRef = inputRef || internalRef;

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [value, textareaRef]);

  function submit() {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
  }

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    } else if (e.key === "Escape" && isEditing) {
      e.preventDefault();
      onCancelEdit?.();
    }
  }

  const hasQuickReplies = quickReplies.length > 0;
  const showScrollBtn = !isAtBottom;

  return (
    <div
      className="pointer-events-none relative px-4 pt-6 sm:px-6"
      style={{
        paddingBottom: "max(1.25rem, env(safe-area-inset-bottom))",
      }}
    >
      {/* Full-width backdrop blur & canvas gradient layer.
          Positioned absolutely at the bottom to span the entire screen width.
          Uses mask-image to fade the blur at the top, eliminating the sharp horizontal line. */}
      <div
        className="pointer-events-none absolute inset-x-0 bottom-0 z-0"
        style={{
          height: "140px",
          background: "linear-gradient(to top, color-mix(in srgb, var(--color-canvas) 75%, transparent) 30%, transparent 100%)",
          WebkitBackdropFilter: "blur(8px)",
          backdropFilter: "blur(8px)",
          WebkitMaskImage: "linear-gradient(to top, rgba(0,0,0,1) 40%, rgba(0,0,0,0) 100%)",
          maskImage: "linear-gradient(to top, rgba(0,0,0,1) 40%, rgba(0,0,0,0) 100%)",
        }}
      />

      <div className="relative z-10 mx-auto max-w-3xl">
        {/* Scroll-to-bottom button — sits above quick replies when they exist,
            otherwise above the input bar. Animates in/out smoothly. */}
        <div
          className="pointer-events-none flex justify-center overflow-hidden transition-all duration-300 ease-out"
          style={{
            maxHeight: showScrollBtn ? "48px" : "0px",
            opacity: showScrollBtn ? 1 : 0,
            marginBottom: showScrollBtn ? (hasQuickReplies ? "0.25rem" : "0.5rem") : "0px",
          }}
        >
          <button
            type="button"
            onClick={onScrollToBottom}
            aria-label="Scroll to bottom"
            className="pointer-events-auto flex h-8 w-8 items-center justify-center rounded-full border border-line/60 bg-surface shadow-sm transition-all hover:bg-surface-2 hover:shadow-md active:scale-95"
          >
            <ArrowDownIcon className="h-4 w-4 text-muted" />
          </button>
        </div>

        <QuickReplies options={quickReplies} disabled={disabled} onPick={onPickQuickReply} />

        {isEditing && (
          <div className="mb-1.5 flex items-center gap-1.5 px-1 text-xs text-muted pointer-events-auto">
            <EditIcon className="h-3 w-3" />
            <span className="flex-1">
              Editing message<span className="hidden sm:inline"> — press Esc to cancel</span>
            </span>
            {/* Mobile has no Esc key — give an always-tappable cancel control. */}
            <button
              type="button"
              onClick={() => onCancelEdit?.()}
              aria-label="Cancel editing"
              className="flex h-6 items-center gap-1 rounded-full px-2 font-medium text-brand transition hover:bg-surface-2"
            >
              <svg viewBox="0 0 20 20" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="2.2">
                <path d="M6 6l8 8M14 6l-8 8" strokeLinecap="round" />
              </svg>
              Cancel
            </button>
          </div>
        )}

        <div className="pointer-events-auto flex items-end gap-2 rounded-full border border-line/60 bg-surface/85 pl-4 pr-1.5 py-1.5 shadow-[0_4px_20px_rgba(14,34,38,0.08)] backdrop-blur-2xl transition-all focus-within:shadow-[0_4px_20px_rgba(13,148,136,0.12)] dark:shadow-[0_4px_20px_rgba(0,0,0,0.35)]">
          <textarea
            ref={textareaRef}
            rows={1}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Tell me where you'd like to go…"
            className="max-h-44 flex-1 resize-none bg-transparent py-1.5 text-sm text-ink placeholder:text-muted focus:outline-none"
          />
          {disabled && onCancel ? (
            // While a reply/plan is generating, the send button becomes a stop
            // button — lets the user change their mind instead of waiting out
            // a run that can take over a minute.
            <button
              type="button"
              onClick={onCancel}
              aria-label="Stop generating"
              title="Stop generating"
              className="mb-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-ink text-canvas shadow-sm transition-all hover:opacity-85 active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
            >
              <StopIcon className="h-4 w-4" />
            </button>
          ) : (
            <button
              type="button"
              onClick={submit}
              disabled={disabled || !value.trim()}
              aria-label="Send message"
              className="mb-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand text-on-brand shadow-sm transition-all hover:bg-brand-strong active:scale-95 disabled:cursor-not-allowed disabled:opacity-35 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
            >
              <SendIcon className="h-4.5 w-4.5" />
            </button>
          )}
        </div>
        <p className="pointer-events-none mt-2 text-center text-[11px] text-muted">
          Bedrock plans trips — it may take a moment to build a full itinerary.
        </p>
      </div>
    </div>
  );
}
