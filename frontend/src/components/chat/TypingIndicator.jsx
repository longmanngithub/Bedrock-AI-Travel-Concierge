// Three bouncing dots — the lightweight "thinking / typing" placeholder shown
// while the concierge's reply is still being composed. This is what a follow-up
// question shows, never the boarding-pass skeleton (that's for the final plan).
export default function TypingIndicator() {
  return (
    <span className="inline-flex items-center gap-1 align-middle">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="h-1.5 w-1.5 rounded-full bg-muted"
          style={{ animation: "var(--animate-pulse-dot)", animationDelay: `${i * 160}ms` }}
        />
      ))}
    </span>
  );
}
