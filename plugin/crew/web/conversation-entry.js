"use strict";
// Creating a discussion and delivering its first input are separate receipts.
// Preserve both identities across an uncertain send so retry never makes a
// second task or silently treats a transport response as native acceptance.
((root, factory) => {
  const value = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = value;
  else root.CrewConversationEntry = value;
})(typeof window === "undefined" ? globalThis : window, () => {
  function create({ client, recall, remember, requestId }) {
    async function deliver(key, attempt) {
      const task = attempt.task,
        conversation = attempt.conversation || {
          project: task.primary_project,
          goal_id: task.id,
          session_id: task.leader_session_id,
          text: attempt.payload.text,
        };
      if (
        !conversation.project ||
        !conversation.goal_id ||
        !conversation.session_id
      )
        throw Error("讨论已建立，但会话归属字段不完整，请打开任务核对。");
      const receipt = await client.request(
        `/api/chat/send?project=${encodeURIComponent(conversation.project)}`,
        {
          goal_id: conversation.goal_id,
          session_id: conversation.session_id,
          text: conversation.text,
          message_id: attempt.message_id,
          intent: "dialogue",
        },
      );
      if (!(receipt.accepted === true || receipt.status === "accepted")) {
        const error = new Error(
          ["unknown", "submitted"].includes(receipt.status) || !receipt.status
            ? "任务已建立，原话是否送达尚未确认；重试会保留同一消息身份。"
            : receipt.error ||
              receipt.reason ||
              "任务已建立，原话尚未被载体接收。",
        );
        error.receipt = receipt;
        error.task = task;
        throw error;
      }
      remember(key, null);
      return { task, receipt };
    }
    return {
      async start({ project, text, key = "conversation.create" }) {
        const payload = { project, text },
          prior = recall(key, null);
        const attempt =
          prior && JSON.stringify(prior.payload) === JSON.stringify(payload)
            ? prior
            : { payload, request_id: requestId(), message_id: requestId() };
        remember(key, attempt);
        if (!attempt.task) {
          const value = await client.request("/api/tasks/create", {
            ...payload,
            request_id: attempt.request_id,
          });
          attempt.task = value.task;
          remember(key, attempt);
        }
        return deliver(key, attempt);
      },
      async fromItem(item, key = `conversation.item.${item.id}`, options = {}) {
        const payload = {
            id: item.id,
            version: item.version,
            project: options.project ?? item.project,
            ...(options.task_id ? { task_id: options.task_id } : {}),
          },
          prior = recall(key, null);
        const sameSelection =
          prior?.task &&
          String(prior.payload.id) === String(payload.id) &&
          prior.payload.project === payload.project &&
          prior.payload.task_id === payload.task_id;
        const attempt =
          sameSelection ||
          (prior && JSON.stringify(prior.payload) === JSON.stringify(payload))
            ? prior
            : { payload, request_id: requestId(), message_id: requestId() };
        remember(key, attempt);
        if (!attempt.task) {
          const value = await client.request("/api/personal/items/chat", {
            ...payload,
            request_id: attempt.request_id,
          });
          attempt.task = value.task;
          attempt.conversation = value.conversation;
          remember(key, attempt);
        }
        return deliver(key, attempt);
      },
    };
  }
  return { create };
});
