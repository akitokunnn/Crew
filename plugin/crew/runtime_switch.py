"""Confirmed runtime changes; Crew identity and each native history stay separate.

A successful switch configures an offline runtime. It never starts a model turn.
The handoff is consumed only after the chosen native runtime accepts a message.
"""
import datetime as dt
import hashlib
import json
import secrets
from contextlib import contextmanager

from . import board, goals, models, runtimes, store

MAX_HANDOFF = 16000
PREVIEW_SECONDS = 300


def scoped_executions(conn, session_id):
    """All generations are checked for a leftover writer, even after a round ended."""
    return board._rows(conn, 'SELECT e.*,g.status goal_status,g.current_round,g.requirement_version goal_requirement_version,'
                       'g.version goal_version,g.title goal_title FROM goal_executions e JOIN goals g ON g.id=e.goal_id '
                       'WHERE e.session_id=? ORDER BY e.goal_id,e.round,e.generation', (session_id,))


def scope_snapshot(conn, session_id, *, include_status=False):
    fields = ('id', 'goal_id', 'round', 'generation', 'runtime', 'model', 'effort', 'requirement_version',
              'goal_status', 'current_round', 'goal_requirement_version', 'goal_version')
    return {'assignments':board._rows(conn, 'SELECT a.goal_id,a.status,g.status goal_status,g.version goal_version '
                                    'FROM goal_assignments a JOIN goals g ON g.id=a.goal_id WHERE a.session_id=? ORDER BY a.goal_id', (session_id,)),
            'executions':[{**{field:item[field] for field in fields},
                           **({'status':item['status']} if include_status else {}),
                           'bindings':board._rows(conn, 'SELECT runtime,native_id,model,effort FROM goal_execution_bindings '
                                                 'WHERE execution_id=? ORDER BY runtime', (item['id'],))}
                          for item in scoped_executions(conn, session_id)]}


def goal_reason(conn, session_id, manager):
    active = conn.execute("SELECT g.title FROM goals g WHERE g.status IN ('running','pausing') AND "
                          "(g.leader_session_id=? OR EXISTS(SELECT 1 FROM goal_assignments a WHERE a.goal_id=g.id AND a.session_id=? AND a.status='assigned') "
                          "OR EXISTS(SELECT 1 FROM goal_executions e WHERE e.goal_id=g.id AND e.session_id=? AND e.status NOT IN ('completed','failed','cancelled'))) LIMIT 1",
                          (session_id, session_id, session_id)).fetchone()
    if active:
        return '请先暂停该人员仍在推进的全部目标（包括「%s」），再更换执行工具' % active['title']
    for execution in scoped_executions(conn, session_id):
        key = execution['id']
        if manager.busy(key) or execution['status'] in ('running','pausing'):
            return '目标「%s」的执行仍在工作或暂停中，请等停止确认' % execution['goal_title']
        current = manager.processes.get(key)
        if current is not None and getattr(current, 'pending_compact', False):
            return '目标执行还有排队压缩，请等它完成'
    return None


def configurable_executions(conn, session_id):
    candidates = {}
    for item in scoped_executions(conn, session_id):
        if (item['goal_status'] in goals.TERMINAL or item['round'] != item['current_round']
                or item['requirement_version'] != item['goal_requirement_version']):
            continue
        previous = candidates.get(item['goal_id'])
        if previous is None or previous['generation'] < item['generation']:
            candidates[item['goal_id']] = item
    return [item for item in candidates.values() if item['status'] not in goals.TERMINAL]


def writer_scope(conn, session_id):
    keys = [session_id] + [item['id'] for item in scoped_executions(conn, session_id)]
    native_ids = {session_id}
    native_ids.update(row['native_id'] for row in conn.execute('SELECT b.native_id FROM goal_execution_bindings b '
                      'JOIN goal_executions e ON e.id=b.execution_id WHERE e.session_id=?', (session_id,)) if row['native_id'])
    native_ids.update(row['native_id'] for row in conn.execute('SELECT native_id FROM session_runtime_bindings WHERE session_id=?', (session_id,)) if row['native_id'])
    return keys, native_ids


@contextmanager
def atomic(conn):
    if conn.in_transaction:
        yield
    else:
        with board.transaction(conn):
            yield


