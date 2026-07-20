import "./globals.css";

const TITLE = "Bedrock · AI Travel Concierge";
const DESCRIPTION =
  "Plan your next trip with Bedrock — a friendly AI travel concierge that builds you a full, personalized itinerary.";

export const metadata = {
  title: TITLE,
  description: DESCRIPTION,
  // The whole app sits behind auth (see AuthContext.jsx) with no separate
  // public marketing surface to index — noindex here rather than let a
  // crawler land on a login wall. robots.ts carries the same directive for
  // crawlers that fetch robots.txt before rendering the page.
  robots: { index: false, follow: false },
  openGraph: {
    title: TITLE,
    description: DESCRIPTION,
    type: "website",
    siteName: "Bedrock",
  },
  twitter: {
    card: "summary",
    title: TITLE,
    description: DESCRIPTION,
  },
};

export const viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#0d9488",
  // Lets safe-area-inset-* env() vars resolve (notch/home-indicator), used by
  // the floating composer so it clears the iOS home indicator.
  viewportFit: "cover",
};

// Applied before first paint so the saved theme never flashes the wrong colors
// on load. Defaults to light (the reference design) when nothing is stored.
const noFlashTheme = `
(function () {
  try {
    var t = localStorage.getItem("bedrock:theme");
    if (t === "dark") document.documentElement.classList.add("dark");
  } catch (e) {}
})();
`;

export default function RootLayout({ children }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: noFlashTheme }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
