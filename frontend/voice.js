"use strict";
/* 语音原型 · 方向 B「声场舞台」
   只用浏览器原生能力与假数据：不发任何网络请求，只读 localStorage 的 crew.theme。 */
(() => {
  const $ = id => document.getElementById(id);
  const root = document.documentElement, body = document.body;
  const reduce = matchMedia("(prefers-reduced-motion: reduce)");
  const TAU = Math.PI * 2;

  /* ================= 能力分层（固定判断顺序） ================= */
  const forceSim = new URLSearchParams(location.search).has("sim"); // 预览便利：?sim 强制模拟
  const secure = window.isSecureContext;
  const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  const synth = window.speechSynthesis || null;
  const cap = {
    rec: secure && !forceSim && !!Rec,
    mic: secure && !forceSim && !!navigator.mediaDevices?.getUserMedia,
    voice: null, // 中文朗读语音；没有就只做字幕动画
  };
  function pickVoice() {
    const zh = synth.getVoices().filter(v => /^zh/i.test(v.lang));
    const cn = zh.filter(v => /zh[-_]CN/i.test(v.lang));
    cap.voice = cn.find(v => /xiaoxiao|huihui|yunxi|xiaoyi|普通话|mandarin/i.test(v.name)) || cn[0] || zh[0] || null;
    renderStatus();
  }

  /* ================= 状态 ================= */
  const S = {
    state: "idle",   // idle | listening | thinking | speaking
    mode: "hold",    // hold | duplex
    duplex: false,   // 免提是否接通
    holding: false, armed: false, t0: 0, pid: null,
    preset: 0, simTurns: 0, demoBarge: false,
    pending: null,   // 待确认的派活卡
    think: 0, lv: 0, voiceLv: 0,
  };
  const STATE_TXT = { idle: "空闲", listening: "在听", thinking: "在想", speaking: "在说" };
  const PRESETS = [
    "给我汇报一下今天各个工作区的进度",
    "让官网改版的 Leader 把首页首屏改成深色版本",
    "等一下，先别派",
    "让数据看板的 Leader 给周报加上 PDF 导出",
    "确认",
  ];
  const WS = [
    { name: "官网改版", st: "run", stTxt: "进行中", dot: "working", sub: "首屏改版做到一半，Leader 正在调样式", lead: "页面组 Leader" },
    { name: "Crew 本体", st: "wait", stTxt: "等你拍板", dot: "waiting", sub: "2 项：语音入口放哪儿、默认要不要免提", lead: "主线 Leader" },
    { name: "数据看板", st: "done", stTxt: "已交付", dot: "", sub: "周报导出，昨晚 22:40 交付", lead: "看板 Leader" },
  ];

  const ui = {
    status: $("status"), engine: $("st-engine"), stateTxt: $("st-state"),
    who: $("now-who"), text: $("now-text"), vtl: $("vtl"), tlIn: $("vtl-in"),
    dock: $("dock"), dockR: $("dock-r"), hint: $("dock-hint"), mic: $("mic"),
    stop: $("btn-stop"), hang: $("btn-hang"), bars: [...$("meter").children], meter: $("meter"),
  };

  /* ================= 主题：只读 crew.theme，跟随系统 ================= */
  const sysDark = matchMedia("(prefers-color-scheme: dark)");
  function applyTheme() {
    let pick = "system";
    try { pick = localStorage.getItem("crew.theme") || pick; } catch {}
    root.dataset.theme = pick === "system" ? (sysDark.matches ? "dark" : "light") : pick;
    field.colors();
  }

  /* ================= 声场：同心有机环 ================= */
  const field = (() => {
    const cv = $("field"), ctx = cv.getContext("2d"), probe = document.createElement("canvas").getContext("2d");
    let w = 0, h = 0, dpr = 1, accent = [62, 124, 116], dark = false, phase = 0, last = 0;
    // 四态的目标形态：spread 环距，amp 起伏，inward 向内聚拢，outward 向外荡开，think 旋转
    const T = {
      idle:      { spread: 1,    amp: .5,  inward: 0, outward: 0, think: 0 },
      listening: { spread: .8,   amp: .75,  inward: 1, outward: 0, think: 0 },
      thinking:  { spread: .93,  amp: .3,  inward: 0, outward: 0, think: 1 },
      speaking:  { spread: 1.08, amp: .75, inward: 0, outward: 1, think: 0 },
    };
    const P = { ...T.idle, rot: 0 };
    const rgb = c => { probe.fillStyle = "#000"; probe.fillStyle = c; const m = probe.fillStyle.match(/[\da-f]{2}/gi); return m ? m.slice(0, 3).map(x => parseInt(x, 16)) : [62, 124, 116]; };
    const col = a => `rgba(${accent[0]},${accent[1]},${accent[2]},${Math.max(0, a).toFixed(3)})`;

    function colors() { accent = rgb(getComputedStyle(root).getPropertyValue("--accent").trim()); dark = root.dataset.theme === "dark"; draw(); }
    function resize() {
      const r = cv.getBoundingClientRect(); dpr = Math.min(2, devicePixelRatio || 1);
      w = r.width; h = r.height; cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); draw();
    }
    function ring(cx, cy, r, amp, t, i, rot) {
      ctx.beginPath();
      for (let k = 0; k <= 120; k++) {
        const a = k / 120 * TAU;
        const n = Math.sin(3 * a + t * .7 + i * .32) * .5 + Math.sin(5 * a - t * 1.1 + i * .55) * .3 + Math.sin(2 * a + t * .45 + i * .2) * .2;
        const rr = r + n * amp, x = cx + Math.cos(a + rot) * rr, y = cy + Math.sin(a + rot) * rr;
        k ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      }
      ctx.closePath();
    }
    function draw() {
      if (!w) return;
      const lv = S.lv, k = dark ? .85 : 1, cx = w / 2, cy = h / 2, R = Math.min(w * .42, h * .5) * .9;
      const breath = 1 + .035 * Math.sin(phase * 1.15);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);
      // 核心的柔光
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 1.15);
      g.addColorStop(0, col((.13 + lv * .2) * k)); g.addColorStop(1, col(0));
      ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);
      ctx.lineWidth = 1.3; ctx.lineJoin = "round";
      // 主环
      for (let i = 0; i < 5; i++) {
        const r = R * (.3 + i * .175) * P.spread * breath * (1 + lv * .05 * (i + 1));
        const amp = R * .04 * (P.amp * .6 + lv) * (.6 + i * .15);
        const rot = P.rot * (i % 2 ? 1 : -1) * (1 + i * .15), a = (.6 - i * .1) * k;
        ring(cx, cy, r, amp, phase, i, rot);
        if (P.think < .98) { ctx.setLineDash([]); ctx.strokeStyle = col(a * (1 - P.think)); ctx.stroke(); }
        if (P.think > .02) { ctx.setLineDash([R * .2, R * .11]); ctx.strokeStyle = col(a * P.think); ctx.stroke(); }
      }
      ctx.setLineDash([]);
      // 在听：涟漪向内聚拢；在说：涟漪向外荡开
      for (let j = 0; j < 3; j++) {
        const pi = (phase * .32 + j / 3) % 1, po = (phase * .3 + j / 3) % 1;
        if (P.inward > .02) { ctx.strokeStyle = col(Math.sin(pi * Math.PI) * .3 * P.inward * (.5 + lv) * k); ring(cx, cy, R * (1.3 - pi) * P.spread, R * .02, phase, j + 7, 0); ctx.stroke(); }
        if (P.outward > .02) { ctx.strokeStyle = col((1 - po) * .36 * P.outward * (.45 + lv) * k); ring(cx, cy, R * (.3 + po * 1.1), R * .03 * (1 + lv), phase, j + 3, 0); ctx.stroke(); }
      }
      // 核心
      ctx.fillStyle = col((.14 + lv * .28) * k);
      ctx.beginPath(); ctx.arc(cx, cy, R * .14 * (1 + lv * .45) * breath, 0, TAU); ctx.fill();
    }
    function frame(now) {
      const dt = Math.min(.05, (now - (last || now)) / 1000); last = now;
      const t = T[S.state], e = Math.min(1, dt * 2.6);
      for (const key in t) P[key] += (t[key] - P[key]) * e;
      P.rot += dt * (.05 + P.think * .5);
      phase += dt * (.6 + S.lv * 1.6 + P.outward * .35);
      draw();
    }
    function snap() { Object.assign(P, T[S.state]); draw(); } // 减少动效时：直接落到目标形态
    new ResizeObserver(resize).observe(cv);
    return { frame, snap, colors };
  })();

  /* ================= 主循环：电平、进度、声场 ================= */
  const noise = (t, s) => (Math.sin(t / 97 + s) * Math.sin(t / 233 + s * 2) + 1) / 2;
  function levelTarget(now) {
    if (S.state === "speaking") return S.voiceLv;
    if (S.state === "listening") {
      if (mic.an) return micLevel();
      return sim ? .32 + .42 * noise(now, 1) : .05 + .04 * noise(now, 3);
    }
    return S.state === "thinking" ? .1 : .03;
  }
  function loop(now) {
    if (talk) talkTick(now);
    const target = levelTarget(now);
    S.lv += (target - S.lv) * (target > S.lv ? .35 : .12);
    if (!reduce.matches) {
      ui.dock.style.setProperty("--lv", S.lv.toFixed(3));
      ui.bars.forEach((b, i) => { b.style.height = (4 + S.lv * 20 * (.45 + .55 * noise(now, i * 1.7))).toFixed(1) + "px"; });
      field.frame(now);
    }
    requestAnimationFrame(loop);
  }

  /* ================= 麦克风电平（AudioContext + AnalyserNode） ================= */
  const mic = { stream: null, ac: null, an: null, src: null, buf: null, pending: false };
  async function ensureMic() {
    if (!cap.mic || mic.stream || mic.pending) return;
    mic.pending = true;
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      if (!S.holding && !S.duplex) { s.getTracks().forEach(t => t.stop()); return; } // 已松手
      mic.stream = s;
      mic.ac ||= new (window.AudioContext || window.webkitAudioContext)();
      await mic.ac.resume();
      mic.src = mic.ac.createMediaStreamSource(s);
      mic.an = mic.ac.createAnalyser(); mic.an.fftSize = 1024; mic.buf = new Float32Array(mic.an.fftSize);
      mic.src.connect(mic.an);
    } catch (err) {
      cap.mic = false; renderStatus();
      toast(err.name === "NotAllowedError" ? "没拿到麦克风权限，音量改用模拟动画。可以在地址栏左侧的网站设置里允许麦克风。" : "麦克风暂时用不了，音量改用模拟动画。");
    } finally { mic.pending = false; }
  }
  function micLevel() {
    mic.an.getFloatTimeDomainData(mic.buf);
    let s = 0; for (const v of mic.buf) s += v * v;
    return Math.min(1, Math.sqrt(s / mic.buf.length) * 7);
  }
  function releaseMic() {
    mic.stream?.getTracks().forEach(t => t.stop()); mic.src?.disconnect();
    mic.stream = mic.src = mic.an = null;
  }

  /* ================= 页面状态渲染 ================= */
  function setState(s) {
    S.state = s; body.dataset.state = s; ui.dock.dataset.state = s;
    renderStatus(); renderDock();
    if (reduce.matches) { S.lv = s === "listening" ? .35 : s === "speaking" ? .4 : .05; ui.dock.style.setProperty("--lv", S.lv); field.snap(); }
  }
  function renderStatus() {
    ui.engine.textContent = cap.rec ? "浏览器识别" : "模拟演示";
    ui.stateTxt.textContent = STATE_TXT[S.state];
    ui.status.title = `识别：${cap.rec ? "浏览器 SpeechRecognition" : "模拟台词"}；朗读：${cap.voice ? cap.voice.name : "无中文语音，仅字幕"}；电平：${cap.mic ? "麦克风" : "模拟"}`;
  }
  function renderDock() {
    const speaking = S.state === "speaking";
    ui.stop.hidden = !speaking; ui.hang.hidden = !S.duplex;
    ui.meter.hidden = speaking;
    ui.dockR.classList.toggle("both", speaking && S.duplex);
    ui.dock.dataset.mode = S.mode; ui.dock.classList.toggle("live", S.duplex);
    ui.mic.setAttribute("aria-label", S.mode === "hold" ? (speaking ? "按住说话（会打断助手）" : "按住说话") : (S.duplex ? "结束免提" : "开始免提对话"));
    document.querySelectorAll(".vseg button").forEach(b => b.setAttribute("aria-checked", String(b.dataset.mode === S.mode)));
  }
  let toastTimer = 0;
  function toast(msg) {
    const el = $("toast"); el.textContent = msg; el.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.hidden = true; }, 4200);
  }

  /* ================= 当前字幕与时间线 ================= */
  let now = { kind: "greet" }; // greet | hint | user | bot
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  function who(name, note = "") {
    ui.who.replaceChildren(el("span", "who-n", name), el("span", "who-s", note));
  }
  function capLong(text) { body.classList.toggle("cap-long", text.length > 30); }
  // 时间线默认跟随底部；用户自己往上翻才停止跟随
  let follow = true, autoUntil = 0;
  const atBottom = () => ui.vtl.scrollTop + ui.vtl.clientHeight >= ui.vtl.scrollHeight - 24;
  ui.vtl.addEventListener("scroll", () => { if (performance.now() > autoUntil) follow = atBottom(); }, { passive: true });
  new ResizeObserver(() => { if (follow) ui.vtl.scrollTop = ui.vtl.scrollHeight; }).observe(ui.vtl); // 声场收放时保持贴底
  function scrollTl() {
    follow = true; autoUntil = performance.now() + 900;
    requestAnimationFrame(() => ui.vtl.scrollTo({ top: ui.vtl.scrollHeight, behavior: reduce.matches ? "auto" : "smooth" }));
  }
  function addTl(node, before) {
    if (body.classList.contains("vtl-empty")) { body.classList.remove("vtl-empty"); $("vtl-try").remove(); }
    before?.isConnected ? ui.tlIn.insertBefore(node, before) : ui.tlIn.append(node); scrollTl();
  }

  // 把当前这一句收进时间线
  function archive() {
    if (now.kind === "user" && now.text) {
      if (now.cancelled) { const n = el("div", "e-sys"); n.append("已取消：", el("s", "", now.text)); addTl(n); }
      else addTl(el("div", now.tap ? "e-user tap" : "e-user", now.text));
    } else if (now.kind === "bot") {
      const n = el("div", "e-bot"), cut = now.cutAt;
      if (cut == null) n.textContent = now.text;
      else n.append(now.text.slice(0, cut), el("span", "cut-mark", "已打断"), el("span", "cut", now.text.slice(cut)));
      addTl(n, now.card); // 文字排在它的卡片之前
    }
    now = { kind: "none" };
  }
  function showHint(text) {
    archive(); now = { kind: "hint" };
    who("提示"); ui.text.className = "now-text hint"; ui.text.textContent = text; capLong(text); capReset();
  }

  /* ---- 用户的话 ---- */
  function userBegin(tap = false) {
    clearTimeout(S.think);
    const barged = !!talk;
    if (talk) stopTalk(true); // 用户开口 = 打断
    archive(); now = { kind: "user", text: "", tap };
    who("你", tap ? "点按" : barged ? "插话" : "在听"); ui.text.className = "now-text"; ui.text.replaceChildren(el("span", "caret")); capLong(""); capReset();
  }
  function userUpdate(fin, interim) {
    if (now.kind !== "user" || now.final) userBegin();
    now.text = fin + interim;
    ui.text.replaceChildren(fin, el("span", "im", interim), el("span", "caret"));
    capLong(now.text); ui.text.scrollTop = ui.text.scrollHeight;
  }
  function userFinal(text) {
    if (now.kind !== "user" || now.final) userBegin();
    now.text = text; now.final = true;
    ui.text.className = "now-text"; ui.text.textContent = text; capLong(text);
    who("你", now.tap ? "点按" : "");
  }
  function userCancel() {
    if (now.kind === "user" && now.text) { now.cancelled = true; ui.text.className = "now-text cancel"; who("你", "已取消"); }
    else showHint("按住说话，松开发送；上滑可以取消。");
  }
  function handleUser(text) {
    setState("thinking");
    clearTimeout(S.think);
    S.think = setTimeout(() => say(replyFor(text)), 600 + Math.random() * 600);
  }

  /* ================= 助手发言：朗读 + 卡拉 OK 高亮 ================= */
  const PUNCT = /[，。、；：！？,.;:!?—…」』）)\s]/;
  // 中文逐字、英文/数字成词，标点并入前一个
  function tokenize(text) {
    const out = [];
    for (let i = 0; i < text.length; i++) {
      const c = text[i], prev = out[out.length - 1];
      if (prev && (PUNCT.test(c) || (/[A-Za-z0-9]/.test(c) && /[A-Za-z0-9]$/.test(text.slice(prev.s, prev.e))))) prev.e = i + 1;
      else out.push({ s: i, e: i + 1 });
    }
    return out;
  }
  // 每个字的估计时长：约 5 字/秒，标点处停顿
  function timeline(text) {
    let t = 0; const at = [];
    for (const c of text) { t += /[。！？]/.test(c) ? 420 : /[，、；：,]/.test(c) ? 270 : /\s/.test(c) ? 40 : /[A-Za-z0-9]/.test(c) ? 90 : 195; at.push(t); }
    return at;
  }

  let talk = null;
  function say(reply) {
    archive();
    const text = reply.text, toks = tokenize(text);
    now = { kind: "bot", text, cutAt: null, card: null };
    who("助手", "在说"); ui.text.className = "now-text bot"; capLong(text);
    const spans = toks.map(t => el("span", "t", text.slice(t.s, t.e)));
    ui.text.replaceChildren(...spans); capReset();
    if (reply.card) { now.card = reply.card(); addTl(now.card); }
    // 按句切分（避免长句被浏览器截断），每句记下偏移
    const chunks = []; let off = 0;
    for (const part of text.match(/[^。！？]+[。！？]?/g) || [text]) { chunks.push({ s: off, e: off + part.length, text: part, at: timeline(part) }); off += part.length; }
    talk = { reply, text, toks, spans, chunks, k: -1, idx: -1, t0: 0, started: false, boundary: false, moved: 0, timer: 0, cur: null, tts: !!(synth && cap.voice) };
    if (talk.tts) synth.cancel();
    ui.dock.style.setProperty("--p", 0);
    setState("speaking");
    advance(0);
    nextChunk();
  }
  function nextChunk() {
    const t = talk; t.k++;
    if (t.k >= t.chunks.length) return finishTalk();
    const c = t.chunks[t.k], my = t.cur = {};
    const live = () => talk === t && t.cur === my; // 每句一个令牌，旧回调一律作废
    t.started = false; t.t0 = performance.now();
    const done = () => { if (!live()) return; advance(c.e); nextChunk(); };
    // 朗读失败或迟迟不开口：这一句改走估时
    const fallback = () => { if (!live()) return; t.tts = false; t.cur = null; clearTimeout(t.timer); synth.cancel(); t.k--; nextChunk(); };
    if (t.tts) {
      const u = new SpeechSynthesisUtterance(c.text);
      u.lang = "zh-CN"; u.voice = cap.voice; u.rate = 1.05;
      u.onstart = () => { if (!live()) return; t.started = true; t.t0 = performance.now(); clearTimeout(t.timer); };
      u.onboundary = e => { if (live() && (!e.name || e.name === "word")) { t.boundary = true; advance(c.s + e.charIndex + (e.charLength || 1)); } };
      u.onend = done; u.onerror = fallback;
      synth.speak(u);
      t.timer = setTimeout(() => { if (!t.started) fallback(); }, 1500);
    } else {
      t.started = true;
      t.timer = setTimeout(done, c.at[c.at.length - 1] + 60);
    }
  }
  function talkTick(nowT) {
    const t = talk, c = t.chunks[t.k];
    if (c && t.started && !t.boundary) { // 没有 onboundary 就按时长估算
      const ms = nowT - t.t0; let n = 0;
      while (n < c.at.length - 1 && c.at[n] <= ms) n++;
      advance(c.s + n);
    }
    // 合成一个说话电平：读到标点时落下去
    const quiet = PUNCT.test(t.text[t.idx - 1] || "") && nowT - t.moved > 140;
    S.voiceLv = quiet ? .06 : .4 + .4 * noise(nowT, 5);
    ui.dock.style.setProperty("--p", (t.idx / t.text.length).toFixed(3));
  }
  function advance(idx) {
    const t = talk;
    if (!t || idx <= t.idx) return;
    t.idx = Math.min(idx, t.text.length); t.moved = performance.now();
    // 提前揭示到当前分句结尾
    let rv = t.idx + 12; while (rv < t.text.length && !/[，。；：！？]/.test(t.text[rv])) rv++;
    t.toks.forEach((tk, i) => {
      const cls = tk.e <= t.idx ? "t sp" : tk.s < t.idx || (tk.s === t.idx && i === 0) ? "t cur" : tk.s <= rv ? "t rv" : "t";
      if (t.spans[i].className !== cls) t.spans[i].className = cls;
      if (cls === "t cur") keepVisible(t.spans[i]);
    });
    syncCard(t.idx);
  }
  // 当前行保持在字幕窗口的第二行，下一行留给淡显的预告
  function keepVisible(span) {
    const box = ui.text, lh = parseFloat(getComputedStyle(box).lineHeight), top = span.offsetTop - box.offsetTop;
    if (top - box.scrollTop > lh * 1.5) box.scrollTop = top - lh;
    box.classList.toggle("rolled", box.scrollTop > 2);
  }
  function capReset() { ui.text.classList.remove("rolled"); ui.text.style.scrollBehavior = "auto"; ui.text.scrollTop = 0; ui.text.style.scrollBehavior = ""; }
  // 汇报卡与语音同步：读到哪一段，哪一行点亮
  function syncCard(idx) {
    const t = talk, card = now.card;
    if (!card || !t.reply.segs) return;
    card.classList.add("syncing");
    let off = 0;
    for (const seg of t.reply.segs) {
      const end = off + seg.t.length, row = seg.row == null ? null : card.querySelector(`[data-row="${seg.row}"]`);
      if (row) { row.classList.toggle("lit", idx > off && idx <= end && idx < t.text.length); row.classList.toggle("read", idx > off); }
      off = end;
    }
  }
  function endSync() { now.card?.classList.remove("syncing"); now.card?.querySelectorAll(".lit").forEach(n => n.classList.remove("lit")); }
  function finishTalk() {
    const t = talk;
    advance(t.text.length); t.spans.forEach(s => { s.className = "t sp"; });
    talk = null; endSync(); who("助手");
    t.reply.after?.();
    afterTurn();
  }
  // 停止助手说话；interrupted=true 时把没念完的部分变灰并标注
  function stopTalk(interrupted) {
    const t = talk; if (!t) return;
    talk = null; clearTimeout(t.timer);
    if (synth) synth.cancel();
    endSync();
    if (!interrupted || t.idx >= t.text.length - 1) { t.spans.forEach(s => { s.className = "t sp"; }); who("助手"); return; }
    let cutTok = t.toks.findIndex(tk => tk.e > t.idx);
    t.spans.forEach((s, i) => { s.className = i < cutTok ? "t sp" : "t cut"; });
    t.spans[cutTok].before(el("span", "cut-mark", "已打断"));
    now.cutAt = t.toks[cutTok].s;
    who("助手", "已打断");
  }
  function afterTurn() {
    if (S.duplex) { setState("listening"); simQueue(1500); }
    else setState("idle");
  }

  /* ================= 规则：识别结果 → 助手回复 ================= */
  function replyFor(raw) {
    const text = raw.replace(/\s+/g, " ").trim();
    if (/取消|先别|等一下|算了|不派|别派/.test(text)) {
      if (!S.pending) return { text: "好，现在也没有要派出的任务。" };
      S.pending.set("off", "已取消 · 没有派出"); S.pending = null;
      return { text: "好，先不派，这张卡片我收起来了。" };
    }
    if (/改一下|改改|换个说法/.test(text) && S.pending) { S.pending.set("edit"); return { text: "好，想怎么改？直接说新的要求，我再复述给你。" }; }
    const isDispatch = /(让|叫|请).+(把|给|做|去|帮)|派|安排/.test(text) && !/^(确认|确定)/.test(text);
    if (!isDispatch && /确认|确定|派吧|就这么办/.test(text)) {
      if (!S.pending) return { text: "现在没有等你确认的任务。" };
      const ws = S.pending.ws; S.pending.set("sent", "已派出 · 假数据，未真正发送"); S.pending = null;
      return { text: `派好了，${ws}的 Leader 会接手。这是原型，没有真的发出去。`, long: false };
    }
    if (/汇报|进度|近况|怎么样了/.test(text)) return reportReply();
    if (isDispatch) return dispatchReply(text);
    const short = text.length > 16 ? text.slice(0, 16) + "…" : text;
    return { text: `我听到的是「${short}」。这个原型只认得汇报和派活，可以试试说「汇报一下进度」。` };
  }

  function reportReply() {
    const segs = [
      { t: "今天三个工作区的情况：" },
      { t: "官网改版还在进行中，首屏改到一半；", row: 0 },
      { t: "Crew 本体有两项等你拍板，都和语音入口有关；", row: 1 },
      { t: "数据看板昨晚已经交付。", row: 2 },
      { t: "合起来，待你拍板的一共两项，要我挨个念一下吗？", row: "sum" },
    ];
    return { text: segs.map(s => s.t).join(""), segs, long: true, card: reportCard };
  }
  const ICON = {
    report: '<svg class="i xs" viewBox="0 0 16 16"><path d="M3 4h10M3 8h10M3 12h6"/></svg>',
    send: '<svg class="i xs" viewBox="0 0 16 16"><path d="M2.8 8h9.4M8.6 4.4 12.2 8l-3.6 3.6"/></svg>',
    check: '<svg class="i sm" viewBox="0 0 16 16"><path d="M3.5 8.4 6.6 11.4 12.5 4.8"/></svg>',
  };
  function reportCard() {
    const c = el("article", "vcard vcard-report");
    c.innerHTML = `<div class="vc-head"><b>${ICON.report}今日汇报</b><span>3 个工作区</span></div>`;
    WS.forEach((w, i) => {
      const r = el("div", "vr"); r.dataset.row = i;
      r.append(el("i", "dot " + w.dot), el("span", "vr-name", w.name), el("span", "vpill " + w.st, w.stTxt), el("span", "vr-sub", w.sub));
      c.append(r);
    });
    const sum = el("div", "vc-sum"); sum.dataset.row = "sum";
    sum.innerHTML = `<span>待你拍板<span class="n">2</span>项</span><strong>都在 Crew 本体</strong>`;
    c.append(sum);
    return c;
  }

  function dispatchReply(text) {
    const ws = /官网|首页|首屏/.test(text) ? "官网改版" : /看板|数据|周报/.test(text) ? "数据看板" : "Crew 本体";
    const lead = WS.find(w => w.name === ws).lead;
    // 抽出"把/给/去…"之后的一句话任务；"把"字句卡片上去掉"把"
    const m = text.match(/(把|给|去|帮|做)(.+)$/);
    const task = (m ? (m[1] === "把" ? m[2] : m[1] + m[2]) : text).replace(/[。！？!?,，\s]+$/, "");
    const said = m && m[1] === "把" ? "把" + task : task;
    const risky = /改|加|删|换|上线|发布|修/.test(task);
    if (S.pending) S.pending.set("off", "已被新的要求替换");
    const card = dispatchCard({ ws, lead, task, risky });
    S.pending = card;
    return {
      text: `好，我复述一下：让${ws}的 Leader ${said}。` + (risky ? "这会改代码，需要你确认。说「确认」就派出，说「取消」就先不派。" : "这只是查看，不改东西。说「确认」就派出。"),
      long: true, card: () => card.el,
    };
  }
  function dispatchCard(d) {
    const c = el("article", "vcard vcard-dispatch");
    c.innerHTML = `<div class="vc-head"><b>${ICON.send}派活确认</b><span>等你确认</span></div>`;
    c.append(el("div", "vd-title", d.task));
    const dl = el("dl", "vd-meta"), lead = el("dd");
    const av = el("span", "av", d.ws[0]);
    lead.append(av, d.lead);
    const risk = el("dd"); risk.append(el("span", d.risky ? "vrisk" : "vrisk low", d.risky ? "会改代码 · 需要确认" : "只读 · 低风险"));
    dl.append(el("dt", "", "目标工作区"), el("dd", "", d.ws), el("dt", "", "负责 Leader"), lead, el("dt", "", "风险"), risk);
    const act = el("div", "vd-act"), hint = el("div", "vd-hint", "也可以直接说「确认」或「取消」");
    const btn = (label, cls, intent) => { const b = el("button", "btn " + cls, label); b.type = "button"; b.onclick = () => tapIntent(label, intent); return b; };
    act.append(btn("确认派出", "primary", "确认"), btn("改一下", "ghost", "改一下"), btn("取消", "quiet", "取消"));
    c.append(dl, act, hint);
    const card = {
      el: c, ws: d.ws,
      set(kind, label) {
        const head = c.querySelector(".vc-head span");
        c.classList.remove("is-edit");
        if (kind === "edit") { c.classList.add("is-edit"); head.textContent = "等你补充"; hint.textContent = "直接说新的要求，我会重新复述"; return; }
        c.classList.add(kind === "sent" ? "is-sent" : "is-off");
        head.textContent = kind === "sent" ? "已派出" : "未派出";
        const done = el("div", "vd-done"); done.innerHTML = kind === "sent" ? ICON.check : "";
        done.append(label); act.replaceWith(done); hint.remove();
      },
    };
    return card;
  }
  // 点卡片按钮 = 说了一句对应的话
  function tapIntent(label, intent) {
    stopSim(); userBegin(true); userFinal(label); handleUser(intent);
  }

  /* ================= 识别：浏览器 SpeechRecognition ================= */
  let rec = null;
  function recStart(continuous) {
    const r = new Rec(); rec = r;
    Object.assign(r, { lang: "zh-CN", interimResults: true, continuous, maxAlternatives: 1 });
    r.finals = ""; r.interim = "";
    r.onresult = e => {
      if (rec !== r) return;
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const res = e.results[i], t = res[0].transcript;
        if (!res.isFinal) interim += t;
        else if (S.duplex) duplexFinal(t);
        else r.finals += t;
      }
      r.interim = interim;
      if (S.duplex) duplexInterim(interim);
      else if (S.holding || r.releasing) userUpdate(r.finals, interim);
    };
    r.onerror = e => { if (rec === r && !/no-speech|aborted/.test(e.error)) recFail(e.error); };
    r.onend = () => {
      if (rec !== r) return;
      if (S.duplex) { try { r.start(); } catch {} return; } // 免提：静音后自动续上
      rec = null;
      if (r.releasing) finishHold(r.finals + r.interim);
    };
    try { r.start(); } catch { recFail("start"); }
  }
  function recStop(abort) {
    const r = rec; if (!r) return;
    if (abort) { rec = null; try { r.abort(); } catch {} return; }
    r.releasing = true; try { r.stop(); } catch {}
  }
  // 回声保护：识别到的内容如果正是助手在念的话，不算打断
  const bare = s => s.replace(/[\s\p{P}]/gu, ""); // 两边同样去掉空白和标点再比
  const isEcho = t => { const c = bare(t); return c.length < 2 || (talk && bare(talk.text).includes(c)); };
  function duplexInterim(t) {
    if (!t.trim() || (S.state === "speaking" && isEcho(t))) return;
    if (S.state !== "listening") setState("listening");
    userUpdate("", t);
  }
  function duplexFinal(t) {
    if (!t.trim() || (S.state === "speaking" && isEcho(t))) return;
    userFinal(t.trim()); handleUser(t);
  }
  function recFail(code) {
    recStop(true); cap.rec = false; renderStatus();
    toast({ "not-allowed": "浏览器没允许语音识别，已切到模拟演示。", "service-not-allowed": "浏览器没允许语音识别，已切到模拟演示。", network: "浏览器的语音识别服务连不上，已切到模拟演示。", "audio-capture": "没找到可用的麦克风，已切到模拟演示。" }[code] || `语音识别出错（${code}），已切到模拟演示。`);
    if (S.holding) simStart(nextPreset(), false);
    else if (S.duplex) simQueue(600);
  }

  /* ================= 模拟识别：预设台词逐字出现 ================= */
  let sim = null;
  const nextPreset = () => PRESETS[S.preset++ % PRESETS.length];
  function simStart(line, auto) {
    stopSim();
    const s = sim = { line, n: 0, auto, fast: false, timer: 0 };
    userBegin();
    setState("listening");
    const step = () => {
      if (sim !== s) return;
      s.n++;
      if (s.n < line.length) { userUpdate("", line.slice(0, s.n)); s.timer = setTimeout(step, s.fast ? 26 : 120 + Math.random() * 90); return; }
      sim = null;
      if (auto || s.fast) { userFinal(line); handleUser(line); }
      else userUpdate(line, ""); // 按住模式：说完了，等松手
    };
    s.timer = setTimeout(step, 220);
  }
  function stopSim() { if (sim) { clearTimeout(sim.timer); sim = null; } }
  // 免提模拟：助手说完后隔一会儿，自动"说"下一句
  let simQ = 0;
  function simQueue(delay) {
    clearTimeout(simQ);
    if (!S.duplex || cap.rec) return;
    if (S.simTurns >= PRESETS.length) { toast("模拟台词演示完了。点主按钮或「挂断」结束免提。"); return; }
    simQ = setTimeout(() => { if (S.duplex && S.state === "listening" && !sim) { S.simTurns++; simStart(nextPreset(), true); } }, delay);
  }
  // 模拟免提：助手说到一半，演示一次"用户开口打断"
  setInterval(() => {
    const t = talk;
    if (!t || !S.duplex || cap.rec || S.demoBarge || sim || !t.reply.long || t.idx / t.text.length < .45) return;
    if (/^确认/.test(PRESETS[S.preset % PRESETS.length])) return;
    S.demoBarge = true; S.simTurns++;
    simStart(nextPreset(), true);
  }, 120);

  /* ================= 按住说话 ================= */
  function holdStart() {
    S.holding = true; S.armed = false; S.t0 = performance.now();
    ui.mic.classList.add("pressed"); ui.dock.classList.add("holding"); ui.hint.textContent = "松开发送 · 上滑取消";
    stopSim(); userBegin(); setState("listening");
    ensureMic();
    if (cap.rec) recStart(true); else simStart(nextPreset(), false);
  }
  function arm(on) {
    if (S.armed === on) return;
    S.armed = on; ui.dock.classList.toggle("armed", on); ui.hint.textContent = on ? "松开取消" : "松开发送 · 上滑取消";
  }
  function holdEnd(cancel) {
    if (!S.holding) return;
    S.holding = false;
    ui.mic.classList.remove("pressed"); ui.dock.classList.remove("holding", "armed");
    const tap = performance.now() - S.t0 < 300;
    if (cancel || tap) {
      if (!cap.rec && (sim || tap)) S.preset--; // 没说完的台词留到下次
      stopSim(); recStop(true); releaseMic();
      if (tap && !cancel) showHint("按住说话，松开发送；上滑可以取消。"); else userCancel();
      setState("idle"); return;
    }
    releaseMic();
    if (cap.rec && rec) { setState("thinking"); recStop(false); return; } // 等 onend 拿到最终结果
    if (cap.rec) return finishHold(now.text || ""); // 识别已自己结束（长时间静音）
    if (sim) { sim.fast = true; return; }  // 模拟：快进把这句说完
    finishHold(now.text || "");
  }
  function finishHold(text) {
    text = text.trim();
    if (!text) { showHint("没听清，再按住说一次？"); setState("idle"); return; }
    userFinal(text); handleUser(text);
  }

  /* ================= 免提（全双工） ================= */
  function startDuplex() {
    S.duplex = true; S.simTurns = 0; S.demoBarge = false;
    if (talk) stopTalk(true);
    clearTimeout(S.think);
    setState("listening");
    ensureMic();
    if (cap.rec) recStart(true); else simQueue(900);
  }
  function hangUp() {
    S.duplex = false;
    clearTimeout(simQ); clearTimeout(S.think); stopSim(); recStop(true); releaseMic();
    if (talk) stopTalk(true);
    if (now.kind === "user" && !now.final) { now.text = ""; showHint("免提已挂断。"); } // 说到一半的话直接丢掉
    setState("idle");
  }
  function interrupt() {
    if (!talk) return;
    stopTalk(true);
    if (S.duplex) { setState("listening"); simQueue(1800); } else setState("idle");
  }

  /* ================= 事件 ================= */
  ui.mic.addEventListener("pointerdown", e => {
    if (e.button > 0) return;
    e.preventDefault();
    if (S.mode === "duplex") { S.duplex ? hangUp() : startDuplex(); return; }
    try { ui.mic.setPointerCapture(e.pointerId); } catch {}
    S.pid = e.pointerId; S.y0 = e.clientY;
    holdStart();
  });
  ui.mic.addEventListener("pointermove", e => { if (e.pointerId === S.pid && S.holding) arm(S.y0 - e.clientY > 64); });
  ui.mic.addEventListener("pointerup", e => { if (e.pointerId === S.pid) { S.pid = null; holdEnd(S.armed); } });
  ui.mic.addEventListener("pointercancel", e => { if (e.pointerId === S.pid) { S.pid = null; holdEnd(true); } });
  ui.mic.addEventListener("contextmenu", e => e.preventDefault());
  // 键盘：空格/回车按住说话，免提模式下切换
  ui.mic.addEventListener("keydown", e => {
    if ((e.key !== " " && e.key !== "Enter") || e.repeat) return;
    e.preventDefault();
    if (S.mode === "duplex") S.duplex ? hangUp() : startDuplex(); else if (!S.holding) holdStart();
  });
  ui.mic.addEventListener("keyup", e => { if ((e.key === " " || e.key === "Enter") && S.holding) holdEnd(false); });

  document.querySelectorAll(".vseg button").forEach(b => b.addEventListener("click", () => {
    if (b.dataset.mode === S.mode) return;
    if (S.duplex) hangUp();
    S.mode = b.dataset.mode; renderDock();
  }));
  ui.stop.addEventListener("click", interrupt);
  ui.hang.addEventListener("click", hangUp);
  $("notice-x").addEventListener("click", () => { $("notice").hidden = true; });

  /* ================= 启动 ================= */
  $("notice").hidden = secure;
  if (synth) { pickVoice(); synth.addEventListener("voiceschanged", pickVoice); }
  applyTheme();
  sysDark.addEventListener("change", applyTheme);
  window.addEventListener("storage", e => { if (e.key === "crew.theme") applyTheme(); });
  reduce.addEventListener("change", () => setState(S.state));
  setState("idle");
  requestAnimationFrame(loop);
})();
// 返回：从工作区助手进来（?project=X）就回到那个助手，否则回系统管理员。
(() => {
  const back = document.querySelector(".vh-back"), project = new URLSearchParams(location.search).get("project");
  if (!back) return;
  back.href = project ? `/assistant.html?project=${encodeURIComponent(project)}` : "/assistant.html";
  back.setAttribute("aria-label", project ? "返回助手" : "返回系统管理员");
  if (!project) back.querySelector("span").textContent = "系统管理员";
})();
