"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { assetUrl, auth, formatError, settings } from "../../lib/api.js";
import { useAuth } from "../../lib/AuthContext.jsx";

function Toggle({ checked, onChange, disabled, label }) {
  return (
    <button type="button" role="switch" aria-label={label} aria-checked={checked} disabled={disabled} onClick={() => onChange(!checked)} className={`inline-flex h-6 w-11 shrink-0 items-center rounded-full border p-0.5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:opacity-60 ${checked ? "border-brand bg-brand" : "border-line bg-surface-2"}`}>
      <span className={`h-5 w-5 rounded-full bg-white shadow-sm transition-transform duration-200 ${checked ? "translate-x-5" : "translate-x-0"}`} />
    </button>
  );
}

function Section({ title, children }) {
  return <section className="space-y-2.5"><h3 className="text-sm font-medium text-ink">{title}</h3>{children}</section>;
}

function MemorySection() {
  const { user, updateUser } = useAuth();
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [message, setMessage] = useState("");
  async function toggle(next) {
    setBusy(true); setMessage("");
    try { updateUser(await settings.setMemoryOptIn(next)); } catch (err) { setMessage(formatError(err)); } finally { setBusy(false); }
  }
  async function remove() {
    setBusy(true); setMessage("");
    try { updateUser(await settings.deleteMemory()); setConfirmDelete(false); setMessage("Memory deleted."); } catch (err) { setMessage(formatError(err)); } finally { setBusy(false); }
  }
  return <Section title="Personalized memory">
    <div className="flex items-start justify-between gap-4"><p className="text-xs leading-relaxed text-muted">Bedrock remembers recurring interests, pace, and typical budget across trips. Off by default.</p><Toggle label="Personalized memory" checked={Boolean(user?.memory_opt_in)} onChange={toggle} disabled={busy} /></div>
    {confirmDelete ? <div className="flex items-center gap-2 rounded-lg bg-danger/10 px-2.5 py-1.5 text-xs"><span className="flex-1 text-ink">Delete everything Bedrock has learned?</span><button type="button" disabled={busy} onClick={remove} className="font-medium text-danger">Delete</button><button type="button" onClick={() => setConfirmDelete(false)} className="font-medium text-muted">Cancel</button></div> : <button type="button" onClick={() => setConfirmDelete(true)} className="text-xs font-medium text-danger hover:opacity-70">Delete my memory</button>}
    {message && <p className={`text-xs ${message === "Memory deleted." ? "text-ok" : "text-danger"}`}>{message}</p>}
  </Section>;
}

function CompletionEmailSection() {
  const { user, updateUser } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function toggle(next) {
    setBusy(true); setError("");
    try { updateUser(await settings.setCompletionEmailOptIn(next)); } catch (err) { setError(formatError(err)); } finally { setBusy(false); }
  }
  return <Section title="Email completed responses"><div className="flex items-start justify-between gap-4"><p className="text-xs leading-relaxed text-muted">Email the full final response to your verified account address when Bedrock finishes. Stopped and failed responses are never sent.</p><Toggle label="Email completed responses" checked={Boolean(user?.completion_email_opt_in)} onChange={toggle} disabled={busy} /></div>{error && <p className="text-xs text-danger">{error}</p>}</Section>;
}

function LocationSharingSection() {
  const { user, updateUser } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function toggle(next) {
    setBusy(true); setError("");
    try { updateUser(await settings.setLocationSharingOptIn(next)); } catch (err) { setError(formatError(err)); } finally { setBusy(false); }
  }
  return <Section title="Location for flight pricing"><div className="flex items-start justify-between gap-4"><p className="text-xs leading-relaxed text-muted">Share your browser location so Bedrock can look up real flight prices from where you actually are. Off by default; your browser will ask for permission the next time you plan a trip.</p><Toggle label="Location for flight pricing" checked={Boolean(user?.location_share_opt_in)} onChange={toggle} disabled={busy} /></div>{error && <p className="text-xs text-danger">{error}</p>}</Section>;
}

