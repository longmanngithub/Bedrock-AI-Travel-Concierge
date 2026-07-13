"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import Sidebar from "../components/Sidebar.jsx";
import ChatWindow from "../components/chat/ChatWindow.jsx";
import ThemeToggle from "../components/ThemeToggle.jsx";
import Logo from "../components/Logo.jsx";
import { MenuIcon } from "../components/icons.jsx";
import {
  clearAllConversations,
  createConversation,
  deleteConversation,
  getActiveId,
  setActiveId as persistActiveId,
  getConversation,
} from "../lib/conversations.js";

export default function ClientPage({ initialHasConversations, initialConversationCount, initialActiveStructure }) {
  const [activeId, setActiveId] = useState(null);
  const [version, setVersion] = useState(0);
  const [mounted, setMounted] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Background stream states mapped by conversation ID
  const [activeStreams, setActiveStreams] = useState({});
  const streamControllersRef = useRef({});

  const updateStreamState = useCallback((convId, state) => {
    setActiveStreams((prev) => {
      if (state === null) {
        const next = { ...prev };
        delete next[convId];
        return next;
      }
      return {
        ...prev,
        [convId]: {
          ...(prev[convId] || {}),
          ...state,
        },
      };
    });
  }, []);

  const registerStreamController = useCallback((convId, controller) => {
    streamControllersRef.current[convId]?.abort();
    streamControllersRef.current[convId] = controller;
  }, []);

  const clearStreamController = useCallback((convId) => {
    delete streamControllersRef.current[convId];
  }, []);

  const abortStream = useCallback((convId) => {
    streamControllersRef.current[convId]?.abort();
    delete streamControllersRef.current[convId];
    updateStreamState(convId, null);
  }, [updateStreamState]);

  // Clean up all requests on unmount
  useEffect(() => {
    return () => {
      Object.values(streamControllersRef.current).forEach((c) => c.abort());
    };
  }, []);

  // sessionStorage tracks the tab session. If this is the first load in a new tab/session,
  // we start the user on the Welcome screen (activeId = null) regardless of past chats.
  // If it's a page reload, we restore the active chat normally.
  useEffect(() => {
    const isSessionActive = window.sessionStorage.getItem("bedrock:session_active") === "true";
    if (!isSessionActive) {
      persistActiveId(null);
      setActiveId(null);
      window.sessionStorage.setItem("bedrock:session_active", "true");
    } else {
      setActiveId(getActiveId());
    }
    setMounted(true);
  }, []);

  const bumpVersion = () => setVersion((v) => v + 1);

  function handleNewChat() {
    // If the active chat is already new/empty, just close the sidebar drawer
    // and keep it active instead of spawning duplicate empty entries.
    if (activeId) {
      const activeConv = getConversation(activeId);
      if (activeConv && activeConv.messages.length === 0) {
        setDrawerOpen(false);
        return;
      }
    }

    const conversation = createConversation();
    setActiveId(conversation.id);
    setDrawerOpen(false);
    bumpVersion();
  }

  function handleSelectConversation(id) {
    persistActiveId(id);
    setActiveId(id);
  }

  function handleDeleteConversation(id) {
    abortStream(id);
    deleteConversation(id);
    setActiveId(getActiveId());
    bumpVersion();
  }

  function handleClearAll() {
    Object.values(streamControllersRef.current).forEach((c) => c.abort());
    streamControllersRef.current = {};
    setActiveStreams({});
    clearAllConversations();
    setActiveId(null);
    bumpVersion();
  }

  function ensureActiveConversation() {
    if (activeId) return activeId;
    const conversation = createConversation();
    setActiveId(conversation.id);
    bumpVersion();
    return conversation.id;
  }

  const sidebarProps = {
    activeId,
    onSelectConversation: handleSelectConversation,
    onNewChat: handleNewChat,
    onDeleteConversation: handleDeleteConversation,
    onClearAll: handleClearAll,
    mounted,
    version,
    initialConversationCount,
    activeStreams,
  };

  return (
    <div className="flex h-[100dvh] overflow-hidden bg-canvas">
      {/* Desktop sidebar */}
      <div className="hidden shrink-0 py-3 pl-3 md:block">
        <Sidebar {...sidebarProps} />
      </div>

      {/* Mobile drawer — always mounted (never conditionally rendered) so it can
          slide in AND out with a real transition; closed state just parks it
          off-screen and inert rather than unmounting it mid-animation. */}
      <div
        className={`fixed inset-0 z-40 md:hidden ${drawerOpen ? "" : "pointer-events-none"}`}
        aria-hidden={!drawerOpen}
      >
        <div
          className={`absolute inset-0 bg-ink/40 transition-opacity duration-200 ease-out ${
            drawerOpen ? "opacity-100" : "opacity-0"
          }`}
          onClick={(e) => {
            e.preventDefault();
            setDrawerOpen(false);
          }}
        />
        <div
          className={`absolute inset-y-0 left-0 w-[86%] max-w-[320px] shadow-2xl transition-transform duration-200 ease-out ${
            drawerOpen ? "translate-x-0" : "-translate-x-full"
          }`}
        >
          <Sidebar {...sidebarProps} onClose={() => setDrawerOpen(false)} />
        </div>
      </div>

      {/* Main */}
      <main className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
        {/* Mobile top bar */}
        <header className="flex items-center gap-3 border-b border-line px-4 py-3 md:hidden">
          <button
            type="button"
            onClick={() => setDrawerOpen(true)}
            onTouchStart={(e) => {
              e.preventDefault();
              setDrawerOpen(true);
            }}
            aria-label="Open menu"
            className="flex h-11 w-11 items-center justify-center rounded-full text-ink-soft hover:bg-surface-2"
          >
            <MenuIcon />
          </button>
          <div className="flex items-center gap-2">
            <Logo size={26} />
            <span className="text-[15px] font-bold text-ink">Bedrock</span>
          </div>
          <ThemeToggle className="ml-auto" />
        </header>

        <ChatWindow
          activeId={activeId}
          ensureActiveConversation={ensureActiveConversation}
          bumpVersion={bumpVersion}
          mounted={mounted}
          initialHasConversations={initialHasConversations}
          initialActiveStructure={initialActiveStructure}
          streaming={activeStreams[activeId]?.streaming || null}
          stagingType={activeStreams[activeId]?.stagingType || null}
          isBusy={activeStreams[activeId]?.isBusy || false}
          startedAt={activeStreams[activeId]?.startedAt || null}
          updateStreamState={updateStreamState}
          registerStreamController={registerStreamController}
          clearStreamController={clearStreamController}
        />
      </main>
    </div>
  );
}
