"use client";

import { useEffect, useState } from "react";

// The "deep research" progress panel: a live checklist driven by the backend's
// job progress stream, plus a weighted progress bar. Unlike the old
// StagingIndicator (rotating strings on a timer), every row here reflects REAL
// backend state — and several rows can be "running" at once, because the five
// research agents work concurrently.
//
// Props:
//   steps:    [{ key, label, state: pending|running|done|failed, detail }]
//   percent:  0..100 (weighted completion, monotonic)
//   activity: optional { message } — a coarse "currently doing X" line
//   startedAt: optional ms timestamp — when present, drives the long-wait
//     reassurance line below (see useLongWaitMessage)

// A real crew run legitimately takes anywhere from ~35s to several minutes
// (verified live across an evaluation sweep: mean ~180s, one run over 350s) —
// long enough that a user watching a bare spinner past the first 20-30s can
// reasonably start to wonder if it's actually stuck. This is deliberately a
// reassurance about DURATION (distinct from `activity`, which reports WHAT
// is currently happening) and is worded to raise its own bar as time passes,
// so a 4-minute wait doesn't still say "just a moment."
const LONG_WAIT_TIERS = [
  { afterMs: 90_000, message: "Complex, multi-day trips can take a few minutes — still working, no need to refresh." },
  { afterMs: 25_000, message: "Still working — itineraries with several attractions can take a minute or two." },
];

function useLongWaitMessage(startedAt, active) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!startedAt || !active) return undefined;
    const id = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(id);
  }, [startedAt, active]);
  if (!startedAt || !active) return null;
  const elapsed = now - startedAt;
  const tier = LONG_WAIT_TIERS.find((t) => elapsed >= t.afterMs);
  return tier ? tier.message : null;
}

function StatusDot({ state }) {
  if (state === "done") {
    return (
      <span className="grid h-5 w-5 place-items-center rounded-full bg-brand text-on-brand">
        <svg viewBox="0 0 20 20" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="2.5">
          <path d="M5 10l3.5 3.5L15 7" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
    );
  }
  if (state === "failed") {
    return (
      <span className="grid h-5 w-5 place-items-center rounded-full bg-danger/15 text-danger">
        <svg viewBox="0 0 20 20" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="2.5">
          <path d="M6 6l8 8M14 6l-8 8" strokeLinecap="round" />
        </svg>
      </span>
    );
  }
  if (state === "running") {
    return (
      <span className="grid h-5 w-5 place-items-center">
        <span
          className="h-4 w-4 rounded-full border-2 border-brand border-t-transparent"
          style={{ animation: "var(--animate-spin)" }}
        />
      </span>
    );
  }
  // pending
  return <span className="grid h-5 w-5 place-items-center"><span className="h-2 w-2 rounded-full bg-line" /></span>;
}

// Three dots that pulse in sequence after a running step's label — the same
// "still working" cue as a typing indicator, for the stretches (several
// seconds to over a minute) where an agent is mid-step with no new event to
// render yet.
function RunningDots() {
  return (
    <span className="ml-1 inline-flex items-center gap-0.5 align-middle">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="h-1 w-1 rounded-full bg-brand"
          style={{ animation: "var(--animate-pulse-dot)", animationDelay: `${i * 160}ms` }}
        />
      ))}
    </span>
  );
}

export default function ResearchProgress({ steps = [], percent = 0, activity = null, startedAt = null }) {
  const pct = Math.max(0, Math.min(100, Math.round(percent || 0)));
  const inProgress = pct < 100;
  const longWaitMessage = useLongWaitMessage(startedAt, inProgress);

  return (
    <div className="w-full max-w-xl rounded-2xl border border-line bg-surface p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-medium text-ink">
          <svg
            viewBox="0 0 20 20"
            className="h-4 w-4 text-brand"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            style={inProgress ? { animation: "var(--animate-breathe)" } : undefined}
          >
            <circle cx="9" cy="9" r="6" />
            <path d="M13.5 13.5L17 17" strokeLinecap="round" />
          </svg>
          Researching your trip
        </div>
        <span className="text-xs tabular-nums text-muted">{pct}%</span>
      </div>

      <div className="mb-4 h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
        <div
          className={`h-full rounded-full bg-brand transition-[width] duration-500 ease-out ${
            inProgress ? "shimmer-on-brand" : ""
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>

      <ul className="space-y-2">
        {steps.map((s) => (
          <li key={s.key} className="flex items-start gap-2.5">
            <StatusDot state={s.state} />
            <div className="min-w-0 flex-1">
              <div
                className={
                  "text-sm " +
                  (s.state === "done"
                    ? "text-ink-soft"
                    : s.state === "running"
                    ? "text-ink font-medium"
                    : s.state === "failed"
                    ? "text-danger"
                    : "text-muted")
                }
              >
                {s.label}
                {s.state === "running" && <RunningDots />}
              </div>
              {s.detail && s.state !== "pending" ? (
                <div className="truncate text-xs text-muted">{s.detail}</div>
              ) : null}
            </div>
          </li>
        ))}
      </ul>

      {activity?.message ? (
        <div
          key={activity.message}
          className="animate-fade-in mt-3 flex items-center gap-1.5 truncate border-t border-line pt-2 text-xs text-muted"
        >
          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-brand" style={{ animation: "var(--animate-pulse-dot)" }} />
          {activity.message}
        </div>
      ) : null}

      {/* Duration reassurance, distinct from `activity` above (which says
          WHAT is happening) — this says "this length of wait is normal,"
          so a genuinely slow-but-healthy run doesn't read as stuck. */}
      {longWaitMessage ? (
        <div
          className={`animate-fade-in flex items-start gap-1.5 text-xs text-muted ${
            activity?.message ? "mt-1.5" : "mt-3 border-t border-line pt-2"
          }`}
        >
          <span className="mt-0.5 shrink-0">⏳</span>
          {longWaitMessage}
        </div>
      ) : null}
    </div>
  );
}