def binding(conn, session_id, runtime):
    runtimes.validate(runtime)
    row=conn.execute('SELECT native_id,model,effort,updated_at FROM session_runtime_bindings WHERE session_id=? AND runtime=?',(session_id,runtime)).fetchone()
    if row is not None:return dict(row)
    current=runtimes.get(conn,session_id)
    if current['runtime']==runtime:
        return {'native_id':current['native_id'],**models.get(conn,session_id),'updated_at':None}
    return {'native_id':None,'model':None,'effort':None,'updated_at':None}


def remember(conn,session_id,runtime,native_id,options,now=None):
    conn.execute('INSERT INTO session_runtime_bindings(session_id,runtime,native_id,model,effort,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(session_id,runtime) DO UPDATE SET native_id=COALESCE(excluded.native_id,session_runtime_bindings.native_id),model=excluded.model,effort=excluded.effort,updated_at=excluded.updated_at',
        (session_id,runtime,native_id,options.get('model'),options.get('effort'),board._time(now)))


def pending(conn,session_id):
    row=conn.execute("SELECT * FROM runtime_switches WHERE session_id=? AND status='configured' AND consumed_at IS NULL AND superseded_at IS NULL ORDER BY configured_at DESC,rowid DESC LIMIT 1",(session_id,)).fetchone()
    return dict(row) if row is not None else None


def state(conn,session,manager,agents=None,agents_error=None):
    reason=basic_reason(conn,session,manager)
    if reason is None and agents_error:
        reason='进程状态未知，请稍后再试'
    if reason is None and agents is not None:
        keys,native_ids = writer_scope(conn,session['session_id'])
        if any(agent.get('sessionId') in native_ids and not any(manager.owns_pid(key,agent.get('pid')) for key in keys) for agent in agents):
            reason='会话在外部工具中在线，请先自行关闭'
    row=conn.execute("SELECT configured_at FROM runtime_switches WHERE session_id=? AND status='configured' ORDER BY configured_at DESC,rowid DESC LIMIT 1",(session['session_id'],)).fetchone()
    return {'allowed':reason is None,'reason':reason,'pending_handoff':pending(conn,session['session_id']) is not None,
            'last_switch_at':row[0] if row else None}


def basic_reason(conn,session,manager):
    sid=session['session_id']
    if session['role'] not in ('member','leader'):return '这个会话角色不能更换载体'
    if not session.get('crew_name') or session['status']=='closed' or board.is_archived(conn,sid):return '会话已归档、移除或尚未收编'
    if manager.closed:return 'Crew 正在关闭'
    reason = goal_reason(conn, sid, manager)
    if reason:return reason
    if manager.busy(sid) or (session['status']=='working' and not manager.running(sid)):
        return '会话正在工作，请等这一轮结束'
    current=manager.processes.get(sid)
    if current is not None and getattr(current,'pending_compact',False):return '会话还有排队压缩，请等它完成'
    return None


def check_idle(conn,session,manager):
    reason=basic_reason(conn,session,manager)
    if reason:raise board.Conflict(reason)
    # Check Claude even when the active runtime is Codex: a previously opened
    # Claude transcript must not become a second writer for this Crew identity.
    keys,native_ids = writer_scope(conn,session['session_id'])
    for agent in manager._external_agents():
        if agent.get('sessionId') not in native_ids:continue
        if any(manager.owns_pid(key,agent.get('pid')) for key in keys):continue
        raise board.Conflict('这个会话在 VS Code、终端或其他服务中仍在线，请先自行关闭；Crew 不会关闭外部会话')
    from .codex_runtime import RuntimeLease
    for key in keys:
        current=manager.processes.get(key)
        if not manager.running(key) and current is not None and not current.reader_done.is_set():
            raise board.Conflict('上一轮进程输出还没有排空，请稍后再试')
        if not manager.running(key) or getattr(current,'runtime',None)!='codex':
            lease=RuntimeLease(manager.home,key).acquire()
            lease.release()


def _texts(events):
    chunks=[]
    for event in events:
        if event.get('type')=='message':
            text=event.get('text')
            if isinstance(text,str):chunks.append('用户/协作输入：'+text[-MAX_HANDOFF:])
        elif event.get('type')=='assistant':
            content=event.get('content',[])
            if isinstance(content,list):
                text='\n'.join(block['text'][-MAX_HANDOFF:] for block in content[-12:] if isinstance(block,dict) and block.get('type')=='text' and isinstance(block.get('text'),str))[-MAX_HANDOFF:]
                if text:chunks.append('上一个载体的回复（仅为历史，需自行核验）：'+text)
        if len(chunks)>=12:break
    return chunks


