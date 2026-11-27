"use strict";
(() => {
  const paths = [
    {id:"war-room", title:"开作战室", detail:"助手先和你定计划，敲定后再组队开工。", recommended:true},
    {id:"leader", title:"建第一位 Leader", detail:"找一位负责人，聊清一件事就开工。"},
    {id:"mindmap", title:"先画思维导图", detail:"把零散的想法放到画布上，理清再动手。"}
  ];
  const text = ({title, detail, recommended}) => `<strong>${title}</strong>${recommended ? '<span class="start-recommended">推荐</span>' : ''}<span class="start-description">${detail}</span>`;
  window.CrewWorkspaceStart = {paths, text};
})();
