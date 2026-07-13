// Suggested-reply chips anchored above the composer: tappable answers to the
// clarifying question the assistant just asked. Clean, solid-background pills
// with no blur effect — the composer bar below handles its own frosted-glass
// look; these sit above it as simple, readable action chips.
export default function QuickReplies({ options = [], onPick, disabled }) {
  if (!options || options.length === 0) return null;

  return (
    <div className="pointer-events-none animate-fade-in mt-4 mb-2 flex flex-wrap items-center gap-2">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          disabled={disabled}
          onClick={() => onPick(opt.value)}
          className="pointer-events-auto rounded-full border border-line/70 bg-surface/90 px-3.5 py-1.5 text-sm font-medium text-ink-soft shadow-sm backdrop-blur-sm transition-all hover:border-brand hover:bg-brand-soft hover:text-brand disabled:cursor-not-allowed disabled:opacity-40"
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
