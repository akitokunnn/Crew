const sampleTask = new URLSearchParams(location.search).get("task") || "voice";
if (sampleTask !== "voice") {
  const name =
    sampleTask === "crew" ? "让 Crew 的对话更从容" : "作品集的新首页";
  document.title = name + " · 样例成果";
  document.querySelector(".eyebrow").textContent = "TASK PREVIEW / SAMPLE";
  document.querySelector("h1").textContent = name;
  document.querySelector(".preview-top a").href =
    "index.html#task/" + sampleTask + "/leader";
  document.querySelector(".sample-label").textContent =
    (sampleTask === "crew" ? "v0.2.0" : "v0.1.0") + " · 明确样例";
  document.querySelector(".sample-drop .badge").textContent =
    "本任务的独立样例成果";
  document.querySelector(".sample-product > p").textContent =
    "从任务打开成果，体验后回到同一个任务继续反馈。这里不使用其他任务的内容。";
  document.querySelector("#demo-run").textContent = "体验阶段反馈 →";
}
document.querySelector("#demo-run").addEventListener("click", async (event) => {
  const button = event.currentTarget,
    state = document.querySelector("#demo-state"),
    description = document.querySelector("#demo-description");
  if (button.disabled) return;
  button.disabled = true;
  document.body.classList.add("running");
  state.textContent =
    sampleTask === "voice" ? "正在识别这段样例…" : "正在切换阶段状态…";
  description.textContent = "这是预设状态动画，不表示真实执行进度。";
  await new Promise((resolve) => setTimeout(resolve, 1100));
  document.body.classList.remove("running");
  state.textContent =
    sampleTask === "voice"
      ? "样例完成 · 识别为「我的声音」"
      : "样例成果已准备好，可以继续反馈";
  description.textContent = "你可以回到原任务，继续告诉 leader 你的体验。";
  button.disabled = false;
  button.textContent = "再体验一次 →";
});
