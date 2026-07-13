"use client";

import { useState } from "react";
import Logo from "../Logo.jsx";
import MarkdownText from "./MarkdownText.jsx";
import TravelTicket from "./TravelTicket.jsx";
import TypingIndicator from "./TypingIndicator.jsx";
import TicketSkeleton from "./TicketSkeleton.jsx";
import StagingIndicator from "./StagingIndicator.jsx";
import {
  CheckIcon,
  ChevronDownIcon,
  CopyIcon,
  EditIcon,
  RegenerateIcon,
  ThumbDownIcon,
  ThumbUpIcon,
  UserIcon,
} from "../icons.jsx";

function UserAvatar() {
  return (
    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-2 text-ink-soft">
      <UserIcon className="h-[18px] w-[18px]" />
    </div>
  );
}

function IconButton({ onClick, active, disabled, label, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
      className={`flex h-7 w-7 items-center justify-center rounded-lg transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
        active ? "bg-brand-soft text-brand" : "text-muted hover:bg-surface-2 hover:text-ink"
      }`}
    >
      {children}
    </button>
  );
}

function ActionBar({ message, disabled, onRegenerate, onFeedback, showRegenerate }) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(message.content || "");
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard permission denied — no-op */
    }
  }

  return (
    <div className="mt-2.5 flex items-center gap-1">
      <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
        <IconButton
          label="Good response"
          active={message.feedback === "up"}
          disabled={disabled}
          onClick={() => onFeedback(message.id, "up")}
        >
          <ThumbUpIcon className="h-[15px] w-[15px]" />
        </IconButton>
        <IconButton
          label="Bad response"
          active={message.feedback === "down"}
          disabled={disabled}
          onClick={() => onFeedback(message.id, "down")}
        >
          <ThumbDownIcon className="h-[15px] w-[15px]" />
        </IconButton>
        <IconButton label={copied ? "Copied" : "Copy"} disabled={disabled} onClick={handleCopy}>
          {copied ? <CheckIcon className="h-[15px] w-[15px] text-ok" /> : <CopyIcon className="h-[15px] w-[15px]" />}
        </IconButton>
      </div>
      {showRegenerate && (
        <button
          type="button"
          disabled={disabled}
          onClick={() => onRegenerate(message.id)}
          className="ml-auto flex items-center gap-1.5 rounded-full border border-line px-3 py-1.5 text-xs font-medium text-ink-soft transition-colors hover:border-brand hover:text-brand disabled:cursor-not-allowed disabled:opacity-40"
        >
          <RegenerateIcon className="h-3.5 w-3.5" />
          Regenerate
        </button>
      )}
    </div>
  );
}

// Right/left bubble layout (iMessage-style): your messages hug the right edge
// of the centered column in a filled bubble; Bedrock's replies sit flush left,
// plain text with its mark for an avatar — no bubble, so long itineraries and
// markdown don't fight a box. Both share the same max-w-3xl reading column.
export default function MessageRow({
  message,
  isLast,
  disabled,
  streaming = false,
  stagingType = null,
  startedAt = null,
  onEditMessage,
  onRegenerate,
  onFeedback,
}) {
  const isUser = message.role === "user";
  const isTicket = message.kind === "result" && message.itinerary;
  // Only a genuine failure gets the warning treatment. A "refusal" is just a
  // friendly off-topic redirect, so it renders as ordinary assistant text.
  const isNotice = message.kind === "error";
  const hasText = Boolean((message.content || "").trim());

  // While an assistant turn is streaming we may not know its type yet: before
  // the first token (and before meta) show typing dots; once it's a `result`
  // and the itinerary hasn't landed, show the boarding-pass skeleton.
  const showTypingDots = streaming && !hasText;
  const showTicketSkeleton = streaming && stagingType === "result" && !isTicket;

  return (
    <div className="group w-full px-4 py-4 sm:px-6">
      <div className={`mx-auto flex max-w-3xl items-start gap-3 ${isUser ? "flex-row-reverse" : ""}`}>
        {isUser ? <UserAvatar /> : <Logo size={32} className="mt-0.5 shrink-0" />}

        {isUser ? (
          <div className="flex min-w-0 max-w-[85%] flex-col items-end sm:max-w-[70%]">
            <button
              type="button"
              disabled={disabled}
              onClick={() => onEditMessage(message.id)}
              aria-label="Edit message"
              title="Edit message"
              className="mb-1 flex h-6 w-6 items-center justify-center rounded-md text-muted opacity-0 transition-opacity hover:bg-surface-2 hover:text-ink group-hover:opacity-100 disabled:opacity-0"
            >
              <EditIcon className="h-3.5 w-3.5" />
            </button>
            <div className="rounded-[22px] rounded-tr-md bg-brand-soft px-4 py-2.5 text-[0.95rem] leading-relaxed whitespace-pre-wrap text-ink">
              {message.content}
            </div>
          </div>
        ) : (
          <div className="min-w-0 flex-1">
            <div className="mb-1 flex items-center gap-1.5">
              <span className="text-sm font-semibold text-brand">Bedrock</span>
              <ChevronDownIcon className="h-3.5 w-3.5 text-muted" />
            </div>

            {isNotice ? (
              <div className="flex items-start gap-2.5 rounded-2xl border border-warn/25 bg-warn/5 px-4 py-3">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-warn" />
                <p className="text-[0.95rem] leading-relaxed text-ink">{message.content}</p>
              </div>
            ) : (
              <>
                {showTypingDots ? (
                  <div className="py-1">
                    <TypingIndicator />
                  </div>
                ) : (
                  hasText && <MarkdownText text={message.content} streaming={streaming} />
                )}

                {showTicketSkeleton && (
                  <div className="mt-3 space-y-3">
                    <StagingIndicator startedAt={startedAt} />
                    <TicketSkeleton />
                  </div>
                )}

                {isTicket && (
                  <div className="mt-3">
                    <TravelTicket
                      itinerary={message.itinerary}
                      tripRequest={message.tripRequest}
                      elapsedSeconds={message.elapsedSeconds}
                    />
                  </div>
                )}
              </>
            )}

            {!streaming && !isNotice && (
              <ActionBar
                message={message}
                disabled={disabled}
                onRegenerate={onRegenerate}
                onFeedback={onFeedback}
                showRegenerate={isLast}
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