def handoff(conn,session,journal):
    card=conn.execute('SELECT scope,done_when FROM session_cards WHERE session_id=?',(session['session_id'],)).fetchone()
    lines=['Crew 载体交接。Crew 身份、所属组、任务卡与文件未改变；两种工具的原生历史没有互相转换。',
           '以下是白板事实和最近聊天片段，并非已经验证的成果；不要把历史中的完成声明当作验收证据。',
           '成员：'+session['crew_name'],'项目：'+str(session['project']),'所属组：'+str(session['group_name'] or '未分组'),
           '当前任务：'+str(session.get('task') or '未填写'),'工作目录：'+str(session.get('cwd') or ''),'分支：'+str(session.get('branch') or '')]
    if card:lines+=['职责范围：'+str(card['scope'] or '未填写'),'完成标准：'+str(card['done_when'] or '未填写')]
    for row in conn.execute("SELECT title,verification FROM todos WHERE session_id=? AND status='open' ORDER BY id DESC LIMIT 5",(session['session_id'],)):
        lines.append('未完待办：'+row['title']+'；验证方式：'+row['verification'])
    for row in conn.execute("SELECT question FROM decisions WHERE session_id=? AND status='open' ORDER BY id DESC LIMIT 5",(session['session_id'],)):
        lines.append('未决问题：'+row['question'])
    for row in conn.execute('SELECT title,path,summary FROM documents WHERE session_id=? ORDER BY COALESCE(user_important,ai_important) DESC,updated_at DESC LIMIT 5',(session['session_id'],)):
        lines.append('已登记文档：'+row['title']+' '+row['path']+'；提交者摘要：'+str(row['summary'] or '未填写'))
    base='\n'.join(lines)
    with journal.condition:recent='\n\n'.join(reversed(_texts(reversed(journal.events))))
    available=max(0,MAX_HANDOFF-len(base)-20)
    truncated=len(recent)>available or len(base)>MAX_HANDOFF
    recent=recent[-available:] if available else ''
    text=(base[:MAX_HANDOFF]+'\n\n最近聊天片段：\n'+recent)[:MAX_HANDOFF]
    return {'text':text,'chars':len(text),'truncated':truncated}


def revision(conn,session,manager):
    sid=session['session_id'];journal=manager.journal(sid)
    with journal.condition:last_seq=journal.events[-1]['seq'] if journal.events else 0
    control=board.session_control(conn,sid)
    card=conn.execute('SELECT * FROM session_cards WHERE session_id=?',(sid,)).fetchone()
    current=manager.processes.get(sid)
    snapshot={'session':{key:session.get(key) for key in ('session_id','project','crew_name','role','group_name','status','task','cwd','worktree','branch','spawn_id')},
        'runtime':runtimes.get(conn,sid),'options':models.get(conn,sid),'bindings':[dict(row) for row in conn.execute('SELECT * FROM session_runtime_bindings WHERE session_id=? ORDER BY runtime',(sid,))],
        'control':control,'card':dict(card) if card else None,'last_seq':last_seq,
        'owned_pid':current.process.pid if current is not None and manager.running(sid) else None,
        'goal_scope':scope_snapshot(conn,sid,include_status=True)}
    return hashlib.sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def configuration(conn,session_id):
    """Recheck durable inputs after draining an idle runner (which emits status)."""
    session=board.session(conn,session_id)
    card=conn.execute('SELECT * FROM session_cards WHERE session_id=?',(session_id,)).fetchone()
    return {'session':{key:session.get(key) for key in ('session_id','project','crew_name','role','group_name','task','cwd','worktree','branch','spawn_id')},
            'runtime':runtimes.get(conn,session_id),'options':models.get(conn,session_id),
            'bindings':[dict(row) for row in conn.execute('SELECT * FROM session_runtime_bindings WHERE session_id=? ORDER BY runtime',(session_id,))],
            'control':board.session_control(conn,session_id),'card':dict(card) if card else None,
            'goal_scope':scope_snapshot(conn,session_id)}


