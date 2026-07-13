// Generic shimmering placeholder block — the building unit for every skeleton
// loading state in the app (sidebar rows, the boarding-pass preview). The
// `.shimmer` class (see globals.css) carries a theme-aware sweeping highlight.
export default function Skeleton({ className = "" }) {
  return <div className={`shimmer rounded-md ${className}`} />;
}
