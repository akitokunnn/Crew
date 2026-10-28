"use strict";
(() => {
  const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const drafts=new Map(),states={prepared:"已准备，尚未送达",sent:"已发送，未获原生确认",confirmed:"原生已确认接收"};
  function evidenceMarkup(value){
    const records=value.applications||[];
    return records.length?records.slice().reverse().map(item=>`<details class="bp-evidence"><summary>${esc(states[item.status]||item.status)} · ${esc(item.created_at)}</summary><p>${esc(item.evidence||"没有送达回执")}</p><pre>${esc(JSON.stringify(item.manifest||item,null,2))}</pre></details>`).join(""):'<p class="cs-hint">还没有本会话的配置发送记录。保存配置不会启动执行。</p>';
  }
  function mount(host,{read,save,project=null,session=null,execution=null}){
    let disposed=false,items=[],key="common",scope=session?"session":project?"project":"global",busy=false,generation=0;
    const identity=()=>scope==="project"?project:scope==="session"?session:null;
    const query=(action="list",extra={})=>({action,scope,...(identity()?{identity:identity()}:{}),...extra});
    const draftKey=()=>`${scope}:${identity()||""}:${key}`;
    const selected=()=>items.find(item=>item.key===key);
    function message(text){if(!disposed)host.querySelector('[role="status"]').textContent=text;}
    function keep(){const input=host.querySelector('textarea');if(input&&selected())drafts.set(draftKey(),{text:input.value,base:selected().version});}
    host.innerHTML=`<div class="bp-panel"><label class="bp-scope">生效范围<select data-bp-scope><option value="global">全 Crew</option>${project?'<option value="project">当前工作区</option>':""}${session?'<option value="session">当前会话</option>':""}</select></label><p class="cs-hint">会话覆盖工作区，工作区覆盖全局。保存后从下一次正常输入生效，不主动唤醒 AI。</p><div class="bp-layout"><nav aria-label="行为配置类别"></nav><section class="bp-editor"></section></div><p role="status" aria-live="polite"></p>${session?'<details class="bp-proof"><summary>本会话实际使用记录</summary><div data-bp-proof></div><button class="cs-button" data-bp-action="proof">刷新记录</button></details>':'<p class="cs-footnote">在工作区或对话中打开这里，可以设置对应范围，并查看实际发送记录。</p>'}</div>`;
    host.querySelector('[data-bp-scope]').value=scope;
    function render(){
      if(disposed)return;const item=selected();host.querySelector('nav').innerHTML=items.map(row=>`<button type="button" data-bp-key="${esc(row.key)}" aria-pressed="${row.key===key}">${esc(row.label)}${row.overridden?'<span>已覆盖</span>':""}</button>`).join("");
      host.querySelector('.bp-editor').innerHTML=item?`<h2>${esc(item.label)}</h2><p class="cs-hint">当前来源：${esc(({default:"内置默认",global:"全 Crew",project:"工作区",session:"会话"})[item.effective.source.scope] || item.effective.source.scope)} · 版本 ${esc(item.version)}</p><label>内容<textarea rows="15" spellcheck="false">${esc(drafts.get(draftKey())?.text??item.effective.content)}</textarea></label><div class="bp-actions"><button class="cs-button cs-primary" data-bp-action="save">保存</button><button class="cs-button" data-bp-action="reset" ${item.overridden?"":"disabled"}>${scope==="global"?"恢复内置默认":"恢复继承"}</button><button class="cs-button" data-bp-action="discard">撤销编辑</button></div><details><summary>内置默认</summary><pre>${esc(item.default)}</pre></details><details class="bp-history"><summary>修改历史</summary><div data-bp-history></div><button class="cs-button" data-bp-action="history">读取历史</button></details>`:'<p class="cs-hint">正在读取…</p>';
      const draft=drafts.get(draftKey());if(item&&draft&&draft.base!==item.version)message("配置已被其他入口更新。这里保留了你的草稿；请核对后再保存。");
    }
    async function load(){const id=++generation;try{const value=await read(query());if(disposed||id!==generation)return;items=value.items;render();}catch(error){message(error.message);}}
    async function proof(){try{const value=await read({action:"applied",scope_id:execution||session});if(!disposed)host.querySelector('[data-bp-proof]').innerHTML=evidenceMarkup(value);}catch(error){message(error.message);}}
    async function act(event){const button=event.target.closest('button');if(!button||busy)return;if(button.dataset.bpKey){keep();key=button.dataset.bpKey;render();return;}const action=button.dataset.bpAction;if(!action)return;
      if(action==="proof"){await proof();return;}const item=selected();if(!item)return;
      if(action==="discard"){drafts.delete(draftKey());render();message("");return;}
      busy=true;for(const control of host.querySelectorAll("button,select,textarea"))control.disabled=true;
      try{
        if(action==="history"){const value=await read(query("history",{key}));if(disposed)return;host.querySelector('[data-bp-history]').innerHTML=value.history.length?value.history.map(row=>`<details><summary>版本 ${esc(row.version)} · ${esc(row.created_at)} · ${row.action==="reset"?"恢复继承":"保存"}</summary><pre>${esc(row.content??row.effective_content??"已恢复继承")}</pre></details>`).join(""):'<p class="cs-hint">还没有修改记录。</p>';return;}
        keep();const draft=drafts.get(draftKey());await save({action,key,scope,identity:identity(),expected_version:draft?.base??item.version,...(action==="save"?{content:draft.text}:{})});drafts.delete(draftKey());await load();message("已保存，下一次正常输入使用新配置。");
      }catch(error){message(error.message);}
      finally{busy=false;if(!disposed){for(const control of host.querySelectorAll("button,select,textarea"))control.disabled=false;const reset=host.querySelector('[data-bp-action="reset"]');if(reset)reset.disabled=!selected()?.overridden;}}
    }
    host.onclick=act;host.oninput=event=>{if(event.target.tagName==="TEXTAREA")keep();};host.querySelector('[data-bp-scope]').onchange=event=>{keep();scope=event.target.value;items=[];render();message("");void load();};
    void load();if(session)void proof();
    return ()=>{keep();disposed=true;generation++;host.onclick=null;host.oninput=null;};
  }
  window.CrewBehaviorPrompts={mount,evidenceMarkup};
})();
