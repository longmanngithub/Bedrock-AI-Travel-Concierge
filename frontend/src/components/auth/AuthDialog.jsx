"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { auth, formatError } from "../../lib/api.js";
import { EyeIcon, EyeOffIcon } from "../icons.jsx";

function PasswordField({ value, onChange, placeholder, autoComplete, minLength = 1 }) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative">
      <input
        type={show ? "text" : "password"}
        required
        minLength={minLength}
        placeholder={placeholder}
        autoComplete={autoComplete}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-xl border border-line bg-canvas px-3 py-2.5 pr-10 text-sm text-ink outline-none focus:border-brand"
      />
      <button type="button" tabIndex={-1} onClick={() => setShow((v) => !v)} aria-label={show ? "Hide password" : "Show password"} className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-muted transition hover:text-ink">
        {show ? <EyeOffIcon className="h-4 w-4" /> : <EyeIcon className="h-4 w-4" />}
      </button>
    </div>
  );
}

function VerificationCodeField({ value, onChange, disabled }) {
  const inputRef = useRef(null);
  const [isFocused, setIsFocused] = useState(false);
  const digits = Array.from({ length: 6 }, (_, index) => value[index] || "");
  const activeIndex = value.length < 6 ? value.length : null;

  useEffect(() => {
    if (!disabled && value.length === 0) inputRef.current?.focus();
  }, [disabled, value.length]);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between px-1">
        <label htmlFor="verification-code" className="text-xs font-medium text-ink-soft">Verification code</label>
        <span className="text-xs tabular-nums text-muted">{value.length}/6</span>
      </div>
      <div className="relative rounded-2xl">
        <input
          ref={inputRef}
          id="verification-code"
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          required
          pattern="[0-9]{6}"
          maxLength={6}
          value={value}
          onChange={(e) => onChange(e.target.value.replace(/\D/g, ""))}
          onFocus={() => setIsFocused(true)}
          onBlur={() => setIsFocused(false)}
          disabled={disabled}
          aria-describedby="verification-code-hint"
          className="absolute inset-0 z-10 h-full w-full cursor-text opacity-0 disabled:cursor-not-allowed"
        />
        <div aria-hidden="true" className="grid grid-cols-6 gap-2">
          {digits.map((digit, index) => (
            <div
              key={index}
              className={`grid aspect-square min-w-0 place-items-center rounded-xl border text-xl font-semibold tabular-nums transition ${
                digit ? "border-line bg-brand-soft text-ink" : "border-line bg-canvas text-muted"
              } ${isFocused && index === activeIndex ? "border-brand ring-2 ring-brand/20" : ""}`}
            >
              {digit || "–"}
            </div>
          ))}
        </div>
      </div>
      <p id="verification-code-hint" className="px-1 text-xs text-muted">Enter or paste the six-digit code from your email.</p>
    </div>
  );
}

function passwordChecks(value) {
  return [
    [value.length >= 12, "12 or more characters"],
    [/[a-z]/.test(value), "a lowercase letter"],
    [/[A-Z]/.test(value), "an uppercase letter"],
    [/\d/.test(value), "a number"],
    // Must match the backend's definition exactly (auth/passwords.py:
    // "not alnum and not whitespace") — \w wrongly excludes underscore
    // as a symbol, which previously rejected passwords the backend accepts.
    [/[^a-zA-Z0-9\s]/.test(value), "a symbol"],
  ];
}

