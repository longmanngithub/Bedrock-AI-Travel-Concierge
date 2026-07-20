"use client";

import { useState } from "react";
import { auth, assetUrl, formatError } from "../../lib/api.js";
import { useAuth } from "../../lib/AuthContext.jsx";
import ThemeToggle from "../ThemeToggle.jsx";
import { MoreIcon } from "../icons.jsx";
import SettingsModal from "./SettingsModal.jsx";

// Self-contained account control for the sidebar footer: the signed-in
// user's name/avatar with a logout + Google-link menu. The signed-out and
// loading states render nothing here — AuthProvider covers the whole app
// with a blocking loading scrim or a non-dismissible sign-in dialog in those
// cases, so this component only ever needs to handle "signed in".
export default function AuthControls() {
  const { user, loading, logout, notice, dismissNotice } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);

  async function handleLogout() {
    await logout();
    setMenuOpen(false);
  }

  if (loading || !user) return null;

  const label = user.display_name || user.email;
  const avatar = assetUrl(user.avatar_url);
  return (
    <div className="space-y-1.5">
      {notice && (
        <div
          className={`flex items-start gap-1.5 rounded-lg px-2.5 py-1.5 text-xs ${
            notice.type === "success" ? "bg-ok/10 text-ok" : "bg-danger/10 text-danger"
          }`}
        >
          <span className="flex-1">{notice.message}</span>
          <button type="button" onClick={dismissNotice} className="shrink-0 opacity-70 hover:opacity-100">
            ✕
          </button>
        </div>
      )}
      <div className="relative">
        <button
          type="button"
          onClick={() => setMenuOpen((v) => !v)}
          className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-sm text-ink-soft transition hover:bg-surface-2"
        >
          {avatar ? (
            <img src={avatar} alt="" className="h-7 w-7 shrink-0 rounded-full object-cover bg-surface-2" />
          ) : (
            <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-brand-soft text-xs font-semibold text-brand">
              {label.slice(0, 1).toUpperCase()}
            </span>
          )}
          <span className="min-w-0 flex-1 truncate text-left">{label}</span>
          <MoreIcon className="h-4 w-4 text-muted" />
        </button>
        {menuOpen && <AccountMenu user={user} onLogout={handleLogout} onClose={() => setMenuOpen(false)} />}
      </div>
    </div>
  );
}

function AccountMenu({ user, onLogout, onClose }) {
  const { refresh } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);

  async function handleUnlink() {
    setError("");
    setBusy(true);
    try {
      await auth.unlinkGoogle();
      await refresh();
    } catch (err) {
      setError(formatError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="absolute bottom-full left-0 mb-1 w-full overflow-hidden rounded-xl border border-line bg-surface shadow-lg">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2">
        <span className="flex-1 text-sm text-ink">Appearance</span>
        <ThemeToggle className="h-8 w-8" />
      </div>
      {user.google_linked ? (
        user.has_password ? (
          <button
            type="button"
            onClick={handleUnlink}
            disabled={busy}
            className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-sm text-ink transition hover:bg-surface-2 disabled:opacity-60"
          >
            <GoogleGlyph />
            <span className="flex-1">{busy ? "Disconnecting…" : "Disconnect Google"}</span>
          </button>
        ) : (
          <div className="flex items-center gap-2 px-3 py-2.5 text-sm text-muted">
            <GoogleGlyph />
            Google connected
          </div>
        )
      ) : (
        <a
          href={auth.googleStartUrl("link")}
          className="flex w-full items-center gap-2 px-3 py-2.5 text-sm text-ink transition hover:bg-surface-2"
        >
          <GoogleGlyph />
          Connect Google account
        </a>
      )}
      {error && <p className="border-t border-line px-3 py-2 text-xs text-danger">{error}</p>}
      <button
        type="button"
        onClick={() => setSettingsOpen(true)}
        className="flex w-full items-center gap-2 border-t border-line px-3 py-2.5 text-sm text-ink transition hover:bg-surface-2"
      >
        <svg viewBox="0 0 20 20" className="h-4 w-4 text-muted" fill="none" stroke="currentColor" strokeWidth="1.8">
          <path d="M10 13a3 3 0 100-6 3 3 0 000 6z" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M16.2 12.4a1.4 1.4 0 00.28 1.54l.05.05a1.7 1.7 0 11-2.4 2.4l-.05-.05a1.4 1.4 0 00-1.54-.28 1.4 1.4 0 00-.85 1.28V17.5a1.7 1.7 0 11-3.4 0v-.08a1.4 1.4 0 00-.92-1.28 1.4 1.4 0 00-1.54.28l-.05.05a1.7 1.7 0 11-2.4-2.4l.05-.05a1.4 1.4 0 00.28-1.54 1.4 1.4 0 00-1.28-.85H2.5a1.7 1.7 0 110-3.4h.08a1.4 1.4 0 001.28-.92 1.4 1.4 0 00-.28-1.54l-.05-.05a1.7 1.7 0 112.4-2.4l.05.05a1.4 1.4 0 001.54.28h.06a1.4 1.4 0 00.85-1.28V2.5a1.7 1.7 0 113.4 0v.08a1.4 1.4 0 00.85 1.28h.06a1.4 1.4 0 001.54-.28l.05-.05a1.7 1.7 0 112.4 2.4l-.05.05a1.4 1.4 0 00-.28 1.54v.06a1.4 1.4 0 001.28.85h.16a1.7 1.7 0 110 3.4h-.08a1.4 1.4 0 00-1.28.85z" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        Settings
      </button>
      <button
        type="button"
        onClick={onLogout}
        className="flex w-full items-center gap-2 border-t border-line px-3 py-2.5 text-sm text-ink transition hover:bg-surface-2"
      >
        <svg viewBox="0 0 20 20" className="h-4 w-4 text-muted" fill="none" stroke="currentColor" strokeWidth="1.8">
          <path d="M8 5V4a1 1 0 011-1h6a1 1 0 011 1v12a1 1 0 01-1 1H9a1 1 0 01-1-1v-1M11 10H3m0 0l3-3m-3 3l3 3" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        Log out
      </button>
      {settingsOpen && (
        <SettingsModal onClose={() => { setSettingsOpen(false); onClose(); }} />
      )}
    </div>
  );
}

function GoogleGlyph() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4 shrink-0"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84A11 11 0 0012 23z"/><path fill="#FBBC05" d="M5.84 14.1a6.6 6.6 0 010-4.2V7.06H2.18a11 11 0 000 9.88l3.66-2.84z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84C6.71 7.31 9.14 5.38 12 5.38z"/></svg>
  );
}
