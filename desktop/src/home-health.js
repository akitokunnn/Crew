'use strict';
(() => {
  const $=id=>document.getElementById(id),bridge=window.crewDesktop,panel=$('admin-page'),nav=$('nav-admin');
  const supported=typeof bridge?.adminStatus==='function'&&typeof bridge?.adminCheck==='function'&&typeof bridge?.adminRestart==='function';
  const labels={starting:'正在启动',healthy:'运行正常',degraded:'服务响应异常',recovering:'正在自动恢复',failed:'无法连接',stopped:'后台已停止',unmanaged:'外部服务',unknown:'状态未知',paused:'维护中 / 已暂停',circuit_open:'自动恢复已暂停',stopping:'正在停止'};
  let snapshot=null,pending=null,error='',controlError='',receipt=null,note='',timer=null,visible=false,incidentsKey='',operationsKey='';
  const date=value=>{const parsed=new Date(value);return value&&!Number.isNaN(parsed.valueOf())?parsed.toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'}):'尚未记录';};
  function render(){
    const state=error?'unknown':snapshot?.state||'unknown';
    $('admin-state').textContent=error?'检测失败':labels[state]||labels.unknown;$('admin-state').dataset.state=state;
    $('admin-checked').textContent=snapshot?.checked_at?date(snapshot.checked_at):'尚未检测';
    $('admin-recoveries').textContent=Number.isFinite(snapshot?.recoveries)?String(snapshot.recoveries):'—';
    $('admin-last-recovery').textContent=snapshot?.last_recovery_at?'最近恢复 '+date(snapshot.last_recovery_at):'尚无自动恢复记录';
    $('admin-check').disabled=pending!==null;$('admin-check').textContent=pending==='check'?'正在检测…':'重新检测';
    const allowed=snapshot?.restart?.allowed===true&&!error;
    $('admin-restart').disabled=pending!==null||!allowed;$('admin-restart').textContent=pending==='restart'?'正在请求恢复…':'尝试恢复';
    $('admin-restart-reason').textContent=snapshot?.restart?.reason||(!snapshot?'读取状态后确认是否可以安全恢复。':allowed?'仅恢复确认已退出的后台。':'当前不允许安全恢复。');
    $('admin-error').textContent=error||controlError;$('admin-error').hidden=!error&&!controlError;
    $('admin-note').textContent=pending==='status'?'正在读取本机后台状态…':note;$('admin-note').hidden=!$('admin-note').textContent;
    for(const [id,key]of [['admin-supervisor-pid','supervisor_pid'],['admin-service-pid','service_pid'],['admin-service-url','url'],['admin-instance','instance_id']])$(id).textContent=snapshot?.[key]??'—';
    const incidents=Array.isArray(snapshot?.incidents)?snapshot.incidents:[],key=JSON.stringify([Boolean(snapshot),incidents]);
    if(key!==incidentsKey){incidentsKey=key;$('admin-incidents').replaceChildren();
      if(!incidents.length){const item=document.createElement('p');item.className='admin-empty';item.textContent=snapshot?'尚无异常记录。':'正在读取最近记录…';$('admin-incidents').append(item);}
      for(const incident of incidents.slice().reverse().slice(0,20)){
        const row=document.createElement('article');row.className='admin-incident';row.dataset.level=String(incident.level||'info');
        const at=document.createElement('time');at.textContent=date(incident.at);const message=document.createElement('p');message.textContent=String(incident.message||'未填写说明');row.append(at,message);$('admin-incidents').append(row);
      }
    }
    const operations=snapshot?.operations,opsKey=JSON.stringify(operations||null);
    if(opsKey!==operationsKey){operationsKey=opsKey;const list=$('admin-operations');list.replaceChildren();
      const items=Array.isArray(operations?.incidents)?operations.incidents:[];
      if(!items.length){const empty=document.createElement('p');empty.className='admin-empty';empty.textContent=operations?.error||'尚无需要处理的事故。';list.append(empty);}
      const states={queued:'等待修复策略',waiting_evidence:'相同故障已有分析，等待新增证据',repairing:'正在隔离修复',awaiting_merge:'已验证并提交，等待项目合并',failed:'修复失败，现场已保留',resolved:'已恢复',validating_release:'正在复验已合并版本',staged:'已验证，等待自有后台退出后切换',active:'修复已应用',rolled_back:'新版本失败，已回到此前健康来源',refused:'应用被拒绝'};
      for(const item of items.slice().reverse()){
        const row=document.createElement('article');row.className='admin-incident';
        const summary=document.createElement('p');summary.textContent=(item.summary||states[item.status]||item.status)+' · 诊断 '+String(item.attempts||0)+'/2 次';
        const detail=document.createElement('p');detail.textContent='影响目标：'+(item.goals?.join('、')||'Crew 后台')+(item.result?.commit?' · commit '+item.result.commit.slice(0,12):'');row.append(summary,detail);
        if(operations?.report?.incident_id===item.id){const report=document.createElement('details'),title=document.createElement('summary'),frame=document.createElement('iframe');title.textContent='查看 HTML 事故报告';frame.title='Crew 事故报告';frame.setAttribute('sandbox','');frame.srcdoc=operations.report.html;frame.className='admin-report';report.append(title,frame);row.append(report);}
        list.append(row);
      }
    }
  }
  function isVisible(){return !panel.hidden&&document.visibilityState!=='hidden'&&!document.getElementById('crew-desktop-home')?.hidden;}
  function clear(){clearTimeout(timer);timer=null;}
  function schedule(){clear();if(isVisible())timer=setTimeout(()=>{timer=null;if(isVisible())void request('status');},5000);}
  function consume(value){
    snapshot=value;
    if(receipt&&snapshot?.last_request_id===receipt.id){
      controlError=snapshot.last_request_error||'';
      note=controlError?'':receipt.operation==='check'?'检测请求已处理，结果见当前状态。':'恢复请求已处理，结果见当前状态。';
      receipt=null;
    }
  }
  async function request(operation){
    if(pending!==null)return;
    pending=operation;error='';if(operation!=='status'){note='';controlError='';receipt=null;}else if(!receipt)note='';render();
    try{
      if(operation==='restart'){
        const result=await bridge.adminRestart();if(result?.ok!==true)throw Error(result?.reason||result?.error||'恢复请求尚未被确认。');
        if(result.request_id)receipt={id:result.request_id,operation};note='已请求恢复，正在读取实际状态。';consume(await bridge.adminStatus());
      }else if(operation==='check'){
        const result=await bridge.adminCheck();if(result?.ok!==true)throw Error(result?.reason||result?.error||'检测请求尚未被确认。');
        if(result.request_id)receipt={id:result.request_id,operation};note='已请求检测，等待实际检测结果。';consume(await bridge.adminStatus());
      }else consume(await bridge.adminStatus());
      if(!snapshot||typeof snapshot.state!=='string')throw Error('后台状态暂时无法读取，请重新检测。');
    }catch(caught){const message=(caught?.message||String(caught)).replace(/^Error invoking remote method '[^']+':\s*(?:Error:\s*)?/,'');if(operation==='restart')controlError=message;else error=message;}
    finally{pending=null;render();schedule();}
  }
  function syncVisibility(){const next=isVisible();if(next===visible)return;visible=next;if(next)void request('status');else clear();}
  function hide(){panel.hidden=true;nav.classList.remove('active');nav.removeAttribute('aria-current');visible=false;clear();}
  function show(){
    window.CrewHomeSettings?.hide();
    if(!supported)return;window.CrewHomeTools?.hide();$('workspaces-page').hidden=true;$('tools-page').hidden=true;panel.hidden=false;
    for(const id of ['nav-workspaces','nav-tools']){$(id).classList.remove('active');$(id).removeAttribute('aria-current');}
    nav.classList.add('active');nav.setAttribute('aria-current','page');$('admin-title').focus();syncVisibility();
  }
  nav.hidden=!supported;
  nav.addEventListener('click',show);$('nav-workspaces').addEventListener('click',hide);$('nav-tools').addEventListener('click',hide);
  $('admin-check').addEventListener('click',()=>{void request('check');});
  $('admin-conversation').addEventListener('click',()=>{void window.CrewHomeSettings.show('backend');});
  $('admin-restart').addEventListener('click',()=>{if(snapshot?.restart?.allowed===true&&!$('admin-restart').disabled)void request('restart');});
  document.addEventListener('visibilitychange',syncVisibility);
  new MutationObserver(syncVisibility).observe(document.body,{subtree:true,attributes:true,attributeFilter:['hidden']});
  window.addEventListener('pagehide',clear);
  function mount(host){const parent=panel.parentNode;host.append(panel);$('admin-conversation').hidden=true;panel.hidden=false;syncVisibility();return()=>{hide();$('admin-conversation').hidden=false;parent.append(panel);};}
  render();window.CrewHomeHealth={show,hide,mount};
})();