export default function AuthDialog({ onClose, onAuthed, dismissible = true, initialVerificationEmail, onVerificationRequired }) {
  const [mode, setMode] = useState(initialVerificationEmail ? "verify" : "login");
  const [email, setEmail] = useState(initialVerificationEmail || "");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (initialVerificationEmail) {
      setEmail(initialVerificationEmail);
      setMode("verify");
    }
  }, [initialVerificationEmail]);

  const checks = useMemo(() => passwordChecks(password), [password]);
  const strong = checks.every(([ok]) => ok);

  function switchMode(next) {
    setMode(next);
    setError("");
    setNotice("");
    setPassword("");
    setConfirmPassword("");
    setCode("");
  }

  async function submit(e) {
    e.preventDefault();
    setError("");
    setNotice("");
    if (mode === "register") {
      if (!strong) return setError("Choose a stronger password before continuing.");
      if (password !== confirmPassword) return setError("Those passwords don't match.");
    }
    setBusy(true);
    try {
      if (mode === "verify") {
        const user = await auth.verifyEmail(email, code);
        onAuthed(user);
        return;
      }
      if (mode === "register") {
        const result = await auth.register(email, password);
        setEmail(result.email || email);
        onVerificationRequired?.(result.email || email);
        switchMode("verify");
        setNotice("We sent a six-digit verification code to your email.");
        return;
      }
      const user = await auth.login(email, password);
      onAuthed(user);
    } catch (err) {
      if (err?.code === "E_EMAIL_UNVERIFIED") {
        onVerificationRequired?.(email);
        switchMode("verify");
        setNotice("Verify your email to finish signing in.");
      } else {
        setError(formatError(err));
      }
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    setError("");
    setNotice("");
    setBusy(true);
    try {
      await auth.resendVerification(email);
      setCode("");
      setNotice("A new verification code was sent.");
    } catch (err) {
      setError(formatError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4" onClick={dismissible ? onClose : undefined}>
      <div className="w-full max-w-sm rounded-2xl border border-line bg-surface p-6 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <h2 className="mb-1 text-lg font-semibold text-ink">{mode === "verify" ? "Verify your email" : mode === "login" ? "Welcome back" : "Create your account"}</h2>
        <p className="mb-4 text-sm text-muted">{mode === "verify" ? "Enter the six-digit code we sent before accessing your saved trips." : "Sign in to save trips and sync across devices."}</p>

        {mode !== "verify" && <>
          <a href={auth.googleStartUrl("login")} className="mb-3 flex w-full items-center justify-center gap-2 rounded-xl border border-line bg-surface px-3 py-2.5 text-sm font-medium text-ink transition hover:bg-surface-2">
            <GoogleGlyph /> Continue with Google
          </a>
          <div className="my-3 flex items-center gap-3 text-xs text-muted"><span className="h-px flex-1 bg-line" /> or <span className="h-px flex-1 bg-line" /></div>
        </>}

        <form onSubmit={submit} className="space-y-2.5">
          <input type="email" required placeholder="you@example.com" value={email} autoComplete="email" readOnly={mode === "verify" && Boolean(initialVerificationEmail)} onChange={(e) => setEmail(e.target.value)} className="w-full rounded-xl border border-line bg-canvas px-3 py-2.5 text-sm text-ink outline-none focus:border-brand read-only:opacity-75" />
          {mode === "verify" ? (
            <VerificationCodeField value={code} onChange={setCode} disabled={busy} />
          ) : <>
            <PasswordField value={password} onChange={setPassword} placeholder="Password" autoComplete={mode === "login" ? "current-password" : "new-password"} minLength={mode === "register" ? 12 : 1} />
            {mode === "register" && <>
              <ul className="grid grid-cols-2 gap-x-2 gap-y-1 px-1 text-[11px] text-muted">
                {checks.map(([ok, label]) => <li key={label} className={ok ? "text-ok" : ""}>{ok ? "✓" : "•"} {label}</li>)}
              </ul>
              <PasswordField value={confirmPassword} onChange={setConfirmPassword} placeholder="Confirm password" autoComplete="new-password" minLength={12} />
            </>}
          </>}
          {notice && <p className="text-sm text-ok">{notice}</p>}
          {error && <p className="text-sm text-danger">{error}</p>}
          <button type="submit" disabled={busy || (mode === "verify" && code.length !== 6)} className="w-full rounded-xl bg-brand px-3 py-2.5 text-sm font-medium text-on-brand transition hover:bg-brand-strong disabled:opacity-60">
            {busy ? "…" : mode === "verify" ? "Verify email" : mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>

        {mode === "verify" ? <button type="button" disabled={busy} onClick={resend} className="mt-3 w-full text-center text-sm text-brand hover:underline disabled:opacity-60">Resend code</button> : <button type="button" onClick={() => switchMode(mode === "login" ? "register" : "login")} className="mt-3 w-full text-center text-sm text-brand hover:underline">{mode === "login" ? "New here? Create an account" : "Already have an account? Sign in"}</button>}
      </div>
    </div>
  );
}

function GoogleGlyph() {
  return <svg viewBox="0 0 24 24" className="h-4 w-4"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84A11 11 0 0012 23z"/><path fill="#FBBC05" d="M5.84 14.1a6.6 6.6 0 010-4.2V7.06H2.18a11 11 0 000 9.88l3.66-2.84z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84C6.71 7.31 9.14 5.38 12 5.38z"/></svg>;
}
