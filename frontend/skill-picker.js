// "/" skill picker for chat composers. The list comes from the session's own carrier
// (Claude Code slash commands or Codex skills/list); picking inserts that carrier's native
// invocation (/name or $name) so the AI resolves the skill itself.
(() => {
  const RUNTIME = {claude: "Claude Code", codex: "Codex"};
  const escape = value => String(value).replace(/[&<>"']/g, char => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[char]));
  let serial = 0;

  function rank(skill, query) {
    if (!query) return 0;
    const name = skill.name.toLowerCase(), short = name.slice(name.indexOf(":") + 1);
    if (name.startsWith(query) || short.startsWith(query)) return 0;
    if (name.includes(query)) return 1;
    return skill.description.toLowerCase().includes(query) ? 2 : -1;
  }

  function attach({input, host, load}) {
    const id = `skill-picker-${++serial}`;
    const panel = document.createElement("div");
    panel.className = "skill-picker"; panel.id = id; panel.hidden = true;
    panel.setAttribute("role", "listbox"); panel.setAttribute("aria-label", "可调用的技能");
    host.append(panel);
    input.setAttribute("aria-controls", id); input.setAttribute("aria-autocomplete", "list"); input.setAttribute("aria-expanded", "false");
    let data = null, error = null, pending = null, range = null, matches = [], index = 0;

    // "/" at the start or after whitespace, with the caret right after the typed query.
    function token() {
      const caret = input.selectionStart;
      if (caret !== input.selectionEnd) return null;
      const match = /(^|\s)\/([^\s]*)$/.exec(input.value.slice(0, caret));
      return match ? {start: caret - match[2].length - 1, end: caret, query: match[2].toLowerCase()} : null;
    }
    function fetchSkills(refresh) {
      error = null;
      pending = Promise.resolve(load(refresh)).then(result => { data = result; }, failure => { error = failure?.message || String(failure); })
        .finally(() => { pending = null; if (range) render(); });
      render();
    }
    function show(html) {
      panel.innerHTML = html; panel.hidden = false; input.setAttribute("aria-expanded", "true");
    }
    function close() {
      range = null; panel.hidden = true; panel.innerHTML = "";
      input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant");
    }
    function header() {
      const carrier = data ? RUNTIME[data.runtime] || data.runtime : "";
      return `<div class="skill-picker-head"><span>技能${carrier ? ` · ${escape(carrier)}` : ""}</span><button type="button" class="skill-picker-refresh" data-skill-refresh${pending ? " disabled" : ""}>刷新</button></div>`;
    }
    function render() {
      if (!range) return;
      if (error) return show(`${header()}<div class="skill-picker-note" role="alert">读取技能失败：${escape(error)}</div>`);
      if (!data) return show(`${header()}<div class="skill-picker-note">正在读取技能…</div>`);
      matches = data.skills.map(skill => [rank(skill, range.query), skill]).filter(([score]) => score >= 0)
        .sort((a, b) => a[0] - b[0]).map(([, skill]) => skill);
      if (!matches.length) {
        if (range.query) { panel.hidden = true; input.setAttribute("aria-expanded", "false"); return; }
        return show(`${header()}<div class="skill-picker-note">这个会话的执行工具没有可调用的技能</div>`);
      }
      index = Math.min(index, matches.length - 1);
      show(header() + `<div class="skill-picker-list">${matches.map((skill, at) => `<div class="skill-picker-item" role="option" id="${id}-${at}" data-skill-index="${at}" aria-selected="${at === index}" title="${escape(skill.hint ? `${skill.token} ${skill.hint}\n${skill.description}` : skill.description)}"><span class="skill-picker-token">${escape(skill.token)}</span><span class="skill-picker-desc">${escape(skill.description)}</span></div>`).join("")}</div>`);
      input.setAttribute("aria-activedescendant", `${id}-${index}`);
      panel.querySelector(`[aria-selected="true"]`)?.scrollIntoView({block: "nearest"});
    }
    function pick(at) {
      const skill = matches[at];
      if (!skill || !range) return;
      const text = skill.token + " ";
      input.value = input.value.slice(0, range.start) + text + input.value.slice(range.end);
      const caret = range.start + text.length;
      close();
      input.focus(); input.setSelectionRange(caret, caret);
      input.dispatchEvent(new Event("input", {bubbles: true}));
    }
    function update() {
      const next = token();
      if (!next) return close();
      if (!range || next.query !== range.query) index = 0;
      range = next;
      if (!data && !pending && !error) fetchSkills(false); else render();
    }
    // Returns true when the picker consumed the key.
    function keydown(event) {
      if (!range || panel.hidden || event.isComposing) return false;
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); return true; }
      if (!data || error || !matches.length) return false;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        index = (index + (event.key === "ArrowDown" ? 1 : matches.length - 1)) % matches.length;
        render(); return true;
      }
      if ((event.key === "Enter" && !event.shiftKey) || event.key === "Tab") { event.preventDefault(); pick(index); return true; }
      return false;
    }
    panel.addEventListener("mousedown", event => event.preventDefault());
    panel.addEventListener("click", event => {
      if (event.target.closest("[data-skill-refresh]")) { data = null; fetchSkills(true); return; }
      const item = event.target.closest("[data-skill-index]");
      if (item) pick(Number(item.dataset.skillIndex));
    });
    input.addEventListener("blur", close);
    input.addEventListener("click", update);
    return {update, keydown, close};
  }
  window.CrewSkillPicker = {attach};
})();
