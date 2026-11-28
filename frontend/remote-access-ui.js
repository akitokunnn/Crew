"use strict";
(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  let qrScript;
  function loadQrCode(){
    qrScript ??= new Promise((resolve,reject)=>{
      const script=document.createElement("script");
      script.src="/vendor/qrcode-generator.js";
      script.onload=()=>{
        if(typeof window.qrcode==="function")resolve();
        else{script.remove();reject(new Error("二维码组件未加载"));}
      };
      script.onerror=()=>{script.remove();reject(new Error("无法读取二维码组件"));};
      document.head.append(script);
    }).catch(error=>{qrScript=null;throw error;});
    return qrScript;
  }
  function mount(host, {read, action}) {
    let snapshot=null,pair=null,busy=false,error="",actionError="",disposed=false,timer,lastMarkup=null,generation=0,address=null,copied=null,copyTimer;
    let pairTimer, qrError="", loadingQr=false;
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
    // One string carries address, port and code; the code stays in the fragment, so it never reaches a server log.
    const pairLink=(url,code)=>url+"/remote/login#code="+code;
    function setPair(value){
      clearTimeout(pairTimer);
      pair=value;
      qrError="";
      if(pair)pairTimer=setTimeout(()=>{setPair(null);render();},Math.max(0,Date.parse(pair.expires_at)-Date.now()));
    }
    function pairAddress(url){
      const item=snapshot.addresses.find(entry=>entry.url===url);
      const virtual=item?.kind==="zerotier"||
        (item?.kind==="other"&&/virtual|vethernet|hyper-v|vmware|tailscale|tunnel|vpn|虚拟/i.test(item.name+" "+item.description));
      return {url,item,folded:snapshot.bind===allNetworks&&virtual};
    }
    function pairAddressMarkup({url,item,folded}){
      const link=pairLink(url,pair.code);
      let svg="";
      if(window.qrcode){
        const qr=window.qrcode(0,"M");
        qr.addData(link);
        qr.make();
        svg=`<div class="remote-qr" aria-hidden="true">${qr.createSvgTag({cellSize:4, margin:16, scalable:true})}</div>`;
      }
      const content=`<div class="remote-pair-address">${svg}<div class="remote-link">
        <code>${esc(link)}</code><button type="button" class="cs-button" data-remote-action="copy-link" data-link="${esc(link)}">${copied===link?"已复制":"复制连接链接"}</button></div></div>`;
      return folded?`<details class="remote-pair-fold" data-pair-address="${esc(url)}"><summary>${esc(item.address)} · 虚拟网卡</summary>${content}</details>`:content;
    }
    function pairMarkup(){
      const links=snapshot.urls.map(pairAddress);
      const otherLinks=links.filter(link=>link.folded);
      return `<div class="remote-pair" role="status"><span>配对码</span><strong>${esc(pair.code.slice(0,4))}<span class="remote-code-tail">${esc(pair.code.slice(4))}</span></strong><small>仅可使用一次 · ${esc(when(pair.expires_at))} 失效</small>
        ${links.length?`<p class="remote-scan-hint">用 Crew 安卓版直接扫码，或用手机相机扫码在浏览器里打开</p>
          ${qrError?`<small class="remote-qr-error">二维码无法显示：${esc(qrError)}</small>`:""}
          ${links.filter(link=>!link.folded).map(pairAddressMarkup).join("")}
          ${otherLinks.length?`<details class="remote-pair-fold" data-pair-address="other"><summary>其他地址（${otherLinks.length}）</summary>${otherLinks.map(pairAddressMarkup).join("")}</details>`:""}
          <details class="remote-pair-fold" data-pair-address="desktop-help"><summary>其他电脑怎么连</summary>
            <small>Crew 桌面客户端在“设置 → 后台与运维 → 连接与目录”添加局域网主机时粘贴连接链接。</small>
          </details>`:""}</div>`;
    }
    function advertisingMarkup(){
      const advertising=snapshot.discovery_advertising;
      if(advertising?.active)return '<p class="remote-discovery">附近的手机可以自动找到这台电脑</p>';
      if(advertising?.error)return `<p class="cs-error">自动发现不可用：${esc(advertising.error)}。扫码和手填地址照常可用</p>`;
      return "";
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
      if(disposed)return;if(address===allNetworks&&(!snapshot.enabled||snapshot.bind!==allNetworks))selectCurrent();if(pair&&Date.parse(pair.expires_at)<=Date.now())setPair(null);
      const canPair=!busy&&snapshot?.binding_available!==false&&(!address||address===snapshot?.bind);
      const markup=`<section class="cs-card remote-access"><div class="cs-card-heading"><div><h2>局域网访问</h2><p>让手机和其他电脑通过局域网使用这里的 Crew。</p></div><span class="cs-badge">${snapshot?.enabled?snapshot.binding_available===false?"地址不可用":"已开启":"未开启"}</span></div>${!snapshot?'<p>正在读取…</p>':bindingMarkup()+(snapshot.enabled?`
        <h3>当前访问地址</h3><p>生成配对码后复制连接链接发到另一台设备即可；也可以在那台设备打开下方地址，手动输入配对码。</p>
        <div class="remote-addresses">${snapshot.urls.map(url=>`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a>`).join("")||`<p class="cs-error">${esc(snapshot.binding_error||"暂未找到可访问地址，请检查所选网络。")}</p>`}</div>
        ${advertisingMarkup()}
        ${pair?pairMarkup():snapshot.pair_pending?'<p class="remote-hint">已有待使用的配对码。需要时可重新生成，旧码会失效。</p>':""}
        <div class="remote-actions"><button class="cs-button cs-primary" data-remote-action="pair" ${canPair?"":"disabled"}>${pair||snapshot.pair_pending?"重新生成配对码":"生成配对码"}</button><button class="cs-button" data-remote-action="stop" ${busy?"disabled":""}>关闭访问</button></div>
        ${devicesMarkup()}`:`<p>文件和 AI 仍在这台电脑上运行。选择地址不会启动任务。</p><button class="cs-button cs-primary" data-remote-action="start" ${busy||!selected()?.available?"disabled":""}>开启访问</button>`)}
        <p class="remote-status ${error?"cs-error":""}" role="status">${esc(error||(busy?"正在处理…":""))}</p>
        ${error&&!snapshot?'<button class="cs-button" data-remote-action="refresh">重新读取</button>':""}</section>`;
      if(markup!==lastMarkup){
        const keepFocus=host.contains(document.activeElement)&&document.activeElement.name==="remote-bind";
        const open=new Set(Array.from(host.firstElementChild?.querySelectorAll(".remote-pair-fold[open]")||[], row=>row.dataset.pairAddress));
        host.innerHTML=markup;lastMarkup=markup;
        if(open.size)for(const row of host.querySelectorAll(".remote-pair-fold"))row.open=open.has(row.dataset.pairAddress);
        if(keepFocus)host.querySelector('[name="remote-bind"]').focus({preventScroll:true});
      }
      if(pair&&snapshot.urls.length&&!window.qrcode&&!loadingQr&&!qrError){
        loadingQr=true;
        void loadQrCode().catch(caught=>{qrError=caught.message;}).finally(()=>{loadingQr=false;render();});
      }
    }
    async function refresh({keepError=false}={}){
      const request=++generation;
      try{const value=await read();if(request!==generation||disposed)return;snapshot=value;if(address===null)selectCurrent();if(!keepError)error=actionError||snapshot.last_error||"";
        if(!snapshot.pair_pending||(pair&&snapshot.pair_expires_at&&snapshot.pair_expires_at!==pair.expires_at))setPair(null);}
      catch(caught){if(request===generation&&!keepError)error=actionError||caught.message;}render();
    }
    host.onchange=event=>{if(busy||event.target.name!=="remote-bind")return;address=event.target.value;actionError="";error="";render();};
    host.onclick=async event=>{
      const button=event.target.closest("[data-remote-action]");if(!button||button.disabled||busy)return;const kind=button.dataset.remoteAction;
      if(kind==="refresh"){await refresh();return;}
      if(kind==="copy-link"){const link=button.dataset.link;
        try{await window.CrewBrowser.copyText(link);copied=link;clearTimeout(copyTimer);copyTimer=setTimeout(()=>{copied=null;render();},2000);}
        catch(caught){error="复制失败："+(caught.message||caught)+"。可以选中链接手动复制。";}render();return;}const bind=selected()?.address;if(["start","switch"].includes(kind)&&!selected()?.available)return;
      busy=true;generation++;actionError="";error="";if(["pair","switch"].includes(kind))setPair(null);render();
      try{
        if(kind==="switch")snapshot=await action({action:"stop"});
        const result=await action({action:kind==="switch"?"start":kind,...(["start","switch"].includes(kind)?{bind,port:snapshot.port}:{}),...(kind==="revoke"?{device_id:button.dataset.deviceId}:{})});
        if(typeof result.enabled==="boolean")snapshot=result;
        if(kind==="pair")setPair({code:result.code,expires_at:result.expires_at});
        else if(kind==="stop")setPair(null);
        // A failed read or lost response never replays a completed lifecycle action.
        await refresh();
      }catch(caught){actionError=error=caught.message;await refresh({keepError:true});}finally{busy=false;render();}
    };
    render();void refresh();timer=setInterval(()=>{if(!busy)void refresh();},5000);
    return()=>{disposed=true;generation++;clearInterval(timer);if(copyTimer)clearTimeout(copyTimer);host.onclick=null;host.onchange=null;setPair(null);};
  }
  window.CrewRemoteAccess={mount};
})();
