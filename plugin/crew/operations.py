"""Event-driven operations, owned by the existing HOME supervisor.

No polling thread and no resident model: ``tick`` consumes the local event spool
on the supervisor's existing health cadence and collects one bounded worker.
The spool and snapshots remain available when Crew HTTP is down.
"""
import hashlib
import html
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from .supervisor import _atomic_json, _directory, _now, _read_json, _pid_alive


class OperationsError(Exception):
    pass


def directory(home):
    return _directory(home) / 'operations'


def _digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if len(raw.encode('utf-8')) > 32768:
        raise OperationsError('Incident evidence exceeds 32 KiB')
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def submit_incident(home, kind, scope, evidence, *, goals=(), resolved=False):
    """Write one event without HTTP, model invocation or a second monitor."""
    if not all(isinstance(value, str) and 0 < len(value) <= 200 for value in (kind, scope)):
        raise OperationsError('Incident kind/scope must be nonempty strings (<=200)')
    goals = sorted(set(goals))
    if len(goals) > 200 or any(not isinstance(goal, str) or not 1 <= len(goal) <= 200 for goal in goals):
        raise OperationsError('Invalid incident goals')
    _digest(evidence)
    event = dict(kind=kind, scope=scope, evidence=evidence, goals=goals, resolved=bool(resolved), at=_now())
    event_id = uuid.uuid4().hex
    _atomic_json(directory(home) / 'inbox' / (event_id + '.json'), event)
    return event_id


def read_operations(home):
    try:
        return _read_json(directory(home) / 'state.json', 1024 * 1024)
    except FileNotFoundError:
        return {'incidents': [], 'active_worker': None}


def submit_application(home, incident_id, merged_ref):
    """Trusted project workflow calls this only after its PR/merge rules pass."""
    if not isinstance(incident_id, str) or len(incident_id) != 32 or not isinstance(merged_ref, str):
        raise OperationsError('Invalid merged repair application')
    event_id = uuid.uuid4().hex
    _atomic_json(directory(home) / 'inbox' / (event_id + '.json'),
                 {'type': 'apply', 'incident_id': incident_id, 'merged_ref': merged_ref})
    return event_id


