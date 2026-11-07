"use strict";
(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  function mount(host, {read, action}) {
    let snapshot=null,pair=null,busy=false,error="",actionError="",disposed=false,timer,lastMarkup=null,generation=0,method=null,address="";
    const when=value=>new Date(value).toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit"});
    const addresses=()=>snapshot?.addresses||[];
    function currentMethod(){if(snapshot?.bind==="0.0.0.0"&&snapshot.enabled)return "all";const item=addresses().find(item=>item.address===snapshot?.bind);return item?.kind==="zerotier"?"zerotier":item&&item.kind!=="lan"?"current":"lan";}
    function choices(){return addresses().filter(item=>method==="current"?item.address===snapshot.bind:item.kind===method);}
    function selected(){return method==="all"?{address:"0.0.0.0",available:true}:choices().find(item=>item.address===address);}
    function selectCurrent(){method=currentMethod();address=choices().some(item=>item.address===snapshot.bind)?snapshot.bind:choices().find(item=>item.available)?.address||"";}
    function bindingMarkup(){
      if(!snapshot)return "";
      const item=selected(),changed=item?.address!==snapshot.bind,available=item?.available===true;
      const methods=[["lan","局域网"],["zerotier","ZeroTier"],...(snapshot.enabled&&snapshot.bind==="0.0.0.0"?[["all","全部网络 · 当前设置"]]:[]),...(currentMethod()==="current"?[["current","当前地址"]]:[])];
      const list=choices(),empty=method==="zerotier"?"未检测到可用的 ZeroTier 地址。请先在这台电脑上连接 ZeroTier，再刷新地址。":"未检测到可用的局域网地址，请检查网络连接后刷新。";
      return `<fieldset class="remote-binding" ${busy?"disabled":""}><legend>连接方式</legend><div class="remote-methods">${methods.map(([value,label])=>`<label><input type="radio" name="remote-method" value="${value}" ${method===value?"checked":""}><span>${label}</span></label>`).join("")}</div>${method==="all"?'<p class="remote-hint">当前允许所有本机网络访问。选择具体网络地址，可以只在该网络提供服务。</p>':`<label class="remote-address-choice">这台电脑的地址<select name="remote-bind" ${list.length?"":"disabled"}>${address&&!list.some(entry=>entry.address===address)?`<option value="${esc(address)}" selected disabled>${esc(address)} · 当前不可用</option>`:""}${!list.length||!address?'<option value="">选择可用地址</option>':""}${list.map(entry=>`<option value="${esc(entry.address)}" ${entry.address===address?"selected":""} ${entry.available?"":"disabled"}>${esc(entry.address)} · ${esc(entry.name||entry.description||"网络接口")}${entry.available?"":" · 不可用"}</option>`).join("")}</select></label><p class="remote-hint">${esc(!available?item?.reason||(address&&!item?"原地址当前不可用，请选择其他地址，或连接后刷新。":empty):method==="zerotier"?"另一台设备需已加入同一个 ZeroTier 网络。Crew 只使用所选地址，不管理你的 ZeroTier 网络。":"另一台设备需连接到这个地址所在的局域网。")}</p>`}${snapshot.discovery?.error?`<p class="cs-error">${esc(snapshot.discovery.error)}</p>`:""}<button type="button" class="remote-refresh" data-remote-action="refresh">刷新地址</button>${snapshot.enabled&&changed?`<div class="remote-apply"><button class="cs-button cs-primary" data-remote-action="switch" ${available?"":"disabled"}>切换访问地址</button><p class="remote-hint">切换会短暂断开网页连接，现有任务继续运行。</p></div>`:""}</fieldset>`;
    }
    function render(){
      if(disposed)return;if(method==="all"&&!snapshot?.enabled)selectCurrent();if(pair&&Date.parse(pair.expires_at)<=Date.now())pair=null;
      const canPair=!busy&&snapshot?.binding_available!==false&&selected()?.address===snapshot?.bind;
      const markup=`<section class="cs-card remote-access"><div class="cs-card-heading"><div><h2>局域网访问</h2><p>通过局域网或 ZeroTier，让手机和其他电脑继续使用这里的 Crew。</p></div><span class="cs-badge">${snapshot?.enabled?snapshot.binding_available===false?"地址不可用":"已开启":"未开启"}</span></div>${!snapshot?'<p>正在读取…</p>':bindingMarkup()+(snapshot.enabled?`<h3>当前访问地址</h3><p>在另一台设备打开下方地址，再输入这台电脑生成的配对码。</p><div class="remote-addresses">${snapshot.urls.map(url=>`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a>`).join("")||`<p class="cs-error">${esc(snapshot.binding_error||"暂未找到可访问地址，请检查所选网络。")}</p>`}</div>${pair?`<div class="remote-pair" role="status"><span>配对码</span><strong>${esc(pair.code)}</strong><small>仅可使用一次 · ${esc(when(pair.expires_at))} 失效</small></div>`:snapshot.pair_pending?'<p class="remote-hint">已有待使用的配对码。需要时可重新生成，旧码会失效。</p>':""}<div class="remote-actions"><button class="cs-button cs-primary" data-remote-action="pair" ${canPair?"":"disabled"}>${pair||snapshot.pair_pending?"重新生成配对码":"生成配对码"}</button><button class="cs-button" data-remote-action="stop" ${busy?"disabled":""}>关闭访问</button></div><h3>已连接设备</h3><div class="remote-devices">${snapshot.devices.map(device=>`<div class="remote-device"><div><strong>${esc(device.name||"浏览器")}</strong><small>最近使用 ${esc(when(device.last_seen_at||device.created_at))}</small></div><button class="cs-button" data-remote-action="revoke" data-device-id="${esc(device.id)}" ${busy?"disabled":""}>移除</button></div>`).join("")||'<p class="remote-hint">还没有配对的设备。</p>'}</div>${snapshot.devices.length?`<button class="cs-button" data-remote-action="revoke_all" ${busy?"disabled":""}>移除所有设备</button>`:""}`:`<p>文件和 AI 仍在这台电脑上运行。选择地址不会启动任务。</p><button class="cs-button cs-primary" data-remote-action="start" ${busy||!selected()?.available?"disabled":""}>开启访问</button>`)}<p class="remote-status ${error?"cs-error":""}" role="status">${esc(error||(busy?"正在处理…":""))}</p>${error&&!snapshot?'<button class="cs-button" data-remote-action="refresh">重新读取</button>':""}</section>`;
      if(markup!==lastMarkup){const focusName=host.contains(document.activeElement)?document.activeElement.getAttribute("name"):null;host.innerHTML=markup;lastMarkup=markup;if(focusName)host.querySelector(`[name="${focusName}"]${focusName==="remote-method"?":checked":""}`)?.focus({preventScroll:true});}
    }
    async function refresh({keepError=false}={}){
      const request=++generation;
      try{const value=await read();if(request!==generation||disposed)return;snapshot=value;if(method===null)selectCurrent();if(!keepError)error=actionError||snapshot.last_error||"";if(!snapshot.pair_pending)pair=null;}
      catch(caught){if(request===generation&&!keepError)error=actionError||caught.message;}render();
    }
    host.onchange=event=>{if(busy)return;if(event.target.name==="remote-method"){method=event.target.value;address=choices().find(item=>item.available)?.address||"";}else if(event.target.name==="remote-bind")address=event.target.value;else return;actionError="";error="";render();};
    host.onclick=async event=>{
      const button=event.target.closest("[data-remote-action]");if(!button||button.disabled||busy)return;const kind=button.dataset.remoteAction;
      if(kind==="refresh"){await refresh();return;}const bind=selected()?.address;if(["start","switch"].includes(kind)&&!selected()?.available)return;
      busy=true;generation++;actionError="";error="";if(["pair","switch"].includes(kind))pair=null;render();
      try{
        if(kind==="switch")snapshot=await action({action:"stop"});
        const result=await action({action:kind==="switch"?"start":kind,...(["start","switch"].includes(kind)?{bind,port:snapshot.port}:{}),...(kind==="revoke"?{device_id:button.dataset.deviceId}:{})});
        if(typeof result.enabled==="boolean")snapshot=result;
        pair=kind==="pair"?{code:result.code,expires_at:result.expires_at}:kind==="stop"?null:pair;
        // A failed read or lost response never replays a completed lifecycle action.
        await refresh();
      }catch(caught){actionError=error=caught.message;await refresh({keepError:true});}finally{busy=false;render();}
    };
    render();void refresh();timer=setInterval(()=>{if(!busy)void refresh();},5000);
    return()=>{disposed=true;generation++;clearInterval(timer);host.onclick=null;host.onchange=null;pair=null;};
  }
  window.CrewRemoteAccess={mount};
})();
