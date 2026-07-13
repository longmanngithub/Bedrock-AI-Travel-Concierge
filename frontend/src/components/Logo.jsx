// Bedrock's mark — served from /public/logo.png so it stays a single source of
// truth for the brand asset (swap that file to rebrand, no code changes).
export default function Logo({ size = 32, className = "" }) {
  return (
    // eslint-disable-next-line @next/next/no-img-element -- tiny fixed-size icon, next/image is overkill
    <img
      src="/logo.png"
      alt="Bedrock"
      width={size}
      height={size}
      className={`shrink-0 object-contain ${className}`}
    />
  );
}