def validate_target(runtime,model,effort,catalog):
    runtimes.validate(runtime)
    entry=next((item for item in catalog if item['id']==runtime),None)
    if entry is None or not entry.get('available'):raise board.Conflict((entry or {}).get('error') or '目标载体不可用')
    choices=entry.get('models') if runtime=='codex' else None
    if runtime=='codex' and effort is not None and model is None:
        raise board.BoardError('指定 Codex 档位时请同时选择目标模型；或将模型和档位都设为默认')
    models.validate_options(model,effort,runtime=runtime,catalog=choices)
    return {'model':model,'effort':effort}


def prepare(conn,session,manager,runtime,catalog,model=None,effort=None,handoff_text=None,now=None):
    check_idle(conn,session,manager)
    source=runtimes.get(conn,session['session_id'])
    if source['runtime']==runtime:raise board.Conflict('会话已经使用这个载体')
    options=validate_target(runtime,model,effort,catalog)
    preview=handoff(conn,session,manager.journal(session['session_id']))
    if handoff_text is not None:
        if not isinstance(handoff_text,str) or not handoff_text.strip() or len(handoff_text)>MAX_HANDOFF:raise board.BoardError('交接上下文必须是 1 到 %s 字符的文本'%MAX_HANDOFF)
        preview={'text':handoff_text,'chars':len(handoff_text),'truncated':False}
    stamp=board._time(now);expires=(store.parse_iso(stamp)+dt.timedelta(seconds=PREVIEW_SECONDS)).isoformat(timespec='milliseconds').replace('+00:00','Z')
    identifier=secrets.token_urlsafe(32);digest=revision(conn,session,manager);target=binding(conn,session['session_id'],runtime)
    result={'allowed':True,'preview_id':identifier,'expires_at':expires,'revision':digest,
        'from':{**source,**models.get(conn,session['session_id'])},'to':{'runtime':runtime,**options,'resume_existing':target['native_id'] is not None},
        'handoff':preview,'scope':{'kind':'person','session_id':session['session_id'],
            'executions':[{'id':item['id'],'goal_id':item['goal_id'],'title':item['goal_title'],'status':item['goal_status']} for item in configurable_executions(conn,session['session_id'])],
            'notice':'更换此人员的默认执行工具及其已暂停/尚未启动的当前目标执行；其他人员与历史代次保持原配置'},
        'cost':{'starts_turn':False,'requires_context_replay':True,'notice':'下次发送或唤醒会重新读取交接和本目标上下文，可能增加用量；各工具原生历史保留，不估算 token 或价格'},'starts_turn':False}
    with atomic(conn):
        conn.execute("INSERT INTO runtime_switches(id,session_id,project,from_runtime,to_runtime,target_model,target_effort,revision,handoff,created_at,expires_at,status,preview_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,'preview',?)",
            (identifier,session['session_id'],session['project'],source['runtime'],runtime,model,effort,digest,preview['text'],stamp,expires,json.dumps(result,ensure_ascii=False)))
    return result


