"use client";

import { useEffect, useState } from "react";
import { MoonIcon, SunIcon } from "./icons.jsx";

// Light/dark switch. The actual class is applied pre-paint by the inline script
// in layout.jsx (so there's no flash); this only reflects and toggles it, and
// persists the choice. Multiple toggles can be mounted at once (sidebar footer
// + mobile header), so a broadcast event keeps every instance in sync with the
// live DOM state after any of them flips it.
const THEME_EVENT = "bedrock:themechange";

export default function ThemeToggle({ className = "" }) {
  const [isDark, setIsDark] = useState(false);

  useEffect(() => {
    const sync = () => setIsDark(document.documentElement.classList.contains("dark"));
    sync();
    window.addEventListener(THEME_EVENT, sync);
    return () => window.removeEventListener(THEME_EVENT, sync);
  }, []);

  function toggle() {
    const root = document.documentElement;
    const next = !root.classList.contains("dark");
    // Scope the eased color transition to the moment of the toggle only (see
    // .theme-transition in globals.css) — a permanent global transition would
    // fight every other purpose-built hover/focus transition in the app.
    root.classList.add("theme-transition");
    root.classList.toggle("dark", next);
    try {
      localStorage.setItem("bedrock:theme", next ? "dark" : "light");
    } catch {
      /* storage unavailable — the toggle still works for this session */
    }
    setIsDark(next);
    window.dispatchEvent(new Event(THEME_EVENT));
    window.setTimeout(() => root.classList.remove("theme-transition"), 400);
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
      title={isDark ? "Light mode" : "Dark mode"}
      className={`flex h-11 w-11 items-center justify-center rounded-full text-muted transition-colors hover:bg-surface-2 hover:text-ink ${className}`}
    >
      {isDark ? <SunIcon className="h-[18px] w-[18px]" /> : <MoonIcon className="h-[18px] w-[18px]" />}
    </button>
  );
}
