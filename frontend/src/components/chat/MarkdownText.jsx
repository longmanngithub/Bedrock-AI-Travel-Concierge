// Minimal, dependency-free renderer for the short assistant replies Bedrock
// sends (2-4 sentences, occasional **bold** or a short bullet list). Full
// itinerary content is rendered by TravelTicket, not through markdown text, so
// this intentionally doesn't need a real markdown parser.
function renderInline(text, keyPrefix) {
  return text
    .split(/(\*\*[^*]+\*\*)/g)
    .filter((part) => part.length > 0)
    .map((part, i) =>
      part.startsWith("**") && part.endsWith("**") ? (
        <strong key={`${keyPrefix}-${i}`} className="font-semibold text-ink">
          {part.slice(2, -2)}
        </strong>
      ) : (
        <span key={`${keyPrefix}-${i}`}>{part}</span>
      ),
    );
}

export default function MarkdownText({ text, streaming = false, className = "" }) {
  const lines = (text || "").split("\n");
  const blocks = [];
  let currentList = null;

  lines.forEach((line) => {
    const bulletMatch = /^[-*]\s+(.*)/.exec(line.trim());
    if (bulletMatch) {
      if (!currentList) {
        currentList = [];
        blocks.push({ type: "list", items: currentList });
      }
      currentList.push(bulletMatch[1]);
    } else {
      currentList = null;
      blocks.push({ type: "line", text: line });
    }
  });

  const lastIdx = blocks.length - 1;

  return (
    <div className={`space-y-2 text-[0.95rem] leading-relaxed text-ink ${className}`}>
      {blocks.map((block, i) =>
        block.type === "list" ? (
          <ul key={i} className="list-disc space-y-1 pl-5 marker:text-muted">
            {block.items.map((item, j) => (
              <li key={j}>{renderInline(item, `${i}-${j}`)}</li>
            ))}
          </ul>
        ) : block.text.trim() === "" ? null : (
          <p key={i} className={streaming && i === lastIdx ? "stream-caret" : ""}>
            {renderInline(block.text, `${i}`)}
          </p>
        ),
      )}
    </div>
  );
}
