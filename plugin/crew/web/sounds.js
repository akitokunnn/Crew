"use strict";
(() => {
  let muted = false, volume = .25, context = null, output = null, timer = null, lastSound = -Infinity;
  const pending = new Set(), lastPlayed = new Map();
  const sounds = {
    decision: {priority:0, cooldown:8000, notes:[[0, 520, 520, .20, .12], [.22, 650, 650, .22, .12]]},
    error: {priority:1, cooldown:60000, notes:[[0, 240, 200, .42, .09]]},
    complete: {priority:2, cooldown:8000, notes:[[0, 580, 580, .16, .075], [.18, 435, 435, .20, .075]]},
    reply: {priority:3, cooldown:12000, notes:[[0, 460, 420, .24, .08]]}
  };
  try { muted = localStorage.getItem("crew.sound.muted") === "true"; } catch {}
  try {
    const stored = localStorage.getItem("crew.sound.volume");
    if (stored !== null && stored.trim() !== "") {
      const value = Number(stored);
      if (Number.isFinite(value) && value >= 0 && value <= 1) volume = value;
    }
  } catch {}

  function unlockAudio() {
    if (context !== null) return;
    context = new AudioContext();
    output = context.createGain();
    output.gain.setValueAtTime(muted ? 0 : volume, context.currentTime);
    output.connect(context.destination);
    void context.resume();
  }
  function updateOutput() {
    if (output === null) return;
    output.gain.setTargetAtTime(muted ? 0 : volume, context.currentTime, .015);
  }
  function setMuted(value) {
    muted = value;
    if (muted) pending.clear();
    updateOutput();
    try { localStorage.setItem("crew.sound.muted", String(muted)); } catch {}
  }
  function setVolume(value) {
    volume = value;
    if (volume === 0) pending.clear();
    updateOutput();
    try { localStorage.setItem("crew.sound.volume", String(volume)); } catch {}
  }
  function note(at, startHz, endHz, duration, peak) {
    const oscillator = context.createOscillator(), gain = context.createGain();
    // Pure sine tones stay below 650 Hz; a 35 ms attack and zero-ended release avoid sharp clicks.
    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(startHz, at);
    oscillator.frequency.exponentialRampToValueAtTime(endHz, at + duration - .025);
    gain.gain.setValueAtTime(0, at);
    gain.gain.linearRampToValueAtTime(peak, at + .035);
    gain.gain.exponentialRampToValueAtTime(peak * .015, at + duration - .025);
    gain.gain.linearRampToValueAtTime(0, at + duration);
    oscillator.connect(gain); gain.connect(output);
    oscillator.onended = () => { oscillator.disconnect(); gain.disconnect(); };
    oscillator.start(at); oscillator.stop(at + duration);
  }
  function flush() {
    timer = null;
    if (pending.size === 0) return;
    const batch = [...pending];
    pending.clear();
    if (muted || volume === 0 || context.state !== "running") return;
    const now = performance.now();
    const kind = batch.sort((a, b) => sounds[a].priority - sounds[b].priority)[0];
    for (const item of batch) lastPlayed.set(item, now);
    lastSound = now;
    const at = context.currentTime + .005;
    for (const [offset, startHz, endHz, duration, peak] of sounds[kind].notes) note(at + offset, startHz, endHz, duration, peak);
  }
  function play(kind) {
    const sound = sounds[kind], now = performance.now();
    if (muted || volume === 0 || context === null || context.state !== "running") return;
    if (lastPlayed.has(kind) && now - lastPlayed.get(kind) < sound.cooldown || now - lastSound < 1200) return;
    pending.add(kind);
    // Coalesce one state update into its highest-priority cue; never queue a later reminder.
    if (timer === null) timer = setTimeout(flush, 80);
  }

  document.addEventListener("pointerdown", unlockAudio, {once:true});
  document.addEventListener("keydown", unlockAudio, {once:true});
  window.CrewSound = Object.freeze({play, setMuted, setVolume, getSettings:() => ({muted, volume})});
})();