def switch(conn,session,manager,preview_id,confirm,catalog,now=None):
    if confirm is not True or not isinstance(preview_id,str):raise board.BoardError('请先检查交接内容并确认更换')
    row=conn.execute('SELECT * FROM runtime_switches WHERE id=? AND session_id=? AND project=?',(preview_id,session['session_id'],session['project'])).fetchone()
    if row is None:raise board.Conflict('更换预览已失效，请重新检查')
    if row['status']=='configured':return json.loads(row['result_json'])
    stamp=board._time(now)
    if store.parse_iso(stamp)>store.parse_iso(row['expires_at']):raise board.Conflict('更换预览已过期，请重新检查')
    check_idle(conn,session,manager)
    if revision(conn,session,manager)!=row['revision']:raise board.Conflict('会话状态已经变化，请重新检查交接内容')
    options=validate_target(row['to_runtime'],row['target_model'],row['target_effort'],catalog)
    source=runtimes.get(conn,session['session_id']);source_options=models.get(conn,session['session_id']);target=binding(conn,session['session_id'],row['to_runtime'])
    expected=configuration(conn,session['session_id'])
    if source['runtime']!=row['from_runtime']:raise board.Conflict('当前载体已经变化，请重新检查')
    # This method never terminates an external process. Drain the exact owned
    # idle runner before opening a database transaction or publishing a change.
    keys = [session['session_id']] + [item['id'] for item in scoped_executions(conn,session['session_id'])]
    for key in keys:
        if not manager.running(key):continue
        previous=manager.processes[key]
        manager.detach(key)
        if previous.process.poll() is None:
            raise board.Conflict('旧载体进程尚未真正退出，不能更换')
        # A quota/error result can leave an old Claude attendance status at
        # working. The exact owned idle native has now exited; record that
        # observed offline state even if configuring the target later fails.
        if key==session['session_id'] and board.session(conn,session['session_id'])['status']=='working':
            board.set_status(conn,session['session_id'],'offline',now=stamp)
    check_idle(conn,board.session(conn,session['session_id']),manager)
    result={'ok':True,'status':'configured','session_id':session['session_id'],'runtime':row['to_runtime'],**options,
            'pending_handoff':True,'starts_turn':False,'switch_id':preview_id}
    with atomic(conn):
        if configuration(conn,session['session_id'])!=expected:
            raise board.Conflict('会话配置在停止过程中发生变化，请重新检查交接内容')
        remember(conn,session['session_id'],source['runtime'],source['native_id'],source_options,stamp)
        runtimes.set.__wrapped__(conn,session['session_id'],row['to_runtime'],target['native_id'],now=stamp)
        models.set(conn,session['session_id'],**options,runtime=row['to_runtime'],now=stamp)
        for execution in configurable_executions(conn,session['session_id']):
            if execution['runtime'] == row['to_runtime'] and {key:execution[key] for key in ('model','effort')} == options:continue
            goals.configure_execution(conn,execution['id'],row['to_runtime'],**options,now=stamp)
        remember(conn,session['session_id'],row['to_runtime'],target['native_id'],options,stamp)
        conn.execute("UPDATE runtime_switches SET superseded_at=? WHERE session_id=? AND status='configured' AND consumed_at IS NULL AND superseded_at IS NULL",(stamp,session['session_id']))
        conn.execute("UPDATE runtime_switches SET status='configured',configured_at=?,result_json=? WHERE id=?",(stamp,json.dumps(result,ensure_ascii=False),preview_id))
        board._event(conn,'session:runtime-switch',session['project'],board.HUMAN,{'session_id':session['session_id'],'from':source['runtime'],'to':row['to_runtime'],'switch_id':preview_id,'handoff_chars':len(row['handoff']),'starts_turn':False},stamp)
        conn.execute("UPDATE sessions SET status='offline' WHERE session_id=?",(session['session_id'],))
    manager.journal(session['session_id']).emit('runtime_switch',from_runtime=source['runtime'],runtime=row['to_runtime'],switch_id=preview_id,handoff_chars=len(row['handoff']),starts_turn=False)
    return result


def input_text(conn,session_id,text,message_id,now=None):
    row=pending(conn,session_id)
    if row is None:return text,None
    current=runtimes.get(conn,session_id)
    if current['runtime']!=row['to_runtime']:raise board.Conflict('待交接载体与当前配置不一致')
    with atomic(conn):
        conn.execute('UPDATE runtime_switches SET attempted_message_id=?,attempted_at=? WHERE id=? AND consumed_at IS NULL',(message_id,board._time(now),row['id']))
    return '【Crew 受信交接上下文】\n'+row['handoff']+'\n【交接结束】\n\n'+text,row['id']


def accepted(conn,session_id,message_id,switch_id=None,now=None):
    with atomic(conn):
        row=pending(conn,session_id)
        if row is None or row['attempted_message_id']!=message_id or switch_id is not None and row['id']!=switch_id:return False
        if runtimes.get(conn,session_id)['runtime']!=row['to_runtime']:return False
        conn.execute('UPDATE runtime_switches SET consumed_at=?,accepted_message_id=? WHERE id=? AND consumed_at IS NULL',(board._time(now),message_id,row['id']))
        return True


def failed(conn,session_id,error,now=None):
    with atomic(conn):
        row=pending(conn,session_id)
        if row is not None:
            conn.execute('UPDATE runtime_switches SET last_error=? WHERE id=?',(str(error)[:2000],row['id']))
            board._event(conn,'session:runtime-start-failed',row['project'],session_id,{'switch_id':row['id'],'runtime':row['to_runtime'],'error':str(error)[:2000]},board._time(now))
