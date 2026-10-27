"use strict";
(() => {
  function create({sound, visibility, document:doc = document}) {
    const waiting = new Map(); let generation = 0, previous = null, destroyed = false;
    function acceptedSend({sessionId, afterSeq, goalId = null}) {
      if (!destroyed && goalId == null) waiting.set(sessionId, {generation:++generation, afterSeq});
    }
    function acceptedStop(sessionId) { waiting.delete(sessionId); }
    function watching(id, reading) {
      return !visibility?.blocked() && reading.view === "chat" && reading.readOnlyId === null && reading.leaderId === id && doc.visibilityState === "visible" && doc.hasFocus();
    }
    function sync(snapshot, {fresh = false, requestedGeneration = generation, reading = {}} = {}) {
      if (destroyed) return;
      const oldSnapshot = previous; previous = snapshot;
      if (oldSnapshot === null) return;
      const decisions = snapshot.decisions.filter(item => !item.held && !oldSnapshot.decisions.some(old => old.id === item.id));
      const errors = snapshot.attention.filter(item => item.kind === "session_error" && !oldSnapshot.attention.some(old => old.id === item.id));
      const completed = new Set(), people = snapshot.leaders.concat(snapshot.members);
      for (const [id, pending] of waiting) {
        // An idle snapshot requested before send was accepted cannot complete that send.
        if (pending.generation > requestedGeneration) continue;
        const item = people.find(person => person.session_id === id);
        if (item === undefined || !item.running || errors.some(error => error.session_id === id) || decisions.some(decision => decision.leader_session_id === id || decision.session_id === id)) waiting.delete(id);
        else if (!item.busy && item.status === "idle" && item.last_seq > pending.afterSeq) { waiting.delete(id); completed.add(id); }
      }
      if (fresh) return;
      if (decisions.length) sound.play("decision");
      const failedChannel = notification => (notification.channels || [notification.channel]).some(channel => ["error", "rejected"].includes(channel.state));
      const failed = errors.length > 0 || failedChannel(snapshot.notification) && !failedChannel(oldSnapshot.notification) || snapshot.notification.error !== null && snapshot.notification.error !== oldSnapshot.notification.error;
      if (failed) sound.play("error");
      if (completed.size) sound.play("complete");
      if (decisions.length || failed || completed.size) return;
      // Streaming keeps its reply id; unread can be cleared by auto-read while unfocused.
      if (snapshot.leaders.some(item => {
        const old = oldSnapshot.leaders.find(person => person.session_id === item.session_id);
        return old !== undefined && item.last_reply_id !== null && item.last_reply_id !== old.last_reply_id && !watching(item.session_id, reading) && !waiting.has(item.session_id);
      })) sound.play("reply");
    }
    return {acceptedSend, acceptedStop, sync, generation:() => generation,
      destroy() { destroyed = true; waiting.clear(); previous = null; }};
  }
  const exported = {create};
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  else window.CrewWaitingNotifications = exported;
})();
