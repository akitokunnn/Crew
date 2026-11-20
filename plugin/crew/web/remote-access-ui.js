"use strict";
(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  function mount(host, {read, action}) {
    let snapshot=null,pair=null,busy=false,error="",actionError="",disposed=false,timer,lastMarkup=null,generation=0,address=null;
    const when=value=>new Date(value).toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit"});
    const lastUsed=value=>new Date(value).toLocaleString("zh-CN",{
      year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"
    });
    const allNetworks="0.0.0.0";
    const noAddress="未检测到可用的网络地址，请检查网络连接后刷新。";
    function choices(){
      const list=snapshot.addresses.filter(item=>item.kind!=="loopback");
      if(snapshot.enabled&&snapshot.bind===allNetworks){
        list.unshift({address:allNetworks,name:"全部网络 · 当前设置",available:true});
      }
      return list;
    }
    function selected(){return snapshot&&choices().find(item=>item.address===address);}
    function selectCurrent(){
      const list=choices();
      if(list.some(item=>item.address===snapshot.bind))address=snapshot.bind;
      else if(snapshot.enabled)address="";
      else address=list.find(item=>item.available)?.address||"";
    }
    function optionLabel(item){
      if(item.address===allNetworks)return item.name;
      const description=item.description&&item.description!==item.name?" · "+item.description:"";
      return item.address+" · "+item.name+description+(item.available?"":" · 不可用");
    }
    function bindingMarkup(){
      const list=choices(),item=selected();
      const available=item?.available===true;
      const hint=available?"另一台设备需要连在这个地址所在的网络上。":
        item?.reason||(address&&!item?"原地址当前不可用，请选择其他地址，或连接后刷新。":noAddress);
      const missing=address&&!item?`<option value="${esc(address)}" selected disabled>${esc(address)} · 当前不可用</option>`:"";
      const options=list.map(entry=>`<option value="${esc(entry.address)}" ${entry.address===address?"selected":""} ${entry.available?"":"disabled"}>${esc(optionLabel(entry))}</option>`).join("");
      return `<fieldset class="remote-binding" ${busy?"disabled":""}>
        <label class="remote-address-choice">这台电脑的地址<select name="remote-bind" ${list.length?"":"disabled"}>
          ${missing}${!address?'<option value="">选择可用地址</option>':""}${options}
        </select></label>
        <p class="remote-hint">${esc(list.some(entry=>entry.available)?hint:noAddress)}</p>
        ${snapshot.discovery?.error?`<p class="cs-error">${esc(snapshot.discovery.error)}</p>`:""}
        <button type="button" class="cs-button remote-refresh" data-remote-action="refresh">刷新地址</button>
        ${snapshot.enabled&&address&&address!==snapshot.bind?`<div class="remote-apply">
          <button class="cs-button cs-primary" data-remote-action="switch" ${available?"":"disabled"}>切换访问地址</button>
          <p class="remote-hint">切换会短暂断开网页连接，现有任务继续运行。</p>
        </div>`:""}
      </fieldset>`;
    }
    function devicesMarkup(){
      const devices=snapshot.devices.map(device=>`<div class="remote-device">
        <div><strong>${esc(device.name)}</strong><small>${device.kind==="desktop"?"Crew 桌面客户端":"浏览器"}</small>
        ${device.online?'<small class="remote-online">在线</small>':`<small>最近使用 ${esc(lastUsed(device.last_seen_at))}</small>`}</div>
        <button class="cs-button" data-remote-action="revoke" data-device-id="${esc(device.id)}" ${busy?"disabled":""}>强制下线</button>
      </div>`).join("");
      return `<h3>已连接设备</h3><div class="remote-devices">${devices||'<p class="remote-hint">还没有配对的设备。</p>'}</div>
        <p class="remote-hint">30 天不用会自动失效；强制下线后需要重新配对。</p>
        ${devices?`<button class="cs-button" data-remote-action="revoke_all" ${busy?"disabled":""}>全部下线</button>`:""}`;
    }
    function render(){
      if(disposed)return;if(address===allNetworks&&(!snapshot.enabled||snapshot.bind!==allNetworks))selectCurrent();if(pair&&Date.parse(pair.expires_at)<=Date.now())pair=null;
      const canPair=!busy&&snapshot?.binding_available!==false&&(!address||address===snapshot?.bind);
      const markup=`<section class="cs-card remote-access"><div class="cs-card-heading"><div><h2>局域网访问</h2><p>让手机和其他电脑通过局域网使用这里的 Crew。</p></div><span class="cs-badge">${snapshot?.enabled?snapshot.binding_available===false?"地址不可用":"已开启":"未开启"}</span></div>${!snapshot?'<p>正在读取…</p>':bindingMarkup()+(snapshot.enabled?`
        <h3>当前访问地址</h3><p>在另一台设备打开下方地址，再输入这台电脑生成的配对码。</p>
        <div class="remote-addresses">${snapshot.urls.map(url=>`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a>`).join("")||`<p class="cs-error">${esc(snapshot.binding_error||"暂未找到可访问地址，请检查所选网络。")}</p>`}</div>
        ${pair?`<div class="remote-pair" role="status"><span>配对码</span><strong>${esc(pair.code)}</strong><small>仅可使用一次 · ${esc(when(pair.expires_at))} 失效</small></div>`:snapshot.pair_pending?'<p class="remote-hint">已有待使用的配对码。需要时可重新生成，旧码会失效。</p>':""}
        <div class="remote-actions"><button class="cs-button cs-primary" data-remote-action="pair" ${canPair?"":"disabled"}>${pair||snapshot.pair_pending?"重新生成配对码":"生成配对码"}</button><button class="cs-button" data-remote-action="stop" ${busy?"disabled":""}>关闭访问</button></div>
        ${devicesMarkup()}`:`<p>文件和 AI 仍在这台电脑上运行。选择地址不会启动任务。</p><button class="cs-button cs-primary" data-remote-action="start" ${busy||!selected()?.available?"disabled":""}>开启访问</button>`)}
        <p class="remote-status ${error?"cs-error":""}" role="status">${esc(error||(busy?"正在处理…":""))}</p>
        ${error&&!snapshot?'<button class="cs-button" data-remote-action="refresh">重新读取</button>':""}</section>`;
      if(markup!==lastMarkup){
        const keepFocus=host.contains(document.activeElement)&&document.activeElement.name==="remote-bind";
        host.innerHTML=markup;lastMarkup=markup;
        if(keepFocus)host.querySelector('[name="remote-bind"]').focus({preventScroll:true});
      }
    }
    async function refresh({keepError=false}={}){
      const request=++generation;
      try{const value=await read();if(request!==generation||disposed)return;snapshot=value;if(address===null)selectCurrent();if(!keepError)error=actionError||snapshot.last_error||"";if(!snapshot.pair_pending)pair=null;}
      catch(caught){if(request===generation&&!keepError)error=actionError||caught.message;}render();
    }
    host.onchange=event=>{if(busy||event.target.name!=="remote-bind")return;address=event.target.value;actionError="";error="";render();};
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
