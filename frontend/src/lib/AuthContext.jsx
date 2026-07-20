"use client";

// Single source of truth for the signed-in user, shared by the sidebar account
// control AND the chat window. The whole app is gated on `user`: while the
// initial session check is in flight a blocking loading scrim covers the
// page, and the moment it resolves to "signed out" a non-dismissible sign-in
// dialog covers it instead — nothing behind either overlay is reachable.
// There is no anonymous mode; every screen requires an account.
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { auth, geo, setAuthLostHandler, setLocationHint } from "./api.js";
import AuthDialog from "../components/auth/AuthDialog.jsx";
import Logo from "../components/Logo.jsx";

const AuthCtx = createContext(null);

// The access cookie lives 10 minutes (backend/app/config.py); refresh well
// before that so an active session never actually hits the wall — a crew run
// alone can take longer than 10 minutes. This is what makes the 30-day
// refresh cookie meaningful instead of dead weight: without it, EVERY api.js
// call (and the job-progress EventSource, which can't retry itself the way a
// fetch can) starts failing the moment 10 idle-ish minutes pass.
const SILENT_REFRESH_INTERVAL_MS = 5 * 60 * 1000;

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState(null); // { type: "success" | "error", message }
  const [pendingVerificationEmail, setPendingVerificationEmail] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const me = await auth.me();
      setUser(me);
      return me;
    } catch {
      setUser(null);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Pick up the redirect back from a Google sign-in/link round trip
  // (`?linked=google` or `?auth_error=...` set by the backend callback) and
  // scrub the query string so a reload doesn't re-show the notice.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const linked = params.get("linked");
    const authError = params.get("auth_error");
    const verifyEmail = params.get("verify_email");
    if (!linked && !authError && !verifyEmail) return;

    if (verifyEmail) {
      setPendingVerificationEmail(verifyEmail);
    }
    if (linked === "google") {
      setNotice({ type: "success", message: "Google account connected." });
      refresh();
    } else if (authError === "oauth_taken") {
      setNotice({ type: "error", message: "That Google account is already linked to a different account." });
    } else if (authError) {
      setNotice({ type: "error", message: "We couldn't complete sign-in with Google. Please try again." });
    }

    params.delete("linked");
    params.delete("auth_error");
    params.delete("verify_email");
    const qs = params.toString();
    window.history.replaceState({}, "", window.location.pathname + (qs ? `?${qs}` : ""));
  }, [refresh]);

  const dismissNotice = useCallback(() => setNotice(null), []);
  const updateUser = useCallback((nextUser) => {
    if (nextUser) setUser(nextUser);
    return nextUser;
  }, []);

  // Drop the session client-side so the gate re-covers the app immediately —
  // used both for an explicit logout and for a mid-session 401 that a silent
  // refresh couldn't recover (the refresh token itself expired or was
  // revoked), since in both cases the correct next step is the same: sign in
  // again before anything else is usable.
  const forceReauth = useCallback(() => setUser(null), []);

  // api.js can't import this context (it would be circular — this file
  // already imports api.js), so it exposes a setter instead: any request
  // that fails auth even after a silent-refresh attempt calls this.
  useEffect(() => {
    setAuthLostHandler(forceReauth);
    return () => setAuthLostHandler(null);
  }, [forceReauth]);

  // Proactive silent refresh while a session is active — keeps the access
  // cookie perpetually valid so nothing (including the job-progress
  // EventSource, which has no retry hook of its own) ever actually hits the
  // 10-minute wall during normal use. Uses silentRefresh (not refresh) and
  // swallows all errors here: this is an optimistic background call, and a
  // single transient failure (a network blip, a brief backend restart) must
  // not force a visible logout while the user is just idly reading — a
  // refresh that genuinely fails still gets caught by apiFetch's own
  // retry-on-401 path the next time an actual user action hits the API,
  // which calls forceReauth.
  useEffect(() => {
    if (!user) return;
    const id = setInterval(() => {
      auth.silentRefresh().catch(() => {});
    }, SILENT_REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, [user]);

  // Opt-in location capture for accurate flight pricing (see routers/geo.py
  // and Settings > Location for flight pricing). Runs once per session the
  // moment the toggle is on — not re-triggered by unrelated `user` updates
  // (avatar changes, etc.) thanks to the ref guard — and the browser's own
  // permission grant makes every later call silent. Any failure (permission
  // denied, no geolocation support, geocoding down) just leaves the hint
  // unset; it must never block or interrupt chat.
  const locationCapturedRef = useRef(false);
  useEffect(() => {
    if (!user?.location_share_opt_in) {
      locationCapturedRef.current = false;
      setLocationHint(null);
      return;
    }
    if (locationCapturedRef.current) return;
    if (typeof navigator === "undefined" || !navigator.geolocation) return;
    locationCapturedRef.current = true;
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        geo
          .reverseGeocode(pos.coords.latitude, pos.coords.longitude)
          .then((result) => setLocationHint(result?.origin))
          .catch(() => {});
      },
      () => {},
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 3600000 }
    );
  }, [user?.location_share_opt_in]);

  async function logout() {
    await auth.logout().catch(() => {});
    setUser(null);
  }

  const value = { user, loading, refresh, updateUser, logout, forceReauth, notice, dismissNotice };
  const showGate = !loading && !user;

  return (
    <AuthCtx.Provider value={value}>
      {children}
      {loading && <LoadingGate />}
      {showGate && (
        <AuthDialog
          dismissible={false}
          initialVerificationEmail={pendingVerificationEmail}
          onVerificationRequired={(email) => setPendingVerificationEmail(email)}
          onAuthed={(u) => {
            setPendingVerificationEmail(null);
            setUser(u);
          }}
        />
      )}
    </AuthCtx.Provider>
  );
}

// Covers the page during the initial session check so nothing is clickable
// before we know whether a sign-in prompt is needed — avoids a flash of an
// interactive-looking app that then yanks a modal over it a beat later.
function LoadingGate() {
  return (
    <div className="fixed inset-0 z-[60] grid place-items-center bg-canvas">
      <div className="animate-pulse">
        <Logo size={40} />
      </div>
    </div>
  );
}

export function useAuth() {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
