const FRAME_MS = 32;
const MAX_FRAMES = 36;

export function createReplyReveal(render, signal) {
  const received = [];
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  let displayed = "";
  let cursor = 0;
  let timer;
  let resolveDrain;

  function stopTimer() {
    clearTimeout(timer);
    timer = undefined;
    resolveDrain?.();
    resolveDrain = undefined;
  }

  function finish() {
    stopTimer();
    if (signal.aborted || cursor === received.length) return;
    displayed += received.slice(cursor).join("");
    cursor = received.length;
    render(displayed);
  }

  function tick() {
    timer = undefined;
    if (signal.aborted || document.hidden) {
      finish();
      return;
    }

    const count = Math.max(3, Math.ceil(received.length / MAX_FRAMES));
    const end = Math.min(cursor + count, received.length);
    displayed += received.slice(cursor, end).join("");
    cursor = end;
    render(displayed);
    schedule();
  }

  function schedule() {
    if (cursor === received.length) stopTimer();
    else if (timer === undefined) timer = setTimeout(tick, FRAME_MS);
  }

  signal.addEventListener("abort", stopTimer, { once: true });
  return {
    append(text, immediate) {
      if (signal.aborted || !text) return;
      for (const character of text) received.push(character);
      if (immediate || document.hidden || reducedMotion.matches) {
        finish();
      } else {
        schedule();
      }
    },
    drain() {
      if (cursor === received.length || signal.aborted) return;
      return new Promise((resolve) => { resolveDrain = resolve; });
    },
    finish,
    dispose() {
      stopTimer();
      signal.removeEventListener("abort", stopTimer);
    },
  };
}