class Operations:
    """One controller used only while Supervisor holds its OS lock.

    Goal callbacks persist a pause token before any repair begins. Resume is a
    compare-and-set against the latest user intent, supplied by the goal store.
    ``repository`` injects an isolated test checkout. Normal jobs use the trusted
    policy.json ``repository`` absolute path, or this module's source checkout.
    Flat portable packages require that explicit Crew Git repository plus trusted
    ``reproduce`` / ``verify`` argv arrays. Configuration is checked only when a
    repair/application job is actually needed, never during healthy startup.
    """
    def __init__(self, home, *, running_goals=None, pause_goal=None, resume_goal=None, repository=None,
                 activate_release=None):
        self.home = Path(home).resolve()
        self.directory = directory(home)
        self._repository_override = Path(repository).resolve() if repository is not None else None
        self.repository = self._repository_override or Path(__file__).resolve().parents[2]
        self.running_goals = running_goals or (lambda: ())
        self.pause_goal, self.resume_goal = pause_goal, resume_goal
        self.activate_release = activate_release
        self.state = read_operations(home)
        self.process = None

    def snapshot(self):
        return {'active_worker': self.state['active_worker'],
                'incidents': [{key: value for key, value in item.items() if key not in ('evidence', 'pause_tokens')}
                              for item in self.state['incidents'][-20:]]}

    def _save(self):
        _atomic_json(self.directory / 'state.json', self.state)

    def _repository_for_job(self, policy):
        if self._repository_override is not None:
            repository = self._repository_override
        elif 'repository' in policy:
            configured = policy['repository']
            if not isinstance(configured, str) or not configured or not Path(configured).is_absolute():
                raise OperationsError('RepositoryConfigurationRequired: policy.repository must be an absolute Crew Git repository path; '
                                      'configure trusted reproduce/verify command arrays in policy.json')
            repository = Path(configured).resolve()
        else:
            repository = Path(__file__).resolve().parents[2]
        # Git validation belongs to the finite worker. Even a slow/unavailable
        # repository cannot block the supervisor's existing health cadence.
        self.repository = repository
        return repository

    def _configuration_error(self, incident, error):
        message = type(error).__name__ + ': ' + str(error)
        if incident['summary'] != message:
            incident['summary'] = message
            self._save()

    def observe_service(self, state, evidence):
        """Consume the existing supervisor probe, never perform another probe."""
        if state in ('starting', 'paused', 'stopping', 'stopped'):
            return
        fingerprint = _digest(['service', 'crew'])
        incident = next((item for item in reversed(self.state['incidents'])
                         if item['fingerprint'] == fingerprint and item['status'] != 'resolved'), None)
        if state == 'healthy':
            if incident:
                self._resolve(incident, evidence)
                self._save()
            return
        self._accept(dict(kind='service', scope='crew', evidence=evidence,
                          goals=list(self.running_goals()), resolved=False, at=_now(),
                          analyze=state not in ('recovering',)))

    def _accept(self, event):
        if event.get('type') == 'apply':
            self.apply_merged(event['incident_id'], merged_ref=event['merged_ref'])
            return
        fingerprint = _digest([event['kind'], event['scope']])
        incident = next((item for item in reversed(self.state['incidents'])
                         if item['fingerprint'] == fingerprint and item['status'] != 'resolved'), None)
        if event.get('resolved'):
            if incident:
                self._resolve(incident, event['evidence'])
            return
        evidence_key = event['evidence']
        if event['kind'] == 'service' and isinstance(evidence_key, dict):
            evidence_key = {key: value for key, value in evidence_key.items() if key != 'service_pid'}
        digest = _digest(evidence_key)
        if (incident is not None and digest == incident['evidence_hash'] and
                set(event['goals']).issubset(incident['goals'])):
            return
        if incident is None:
            seen = any(item['fingerprint'] == fingerprint and digest in item['analyzed_hashes']
                       for item in self.state['incidents'])
            incident = dict(id=uuid.uuid4().hex, fingerprint=fingerprint, kind=event['kind'], scope=event['scope'],
                            status='waiting_evidence' if seen else 'queued', first_at=event['at'], last_at=event['at'], evidence=event['evidence'],
                            evidence_hash=digest, analyzed_hashes=[], attempts=0, goals=[], pause_tokens={},
                            summary=None, report=None, result=None, analyze=event.get('analyze', True))
            self.state['incidents'].append(incident)
        elif digest != incident['evidence_hash']:
            incident.update(last_at=event['at'], evidence=event['evidence'], evidence_hash=digest,
                            analyze=event.get('analyze', True))
            if incident['attempts'] < 2 and digest not in incident['analyzed_hashes']:
                incident['status'] = 'queued'
        for goal_id in event['goals']:
            if goal_id in incident['goals']:
                continue
            if self.pause_goal is None:
                raise OperationsError('Affected goal cannot be paused: goal callbacks unavailable')
            # Save each returned token immediately, before any worker is started.
            token = self.pause_goal(goal_id, incident['id'])
            incident['goals'].append(goal_id)
            if token is not None:
                incident['pause_tokens'][goal_id] = token
            self._save()
        self._save()

    def _resolve(self, incident, evidence):
        incident.update(status='resolved', resolved_at=_now(), recovery=evidence)
        self._resume_ready()
        # A still-running bounded repair may finish, but recovery cannot apply it.
        self._report(incident)

    def _resume_ready(self):
        for owner in self.state['incidents']:
            if owner['status'] != 'resolved':
                continue
            restored = owner.setdefault('restored_goals', [])
            skipped = owner.setdefault('preserved_goals', [])
            pending = []
            for goal_id, token in owner['pause_tokens'].items():
                if goal_id in restored or goal_id in skipped:
                    continue
                if any(item['status'] != 'resolved' and goal_id in item['goals'] for item in self.state['incidents']):
                    pending.append(goal_id)
                    continue
                if self.resume_goal is None:
                    raise OperationsError('Goal recovery callback unavailable')
                resumed = self.resume_goal(goal_id, token)
                if resumed is None:
                    pending.append(goal_id)  # Native interruption acknowledgement is still pending.
                else:
                    (restored if resumed else skipped).append(goal_id)
            owner['pending_goals'] = pending

    def _report(self, incident):
        result = incident.get('result') or {}
        summaries = {'resolved': '已恢复', 'staged': '已合并并验证，等待自有后台退出后切换',
                     'refused': '修复应用被拒绝，保留当前服务', 'rolled_back': '新版本失败，已回到此前健康来源',
                     'validating_release': '正在独立验证已合并版本'}
        summary = summaries.get(incident['status']) or ('修复已提交，等待项目合并' if result.get('status') == 'awaiting_merge'
                                                       else '故障处理未完成')
        error = (incident.get('release') or {}).get('error') or result.get('error')
        incident['summary'] = summary + ('；' + error if error else '')
        report = self.directory / 'incidents' / incident['id'] / 'report.html'
        values = dict(incident=incident['id'], status=incident['status'], goals=incident['goals'],
                      example={'input': {'kind': incident['kind'], 'scope': incident['scope']},
                               'expected': '独立复现从失败变为通过；项目合并并复验后由自有服务应用',
                               'actual_before': result.get('before') or incident['evidence'],
                               'actual_after': result.get('verification') or incident.get('recovery') or '尚未验证',
                               'conclusion': incident['summary']},
                      before=incident['evidence'], after=incident.get('recovery') or result,
                      verification=result.get('verification'), commit=result.get('commit'),
                      attempts=incident['attempts'], error=error, release=incident.get('release'))
        sections = ''.join('<section><h2>' + html.escape(key) + '</h2><pre>' +
                           html.escape(json.dumps(value, ensure_ascii=False, indent=2)) + '</pre></section>'
                           for key, value in values.items())
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                          '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
                          '<title>Crew 事故报告</title><style>body{max-width:900px;margin:40px auto;padding:0 20px;'
                          'font:16px system-ui;color:#18202b}pre{white-space:pre-wrap;background:#f2f4f7;padding:16px;'
                          'overflow-wrap:anywhere}h2{font-size:18px}</style><h1>' + html.escape(incident['summary']) +
                          '</h1><p>实际故障输入 → 隔离复现 → 修复后独立验证；模型输出不作为验收。</p>' + sections + '</html>',
                          encoding='utf-8')
        incident['report'] = str(report)

    def tick(self):
        if any(item.get('pending_goals') for item in self.state['incidents']):
            self._resume_ready()
            self._save()
        inbox = self.directory / 'inbox'
        if inbox.exists():
            for path in sorted(inbox.glob('*.json'))[:100]:
                self._accept(_read_json(path, 65536))
                path.unlink()
        active = self.state['active_worker']
        if active:
            if self.process is not None:
                self.process.poll()
            result_path = Path(active['result'])
            if result_path.exists():
                result = _read_json(result_path, 1024 * 1024)
                incident = next(item for item in self.state['incidents'] if item['id'] == active['incident_id'])
                if active.get('mode') == 'release':
                    incident.pop('pending_merge', None)
                    incident['release'] = result
                    if result['status'] == 'ready_to_activate':
                        try:
                            if self.activate_release is None:
                                raise OperationsError('ActivationUnavailable: supervisor callback is missing')
                            applied = self.activate_release(Path(result['release_path']), incident['id'])
                            incident['release']['status'] = 'staged' if applied else 'refused'
                        except (OSError, OperationsError) as exc:
                            incident['release'].update(status='refused', error=type(exc).__name__ + ': ' + str(exc))
                    incident['status'] = incident['release']['status']
                else:
                    incident['result'] = result
                    if incident['status'] != 'resolved':
                        incident['status'] = ('queued' if incident['evidence_hash'] != active['evidence_hash'] and
                                              incident['attempts'] < 2 else result['status'])
                self._report(incident)
                self.state['active_worker'] = None
                if self.process is not None:
                    self.process.wait(timeout=5)
                self.process = None
                self._save()
            elif _pid_alive(active['pid']) is False:
                incident = next(item for item in self.state['incidents'] if item['id'] == active['incident_id'])
                incident['result'] = {'status': 'failed', 'error': 'WorkerExited: no result; inspect worker.log'}
                if incident['status'] != 'resolved':
                    incident['status'] = 'failed'
                self._report(incident)
                self.state['active_worker'] = None
                self._save()
            return
        policy_path = self.directory / 'policy.json'
        if not policy_path.exists():
            for incident in self.state['incidents']:
                if incident['status'] == 'queued':
                    self._configuration_error(incident, OperationsError(
                        'OperationsConfigurationRequired: configure policy.json repository (absolute Crew Git root) '
                        'and trusted reproduce/verify command arrays before AI repair'))
            return
        policy = _read_json(policy_path, 65536)
        for incident in self.state['incidents']:
            releasing = bool(incident.get('pending_merge'))
            if not releasing and (incident['status'] != 'queued' or not incident['analyze'] or incident['attempts'] >= 2 or
                    incident['evidence_hash'] in incident['analyzed_hashes']):
                continue
            from .operations_worker import _policy
            try:
                _policy(policy)
                repository = self._repository_for_job(policy)
            except (OSError, OperationsError, subprocess.SubprocessError) as exc:
                self._configuration_error(incident, exc)
                raise
            if not releasing:
                incident['attempts'] += 1
                incident['analyzed_hashes'].append(incident['evidence_hash'])
            mode = 'release' if releasing else 'repair'
            name = 'release-' + uuid.uuid4().hex if releasing else str(incident['attempts'])
            job_dir = self.directory / 'incidents' / incident['id'] / name
            job_dir.mkdir(parents=True, exist_ok=True)
            job = dict(home=str(self.home), repository=str(repository), incident=incident,
                       policy=policy, directory=str(job_dir), mode=mode)
            job_path, result_path = job_dir / 'job.json', job_dir / 'result.json'
            _atomic_json(job_path, job)
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            with (job_dir / 'worker.log').open('ab') as log:
                self.process = subprocess.Popen([sys.executable, '-m', 'crew.operations_worker', str(job_path)],
                                                cwd=Path(__file__).resolve().parents[1],
                                                stdin=subprocess.DEVNULL, stdout=log, stderr=log, **options)
            incident['status'] = 'validating_release' if releasing else 'repairing'
            incident['summary'] = None
            self.state['active_worker'] = dict(pid=self.process.pid, incident_id=incident['id'], result=str(result_path),
                                              evidence_hash=incident['evidence_hash'], mode=mode)
            self._save()
            break

    def apply_merged(self, incident_id, *, merged_ref):
        """Validate a project-approved merge in a fresh snapshot before activation.

        The trusted project integration supplies ``merged_ref`` after its PR /
        merge rules pass; neither the model nor its JSON output can approve it.
        Validation runs in the finite worker, preserving the supervisor's health
        cadence. Activation stays in the supervisor and never in the model.
        """
        incident = next(item for item in self.state['incidents'] if item['id'] == incident_id)
        result = incident.get('result') or {}
        if result.get('status') != 'awaiting_merge':
            raise OperationsError('No independently verified repair commit to apply')
        policy = _read_json(self.directory / 'policy.json', 65536)
        from .operations_worker import _policy
        try:
            _policy(policy)
            self._repository_for_job(policy)
        except (OSError, OperationsError, subprocess.SubprocessError) as exc:
            self._configuration_error(incident, exc)
            raise
        if policy.get('merge_ref') != merged_ref:
            raise OperationsError('UnapprovedMergeTarget: project merge ref does not match policy')
        if incident.get('pending_merge') or (incident.get('release') or {}).get('status') in ('staged', 'active'):
            return
        incident['pending_merge'] = merged_ref
        self._save()

    def release_result(self, incident_id, *, healthy, detail):
        incident = next(item for item in self.state['incidents'] if item['id'] == incident_id)
        incident['release'].update(status='active' if healthy else 'rolled_back', detail=detail)
        if healthy:
            self._resolve(incident, detail)
        else:
            incident['status'] = 'rolled_back'
            self._report(incident)
        self._save()


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='Queue project-approved merged Crew repair without HTTP')
    parser.add_argument('--home', required=True, type=Path)
    parser.add_argument('--incident', required=True)
    parser.add_argument('--merged-ref', required=True)
    args = parser.parse_args(argv)
    print(submit_application(args.home, args.incident, args.merged_ref))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
