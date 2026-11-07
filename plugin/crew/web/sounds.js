"use strict";
(() => {
  let catalog = null, preferences = null, context = null, output = null, timer = null;
  let initialized = false, baseline = 0, revision = -1;
  const observed = new Map(), pending = new Map(), lastPlayed = new Map();
  const decoded = new Map(), decoding = new Map();
  const soundError = error => document.dispatchEvent(new CustomEvent("crew:sound-error", {detail:error.message}));
  const prefix = "crew.sound.event.", client = Math.random().toString(36).slice(2);
  let claimSequence = 0;
  const legacy = {muted:false, volume:.25};
  try {
    legacy.muted = localStorage.getItem("crew.sound.muted") === "true";
    const value = localStorage.getItem("crew.sound.volume");
    if (value !== null && value.trim() && Number.isFinite(Number(value)) && Number(value) >= 0 && Number(value) <= 1) legacy.volume = Number(value);
  } catch { /* Storage restrictions do not prevent in-page preferences. */ }
  function getSettings() { return {...catalog?.defaults, ...legacy, ...preferences}; }
  function updateOutput() {
    if (!output) return;
    const settings = getSettings();
    output.gain.setTargetAtTime(settings.muted ? 0 : settings.volume, context.currentTime, .015);
  }
  function configure(value, nextCatalog, nextRevision) {
    if (nextCatalog) {
      catalog = nextCatalog;
      for (const id of new Set([...decoded.keys(), ...decoding.keys()])) if (!catalog.presets[id]) forget(id);
    }
    if (nextRevision < revision) return;
    revision = nextRevision;
    if (value) preferences = {...value};
    updateOutput();
  }
  async function unlockAudio() {
    if (!context) {
      context = new AudioContext(); output = context.createGain();
      output.connect(context.destination); updateOutput();
    }
    if (context.state !== "running") await context.resume();
    return context.state === "running";
  }
  function note(destination, at, startHz, endHz, duration, peak) {
    const oscillator = context.createOscillator(), gain = context.createGain();
    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(startHz, at);
    oscillator.frequency.exponentialRampToValueAtTime(endHz, at + duration - .025);
    gain.gain.setValueAtTime(0, at);
    gain.gain.linearRampToValueAtTime(peak, at + .035);
    gain.gain.exponentialRampToValueAtTime(peak * .015, at + duration - .025);
    gain.gain.linearRampToValueAtTime(0, at + duration);
    oscillator.connect(gain); gain.connect(destination);
    oscillator.onended = () => { oscillator.disconnect(); gain.disconnect(); };
    oscillator.start(at); oscillator.stop(at + duration);
  }
  function presetFor(kind, settings) {
    const cue = catalog.kinds[kind];
    return cue.setting ? {id:settings[cue.setting], preset:catalog.presets[settings[cue.setting]]} : {id:kind, preset:cue};
  }
  function destinationFor(volume, duration) {
    let destination = output;
    if (volume !== undefined) {
      destination = context.createGain(); destination.gain.value = volume; destination.connect(context.destination);
      const previewOutput = destination;
      setTimeout(() => previewOutput.disconnect(), duration);
    }
    return destination;
  }
  function renderNotes(notes, volume) {
    const at = context.currentTime + .005, destination = destinationFor(volume, 1500);
    for (const [offset, startHz, endHz, duration, peak] of notes) note(destination, at + offset, startHz, endHz, duration, peak);
  }
  function forget(id) { decoded.delete(id); decoding.delete(id); }
  async function bufferFor(id, url) {
    if (decoded.has(id)) return decoded.get(id);
    if (decoding.has(id)) return decoding.get(id);
    const request = (async () => {
      const response = await fetch(url);
      if (!response.ok) throw Error(`自定义音效加载失败：${response.status} ${response.statusText}`);
      const buffer = await context.decodeAudioData(await response.arrayBuffer());
      if (decoding.get(id) === request) decoded.set(id, buffer);
      return buffer;
    })();
    decoding.set(id, request);
    try { return await request; } finally { if (decoding.get(id) === request) decoding.delete(id); }
  }
  async function renderPreset(id, preset, volume) {
    if (preset.url) {
      const buffer = await bufferFor(id, preset.url), source = context.createBufferSource();
      source.buffer = buffer; source.connect(destinationFor(volume, buffer.duration * 1000 + 100)); source.onended = () => source.disconnect(); source.start();
    } else renderNotes(preset.notes, volume);
  }
  function claim(id, token) {
    if (!id) return true;
    try {
      const previous = localStorage.getItem(prefix + id);
      if (previous && JSON.parse(previous).done) return false;
      localStorage.setItem(prefix + id, JSON.stringify({token, at:Date.now()}));
    } catch { /* The observer still deduplicates this page when storage is unavailable. */ }
    return true;
  }
  function owns(entry) {
    if (!entry.id) return true;
    try {
      const previous = localStorage.getItem(prefix + entry.id);
      if (previous && JSON.parse(previous).token !== entry.token) return false;
      localStorage.setItem(prefix + entry.id, JSON.stringify({done:true, at:Date.now()}));
    } catch { /* Storage access does not gate audio on this page. */ }
    return true;
  }
  function prune() {
    try {
      const entries = [];
      for (let index = 0; index < localStorage.length; index++) {
        const key = localStorage.key(index);
        if (key.startsWith(prefix)) entries.push({key, at:JSON.parse(localStorage.getItem(key)).at});
      }
      entries.sort((a, b) => b.at - a.at);
      for (const item of entries.slice(256)) localStorage.removeItem(item.key);
    } catch { /* Blocked storage leaves this page's observer usable. */ }
  }
  function withEventLock(entry, action) {
    return entry.id && navigator.locks ? navigator.locks.request(prefix + entry.id, action) : action();
  }
  async function flush() {
    timer = null;
    const batch = [...pending.values()]; pending.clear();
    const entries = [];
    for (const entry of batch) if (await withEventLock(entry, () => owns(entry))) entries.push(entry);
    const settings = getSettings();
    if (!entries.length || settings.muted || !settings.volume || context?.state !== "running") return;
    entries.sort((a, b) => catalog.kinds[a.kind].priority - catalog.kinds[b.kind].priority);
    const selected = entries.find(item => { const {preset} = presetFor(item.kind, settings); return preset?.url || preset?.notes?.length; });
    if (!selected) return;
    for (const entry of entries) lastPlayed.set(entry.kind, performance.now());
    const {id, preset} = presetFor(selected.kind, settings);
    try { await renderPreset(id, preset); } catch (error) { soundError(error); }
    prune();
  }
  function play(kind, id = null) {
    const cue = catalog?.kinds[kind], settings = getSettings();
    if (!cue || settings.muted || !settings.volume || context?.state !== "running") return;
    const {id:presetId, preset} = presetFor(kind, settings);
    if (!preset && presetId !== "off") { soundError(Error(`音效 ${presetId} 已缺失`)); return; }
    if (!preset?.url && !preset?.notes?.length) return;
    if (performance.now() - (lastPlayed.get(kind) ?? -Infinity) < (cue.cooldown || 0)) return;
    const token = `${client}:${++claimSequence}`, entry = {kind, id, token};
    void Promise.resolve(withEventLock(entry, () => {
      if (!claim(id, token)) return;
      pending.set(id || kind, entry);
      // Tabs compete for a durable event before one coalesced batch chooses its cue.
      if (timer === null) timer = setTimeout(() => { void flush(); }, 80);
    })).catch(error => console.warn("提示音未能登记：", error));
  }
  function observe(snapshot, now) {
    if (!snapshot) return;
    configure(snapshot.preferences, snapshot.catalog, snapshot.revision);
    const first = !initialized;
    if (first) { initialized = true; baseline = Date.parse(now) || Date.now(); }
    for (const event of snapshot.events) {
      const previous = observed.get(event.scope); observed.set(event.scope, event.id);
      if (!first && previous !== event.id && (previous !== undefined || Date.parse(event.at) >= baseline)) play(event.kind, event.id);
    }
  }
  async function preview(kind, settings = getSettings()) {
    if (settings.muted || !settings.volume) return false;
    const {id, preset} = presetFor(kind, settings);
    if (!preset) { if (id === "off") return false; throw Error(`音效 ${id} 已缺失`); }
    if (!await unlockAudio()) throw Error("浏览器尚未允许播放声音，请再次点击试听。");
    await renderPreset(id, preset, settings.volume); return true;
  }
  async function previewPreset(id, settings = getSettings()) {
    if (settings.muted || !settings.volume) return false;
    if (!await unlockAudio()) throw Error("浏览器尚未允许播放声音，请再次点击试听。");
    await renderPreset(id, catalog.presets[id], settings.volume); return true;
  }
  function unlock() { void unlockAudio().catch(() => {}); }
  document.addEventListener("pointerdown", unlock, {once:true});
  document.addEventListener("keydown", unlock, {once:true});
  window.CrewSound = Object.freeze({play, preview, previewPreset, forget, configure, observe, getSettings});
})();
