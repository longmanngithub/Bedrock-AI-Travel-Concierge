"use client";

import { useEffect, useState } from "react";

// Honest "building your itinerary" feedback for the ~40-90s crew run: a rotating
// list of plausible phase strings, not a real progress bar. It never claims
// completion — the parent unmounts it the instant the real itinerary arrives,
// so it can't outrun (or lie about) the actual backend state.
const STAGES = [
  "Reading your trip details…",
  "Scouting the destination…",
  "Getting to know your travel style…",
  "Finding places to stay…",
  "Balancing the budget…",
  "Hand-picking places to eat…",
  "Drafting the day-by-day plan…",
  "Having the reviewer double-check it…",
  "Putting your boarding pass together…",
];

export default function StagingIndicator({ startedAt }) {
  const getElapsedStageIdx = () => {
    if (!startedAt) return 0;
    const elapsed = Date.now() - startedAt;
    const idx = Math.floor(elapsed / 2600);
    return Math.min(idx, STAGES.length - 1);
  };

  const [stageIdx, setStageIdx] = useState(getElapsedStageIdx);

  useEffect(() => {
    setStageIdx(getElapsedStageIdx());

    const id = setInterval(() => {
      setStageIdx((i) => Math.min(i + 1, STAGES.length - 1));
    }, 2600);
    return () => clearInterval(id);
  }, [startedAt]);

  return (
    <div className="flex items-center gap-2.5 text-sm text-muted">
      <span className="flex gap-1">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="h-1.5 w-1.5 rounded-full bg-brand"
            style={{ animation: "var(--animate-pulse-dot)", animationDelay: `${i * 160}ms` }}
          />
        ))}
      </span>
      <span>{STAGES[stageIdx]}</span>
    </div>
  );
}