function ProfileSection() {
  const { user, updateUser } = useAuth();
  const inputRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Deliberately NOT gated on has_password (unlike PasswordSection below,
  // where that gate is legitimate) — a Google-only account still has a
  // picture (fetched from the Google Account, see auth/google.py) and
  // should be able to see it, and even upload a custom one to override it.
  async function upload(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    setBusy(true); setError("");
    try { updateUser(await settings.uploadAvatar(file)); } catch (err) { setError(formatError(err)); } finally { setBusy(false); e.target.value = ""; }
  }
  async function remove() {
    setBusy(true); setError("");
    try { updateUser(await settings.deleteAvatar()); } catch (err) { setError(formatError(err)); } finally { setBusy(false); }
  }
  const image = assetUrl(user.avatar_url);
  return <Section title="Profile picture"><div className="flex items-center gap-3"><div className="grid h-10 w-10 place-items-center overflow-hidden rounded-full bg-brand-soft text-sm font-semibold text-brand">{image ? <img src={image} alt="" className="h-full w-full object-cover" /> : (user.display_name || user.email).slice(0, 1).toUpperCase()}</div><p className="flex-1 text-xs text-muted">JPEG, PNG, or WebP up to 5 MB.</p><input ref={inputRef} type="file" accept="image/jpeg,image/png,image/webp" onChange={upload} className="hidden" /><button type="button" disabled={busy} onClick={() => inputRef.current?.click()} className="rounded-full border border-line px-2.5 py-1 text-xs font-medium text-ink-soft">Change</button>{user.avatar_url && <button type="button" disabled={busy} onClick={remove} className="text-xs font-medium text-danger">Remove</button>}</div>{error && <p className="text-xs text-danger">{error}</p>}</Section>;
}

function PasswordSection() {
  const { user, updateUser } = useAuth();
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  if (!user?.has_password) return null;
  async function submit(e) {
    e.preventDefault(); setMessage("");
    if (next !== confirm) return setMessage("New passwords do not match.");
    setBusy(true);
    try { updateUser(await auth.changePassword(current, next, confirm)); setCurrent(""); setNext(""); setConfirm(""); setOpen(false); setMessage("Password updated. Other sessions were signed out."); } catch (err) { setMessage(formatError(err)); } finally { setBusy(false); }
  }
  return <Section title="Password">{!open ? <button type="button" onClick={() => { setMessage(""); setOpen(true); }} className="rounded-full border border-line px-3 py-1.5 text-xs font-medium text-ink-soft hover:border-brand hover:text-brand">Change password</button> : <form onSubmit={submit} className="space-y-2"><input required type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} placeholder="Current password" className="w-full rounded-lg border border-line bg-canvas px-3 py-2 text-sm text-ink outline-none focus:border-brand"/><input required type="password" autoComplete="new-password" minLength={12} value={next} onChange={(e) => setNext(e.target.value)} placeholder="New password" className="w-full rounded-lg border border-line bg-canvas px-3 py-2 text-sm text-ink outline-none focus:border-brand"/><input required type="password" autoComplete="new-password" minLength={12} value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder="Confirm new password" className="w-full rounded-lg border border-line bg-canvas px-3 py-2 text-sm text-ink outline-none focus:border-brand"/><p className="text-[11px] text-muted">Use 12+ characters with uppercase, lowercase, number, and symbol.</p><div className="flex justify-end gap-2"><button type="button" onClick={() => setOpen(false)} className="rounded-full px-3 py-1.5 text-xs text-muted">Cancel</button><button disabled={busy} className="rounded-full bg-brand px-3 py-1.5 text-xs font-medium text-on-brand disabled:opacity-60">{busy ? "Updating…" : "Update password"}</button></div></form>}{message && <p className={`text-xs ${message.startsWith("Password updated") ? "text-ok" : "text-danger"}`}>{message}</p>}</Section>;
}

function TelegramSection() {
  const { user, updateUser, refresh } = useAuth();
  const [link, setLink] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pollRef = useRef(null);
  useEffect(() => () => clearInterval(pollRef.current), []);
  async function connect() {
    setBusy(true); setError("");
    try { const result = await settings.telegramLinkCode(); setLink(result); clearInterval(pollRef.current); pollRef.current = setInterval(async () => { const fresh = await refresh(); if (fresh?.telegram_linked) { clearInterval(pollRef.current); setLink(null); } }, 3000); } catch (err) { setError(formatError(err)); } finally { setBusy(false); }
  }
  async function disconnect() { setBusy(true); setError(""); try { updateUser(await settings.telegramUnlink()); } catch (err) { setError(formatError(err)); } finally { setBusy(false); } }
  return <Section title="Telegram delivery"><div className="flex items-start justify-between gap-4"><p className="text-xs leading-relaxed text-muted">Bedrock sends the boarding-pass PDF here automatically whenever a plan finishes.</p>{user?.telegram_linked ? <button type="button" onClick={disconnect} disabled={busy} className="rounded-full border border-line px-2.5 py-1 text-xs font-medium text-ink-soft">Disconnect</button> : <button type="button" onClick={connect} disabled={busy} className="rounded-full bg-brand px-2.5 py-1 text-xs font-medium text-on-brand">Connect</button>}</div>{link && <div className="rounded-xl border border-line bg-canvas p-3 text-xs text-muted">{link.deep_link ? <a href={link.deep_link} target="_blank" rel="noopener noreferrer" className="block rounded-lg bg-brand px-3 py-2 text-center font-medium text-on-brand">Open Telegram to connect</a> : <>Send <code>/start {link.code}</code> to the Bedrock bot.</>}<p className="mt-2 text-center">Waiting for confirmation…</p></div>}{error && <p className="text-xs text-danger">{error}</p>}</Section>;
}

export default function SettingsModal({ onClose }) {
  if (typeof document === "undefined") return null;
  return createPortal(<div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4" onClick={onClose}><div className="thin-scroll max-h-[90dvh] w-full max-w-sm space-y-5 overflow-y-auto rounded-2xl border border-line bg-surface p-6 shadow-xl" onClick={(e) => e.stopPropagation()}><div className="flex items-center justify-between"><h2 className="text-lg font-semibold text-ink">Settings</h2><button type="button" onClick={onClose} aria-label="Close" className="text-muted hover:text-ink">✕</button></div><MemorySection /><div className="border-t border-line" /><CompletionEmailSection /><div className="border-t border-line" /><LocationSharingSection /><ProfileSection /><PasswordSection /><div className="border-t border-line" /><TelegramSection /></div></div>, document.body);
}
