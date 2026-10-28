'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const workspace = new URLSearchParams(location.search).get('project')?.replace(/^storage:/, '');
  let snapshot, selected = null, oldest = null, requestGeneration = 0;
  const node = (tag, text, className) => { const n=document.createElement(tag);if(text!=null)n.textContent=text;if(className)n.className=className;return n; };
  async function read(query={}) {
    return CrewClient.current.request('/api/workspace-data/history?'+new URLSearchParams({workspace_id:workspace,...query}));
  }
  const artifactURL = id => CrewClient.current.resourceURL('/api/workspace-data/artifact?'+new URLSearchParams({workspace_id:workspace,version_id:id}));
  const stateLabel = value => ({informational:'信息记录',completed:'已完成',failed:'失败',awaiting_acceptance:'等待验收',cancelled:'已取消',paused:'已暂停',running:'执行中',draft:'草稿',active:'有效',archived:'已归档',open:'待处理'}[value]||value);
  function link(parent,id,label) { const a=node('a',label);a.href=artifactURL(id);a.target='_blank';a.rel='noopener';parent.append(a); }
  function card(title,body,meta) { const n=node('article',null,'card');if(title)n.append(node('strong',title));if(meta)n.append(node('p',meta,'meta'));if(body)n.append(node('div',body,'body'));return n; }
  async function list(kind, cursor=null) {
    const generation=++requestGeneration;selected=null;
    const page=await read({collection:kind,...(cursor?{before:JSON.stringify(cursor)}:{})});
    if(generation!==requestGeneration)return;
    if(!cursor)$('content').replaceChildren();
    $('content').querySelector('[data-more]')?.remove();
    const rows=page.items;
    for(const item of rows) {
      const c=card(item.title||item.name,item.summary||item.description||item.body_markdown||'',stateLabel(item.status||item.outcome||item.created_at));
      const version=item.html_artifact_version_id||item.current_version_id;if(version)link(c,version,'查看保存的产出');
      $('content').append(c);
    }
    if(!rows.length&&!cursor)$('content').append(node('p','这里还没有保存的记录。','empty'));
    if(page.has_more&&rows.length){const more=node('button','读取更多记录');more.dataset.more='1';const last=rows[rows.length-1];more.onclick=()=>list(kind,[last.created_at,last.id]).catch(fail);$('content').append(more);}
  }
  async function conversation(id, append=false) {
    const generation=++requestGeneration;selected=id;
    const query={conversation_id:id};if(append&&oldest)query.before=JSON.stringify(oldest);
    const page=await read(query);if(generation!==requestGeneration)return;
    if(!append)$('content').replaceChildren();
    $('content').querySelector('[data-more]')?.remove();
    const fragment=document.createDocumentFragment();
    for(const m of page.messages) {
      const c=card(m.author_name_snapshot||m.author_kind,'',m.created_at);
      for(const part of page.parts.filter(p=>p.message_id===m.id)) {
        if(part.text_content)c.append(node('div',part.text_content,'body'));
        else if(part.json_content)c.append(node('pre',part.json_content,'body'));
        else if(part.artifact_version_id)link(c,part.artifact_version_id,'查看附件');
        else if(part.blob_id){const a=node('a',part.kind==='image'?'查看图片':'查看附件');a.href='/api/workspace-data/blob?'+new URLSearchParams({workspace_id:workspace,blob_id:part.blob_id});a.target='_blank';a.rel='noopener';c.append(a);}
        else if(part.tool_call_id||part.tool_result_id){
          const call=page.tool_calls.find(t=>t.id===part.tool_call_id),result=page.tool_results.find(t=>t.id===part.tool_result_id);
          const details=node('details'),summary=node('summary',call?('工具调用 · '+(call.display_label||call.tool_name)):('工具结果 · '+stateLabel(result?.outcome||'记录')));details.append(summary);
          if(call?.arguments_json)details.append(node('pre',call.arguments_json,'body'));
          if(result?.result_text)details.append(node('pre',result.result_text,'body'));
          const blob=call?.arguments_blob_id||result?.result_blob_id;if(blob){const a=node('a','查看完整工具内容');a.href='/api/workspace-data/blob?'+new URLSearchParams({workspace_id:workspace,blob_id:blob});a.target='_blank';a.rel='noopener';details.append(a);}
          if(result?.artifact_version_id)link(details,result.artifact_version_id,'查看工具产出');
          if(result?.is_truncated)details.append(node('p','此记录在来源处已截断。','meta'));
          c.append(details);
        }
        else c.append(node('small','['+part.kind+']'));
      }
      fragment.append(c);
    }
    $('content').prepend(fragment);
    if(page.messages.length)oldest=[page.messages[0].sequence,page.messages[0].id];
    if(page.has_more){const more=node('button','读取更早的消息');more.dataset.more='1';more.onclick=()=>conversation(id,true).catch(fail);$('content').prepend(more);}
    if(!$('content').children.length)$('content').append(node('p','这段会话尚无可读消息。','empty'));
  }
  function fail(error){$('status').textContent=error.message;}
  function navRow(kind,item){
    if(kind==='agents'){const leaders=new Set(snapshot.groups.map(g=>g.leader_agent_id));return node('div',item.name+(leaders.has(item.id)?' · Leader':''));}
    const b=node('button',item.title||'会话');b.onclick=()=>conversation(item.id).catch(fail);return b;
  }
  function moreNavigation(kind,host,rows,hasMore){
    if(!hasMore||!rows.length)return;
    const last=rows[rows.length-1],button=node('button','读取更多');
    button.onclick=async()=>{button.disabled=true;try{const page=await read({collection:kind,before:JSON.stringify([last.created_at,last.id])});button.remove();for(const item of page.items)host.append(navRow(kind,item));moreNavigation(kind,host,page.items,page.has_more);}catch(error){button.disabled=false;fail(error);}};
    host.append(button);
  }
  $('export').onclick=async()=>{
    $('export').disabled=true;$('status').textContent='正在校验并打包…';
    try{const result=await CrewClient.current.request('/api/workspace-data/export',{workspace_id:workspace});const a=node('a','下载工作区包');a.href=CrewClient.current.resourceURL('/api/workspace-data/download?id='+result.download_id);$('status').replaceChildren(a);a.click();}
    catch(error){fail(error);}finally{$('export').disabled=false;}
  };
  read().then(value=>{
    snapshot=value;$('title').textContent=value.workspace.name;document.title=value.workspace.name+' · Crew 历史';$('identity').textContent='工作区 ID · '+workspace;
    for(const kind of ['agents','conversations']){const host=$(kind);for(const item of value[kind])host.append(navRow(kind,item));moreNavigation(kind,host,value[kind],value.has_more[kind]);}
    for(const [kind,title] of [['goals','任务'],['reports','报告'],['artifacts','资料与文件']]){const b=node('button',title+' · '+(value.counts[kind]??value[kind].length));b.onclick=()=>list(kind).catch(fail);$('sections').append(b);}
    list('reports').catch(fail);
  }).catch(fail);
})();
