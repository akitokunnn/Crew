"use strict";
(() => {
  const decisions = plan => plan.entries.filter(item => item.kind === "decision");
  const pending = plan => decisions(plan).filter(item => !item.answer);

  function approval(room, plan) {
    return {room:room.id, version:plan.version, budget_tokens:plan.budget_tokens,
      recommendations:Object.fromEntries(pending(plan).map(item => [item.entry_id, item.recommendation]))};
  }

  function document({body, entries}) {
    const lines = body.split(/\r\n?|\n/);
    const byLine = new Map(entries.map(item => [item.line, item]));
    const blocks = [];
    let section = "", heading = null;
    const counts = new Map();
    const html = CrewReportMarkdown.render(body, tokens => {
      tokens.forEach((token, index) => {
        if (!token.map || token.hidden) return;
        const [start, end] = token.map;
        const entry = byLine.get(start + 1);
        const isHeading = token.type === "heading_open" && token.level === 0;
        if (isHeading) section = tokens[index + 1].content;
        const isEntry = entry && token.type === "list_item_open";
        const ordinary = token.level === 0 && !["bullet_list_open", "ordered_list_open"].includes(token.type);
        if (!ordinary && !isEntry && token.type !== "list_item_open") return;
        const group = JSON.stringify([section, token.type]);
        const ordinal = (counts.get(group) ?? 0) + 1;
        counts.set(group, ordinal);
        const key = isEntry ? `entry:${entry.entry_id}` : `${group}:${ordinal}`;
        if (isHeading) heading = key;
        const text = lines.slice(start, end).join("\n").trim();
        blocks.push({key, group, heading, line:start + 1, end, text,
          stable:Boolean(isEntry || isHeading), label:isEntry ? `${entry.entry_id} · ${entry.title}` :
            isHeading ? section : (text.split("\n")[0].slice(0, 48) || section)});
        token.attrSet("data-wr-block", key);
      });
    });
    return {html, blocks};
  }

  function compare(previous, next) {
    const remaining = new Map(previous.blocks.map(block => [block.key, block]));
    const positions = new Map();
    const same = new Set();
    for (const block of next.blocks) {
      const match = block.stable ? remaining.get(block.key) :
        [...remaining.values()].find(old => !old.stable && old.group === block.group && old.text === block.text);
      if (!match || match.text !== block.text) continue;
      remaining.delete(match.key);
      positions.set(match.key, block.key);
      same.add(block.key);
    }
    const result = [];
    for (const block of next.blocks) {
      if (same.has(block.key)) continue;
      const old = remaining.get(block.key) ?? (!block.stable && [...remaining.values()].find(item => !item.stable && item.group === block.group));
      if (old) {
        remaining.delete(old.key);
        positions.set(old.key, block.key);
      }
      result.push({...block, type:old ? "改" : "新"});
    }
    const keys = new Set(next.blocks.map(block => block.key));
    for (const old of remaining.values()) {
      result.push({...old, key:keys.has(old.heading) ? old.heading : null, type:"删"});
    }
    return {changes:result, positions};
  }

  window.CrewWarRoomPlan = {decisions, pending, approval, document, compare};
})();
