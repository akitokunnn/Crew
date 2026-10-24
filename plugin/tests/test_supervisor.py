"""Real isolated supervisor/backend processes; no models or production HOME."""
import tests  # Install the production-service connection guard.
import contextlib
import io
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from crew import supervisor
from crew_desktop import HomeServiceLock


PLUGIN = Path(__file__).resolve().parents[1]

FAKE_BACKEND = r'''
import argparse,json,os,sys,threading,time
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from crew_desktop import HomeServiceLock
parser=argparse.ArgumentParser();parser.add_argument('--home');parser.add_argument('--port',type=int);parser.add_argument('--isolated-sources',action='store_true');args=parser.parse_args()
home=Path(args.home).resolve();lock=HomeServiceLock(home)
if not lock.acquire():raise SystemExit(75)
def mode():
    try:return json.loads((home/'mode.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        setting=mode()
        if setting.get('health')=='hang':time.sleep(.6)
        if setting.get('health')=='redirect':
            self.send_response(302);self.send_header('Location','https://example.invalid/private');self.end_headers();return
        data={'ok':True,'service':setting.get('service','crew'),'home':setting.get('home',str(home)),
              'pid':setting.get('pid',os.getpid()),'workers':{'agents':True,'notices':True,'git_ci':True},
              'warnings':['agents'] if setting.get('health')=='warning' else []}
        body=json.dumps(data).encode();self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers()
        try:self.wfile.write(body)
        except OSError:pass
server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler);server.daemon_threads=True
url='http://127.0.0.1:'+str(server.server_port)
(home/'web.json').write_text(json.dumps({'url':url}),encoding='utf-8')
with (home/'launches.jsonl').open('a',encoding='utf-8') as stream:stream.write(json.dumps({'pid':os.getpid(),'port':server.server_port,'requested_port':args.port})+'\n'.replace('\\n','\n'))
started=time.monotonic()
def watch():
    while True:
        if (home/'crash.flag').exists():
            (home/'crash.flag').unlink();os._exit(17)
        after=mode().get('crash_after')
        if after is not None and time.monotonic()-started>after:os._exit(18)
        time.sleep(.02)
def control():
    command='eof'
    for line in sys.stdin:
        if line.strip()=='shutdown':command='shutdown';break
    time.sleep(mode().get('shutdown_delay',0))
    (home/('stopped-'+str(os.getpid()))).write_text(command,encoding='utf-8')
    server.shutdown()
threading.Thread(target=watch,daemon=True).start();threading.Thread(target=control,daemon=True).start()
print(url,flush=True)
try:server.serve_forever(poll_interval=.03)
finally:server.server_close();lock.release()
'''

SUPERVISOR_CHILD = r'''
import sys,time,threading
from pathlib import Path
from crew.supervisor import Supervisor
sys.stdout.reconfigure(encoding='utf-8');sys.stderr.reconfigure(encoding='utf-8')
home=Path(sys.argv[1]);fake=Path(sys.argv[2])
delay=float(sys.argv[3]) if len(sys.argv)>3 else .05
admin=Supervisor(home,isolated_sources=True,command_factory=lambda port:[sys.executable,str(fake)],
                 check_interval=.08,health_timeout=.12,backoffs=(delay,max(delay,.08),max(delay,.1),max(delay,.12),max(delay,.15)),
                 restart_limit=3,restart_window=30,startup_timeout=3)
def stop():
    while not (home/'test-explicit-stop').exists():time.sleep(.02)
    admin.shutdown()
threading.Thread(target=stop,daemon=True).start()
raise SystemExit(admin.run())
'''


def wait_for(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.025)
    raise AssertionError("Timed out waiting for isolated supervisor")


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="crew-supervisor-", dir=Path(__file__).resolve().parent)
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.fake = self.base / "fake_backend.py"
        self.fake.write_text(FAKE_BACKEND, encoding="utf-8")
        self.children = []
        self.addCleanup(self.cleanup_children)

    def launch(self, *, backend=False, real=False, recovery_delay=None):
        if backend:
            command = [sys.executable, str(self.fake), "--home", str(self.home), "--port", "0", "--isolated-sources"]
        elif real:
            command = [sys.executable, str(PLUGIN / "crew_supervisor.py"), "--home", str(self.home), "--port", "0", "--isolated-sources"]
        else:
            command = [sys.executable, "-c", SUPERVISOR_CHILD, str(self.home), str(self.fake)]
            if recovery_delay is not None:
                command.append(str(recovery_delay))
        errors = (self.base / ("stderr-" + str(len(self.children)) + ".log")).open("w+", encoding="utf-8")
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        environment = {**os.environ, "PYTHONPATH": str(PLUGIN)}
        process = subprocess.Popen(command, cwd=PLUGIN, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=errors, text=True, encoding="utf-8", env=environment, **options)
        lines = []
        # Append each line immediately rather than waiting for stdout EOF.
        def read_live():
            try:
                for line in process.stdout:
                    lines.append(line.strip())
            finally:
                process.stdout.close()
        threading.Thread(target=read_live, daemon=True).start()
        self.children.append((process, errors, lines, backend))
        return process, lines

    def cleanup_children(self):
        # The EOF test deliberately detaches stdin. Its test shim invokes the
        # same explicit Supervisor.shutdown method; no public shutdown action
        # is added to the administrator's control-file API.
        (self.home / "test-explicit-stop").touch()
        for process, errors, lines, backend in reversed(self.children):
            if process.poll() is None and process.stdin is not None and not process.stdin.closed:
                with contextlib.suppress(OSError):
                    process.stdin.write("shutdown\n")
                    process.stdin.flush()
            try:
                process.wait(timeout=15)
            finally:
                if process.stdin is not None:
                    with contextlib.suppress(OSError):
                        process.stdin.close()
                errors.close()

    def status(self, predicate=lambda value: True):
        def check():
            value = supervisor.read_status(self.home)
            return value if value and predicate(value) else None
        try:
            return wait_for(check)
        except AssertionError as exc:
            raise AssertionError("Supervisor did not reach expected state: " + repr(supervisor.read_status(self.home))) from exc

    def mode(self, **value):
        supervisor._atomic_json(self.home / "mode.json", value)

    def launches(self):
        path = self.home / "launches.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def control(self, action, *, request_id=None, instance_id=None):
        status = self.status()
        request = {"instance_id": instance_id or status["instance_id"],
                   "request_id": request_id or str(time.monotonic_ns()), "action": action}
        supervisor._atomic_json(self.home / "desktop" / "admin" / "control.json", request)
        return request

    def test_start_shutdown_waits_for_real_exit_and_announces_only_one_url(self):
        self.mode(shutdown_delay=.3)
        process, lines = self.launch()
        status = self.status(lambda value: value["state"] == "healthy")
        wait_for(lambda: len(lines) == 1)
        self.assertEqual(lines, [status["url"]])
        self.assertEqual(status["ownership"], "owned")
        self.assertFalse(status["restart"]["allowed"])
        started = time.monotonic()
        process.stdin.write("shutdown\n");process.stdin.flush()
        self.assertEqual(process.wait(timeout=10), 0)
        self.assertGreaterEqual(time.monotonic() - started, .25)
        stopped = self.status(lambda value: value["state"] == "stopped")
        self.assertFalse(stopped["restart"]["allowed"])
        self.assertEqual((self.home / ("stopped-" + str(status["service_pid"]))).read_text(), "shutdown")
        self.assertFalse(supervisor._pid_alive(status["service_pid"]))
        self.assertEqual(len(lines), 1)

    def test_crashed_backend_recovers_same_port_and_desktop_eof_does_not_stop_it(self):
        process, lines = self.launch()
        first = self.status(lambda value: value["state"] == "healthy")
        process.stdin.close()
        (self.home / "crash.flag").touch()
        recovered = self.status(lambda value: value["state"] == "healthy" and value["recoveries"] == 1)
        self.assertEqual(process.poll(), None)
        self.assertNotEqual(first["service_pid"], recovered["service_pid"])
        self.assertEqual(first["url"], recovered["url"])
        self.assertEqual([item["requested_port"] for item in self.launches()], [0, supervisor._url(first["url"])[1]])
        self.assertIsNotNone(recovered["last_recovery_at"])
        self.assertEqual(len(lines), 1)
        self.assertTrue(any("继续后台监测" in item["message"] for item in recovered["incidents"]))

    def test_live_hung_backend_is_degraded_and_recover_cannot_kill_it(self):
        process, lines = self.launch()
        first = self.status(lambda value: value["state"] == "healthy")
        self.mode(health="hang")
        degraded = self.status(lambda value: value["state"] == "degraded")
        self.assertEqual(degraded["service_pid"], first["service_pid"])
        self.assertFalse(degraded["restart"]["allowed"])
        request = supervisor.request_action(self.home, "recover")
        rejected = self.status(lambda value: value["last_request_id"] == request["request_id"])
        self.assertIsNotNone(rejected["last_request_error"])
        self.assertEqual(len(self.launches()), 1)
        self.assertTrue(supervisor._pid_alive(first["service_pid"]))
        self.mode()
        healthy = self.status(lambda value: value["state"] == "healthy")
        self.assertEqual(healthy["service_pid"], first["service_pid"])

    def test_worker_warning_is_degraded_without_process_replacement(self):
        self.mode(health="warning")
        process, lines = self.launch()
        status = self.status(lambda value: value["state"] == "degraded")
        wait_for(lambda: len(lines) == 1)
        self.assertTrue(supervisor._pid_alive(status["service_pid"]))
        self.assertFalse(status["restart"]["allowed"])
        self.assertEqual(len(self.launches()), 1)

    def test_restart_budget_circuit_and_explicit_recovery(self):
        self.mode(crash_after=.25)
        process, lines = self.launch()
        circuit = self.status(lambda value: value["state"] == "circuit_open")
        self.assertTrue(circuit["restart"]["allowed"])
        self.assertEqual(len(self.launches()), 4)  # Initial + three automatic attempts.
        self.mode()
        request = supervisor.request_action(self.home, "recover")
        recovered = self.status(lambda value: value["state"] == "healthy" and value["last_request_id"] == request["request_id"])
        self.assertIsNone(recovered["last_request_error"])
        self.assertEqual(recovered["url"], circuit["url"])
        self.assertEqual(len(self.launches()), 5)
        self.assertEqual(len(lines), 1)

    def test_healthy_external_backend_is_borrowed_and_not_stopped(self):
        backend, backend_lines = self.launch(backend=True)
        wait_for(lambda: backend_lines)
        process, lines = self.launch()
        status = self.status(lambda value: value["state"] == "healthy")
        self.assertEqual(status["service_pid"], backend.pid)
        self.assertEqual(status["ownership"], "borrowed")
        self.assertEqual(len(self.launches()), 1)
        process.stdin.write("shutdown\n");process.stdin.flush()
        self.assertEqual(process.wait(timeout=10), 0)
        self.assertIsNone(backend.poll())
        self.assertTrue(supervisor._pid_alive(backend.pid))

    def test_incompatible_existing_service_fails_closed(self):
        self.mode(service="some-other-product")
        backend, backend_lines = self.launch(backend=True)
        wait_for(lambda: backend_lines)
        process, lines = self.launch()
        self.assertEqual(process.wait(timeout=10), 76)
        self.assertEqual(lines, [])
        self.assertIsNone(backend.poll())
        self.assertEqual(len(self.launches()), 1)
        self.assertEqual(self.status()["state"], "failed")

    def test_one_os_lock_per_home_and_competitor_does_not_overwrite_status(self):
        process, lines = self.launch()
        first = self.status(lambda value: value["state"] == "healthy")
        competitor, competitor_lines = self.launch()
        self.assertEqual(competitor.wait(timeout=10), 75)
        self.assertEqual(competitor_lines, [])
        self.assertEqual(self.status()["instance_id"], first["instance_id"])
        self.assertEqual(self.status()["service_pid"], first["service_pid"])
        self.assertEqual(len(self.launches()), 1)

    def test_instance_binding_and_request_idempotence(self):
        process, lines = self.launch()
        first = self.status(lambda value: value["state"] == "healthy")
        self.control("recover", instance_id="old-generation", request_id="old-request")
        time.sleep(.25)
        self.assertIsNone(self.status()["last_request_id"])
        request = self.control("check", request_id="same-request")
        self.status(lambda value: value["last_request_id"] == request["request_id"])
        self.control("unknown", request_id="same-request")
        time.sleep(.25)
        status = self.status()
        self.assertEqual(status["last_action"], "check")
        self.assertIsNone(status["last_request_error"])
        self.control("unknown", request_id="new-request")
        rejected = self.status(lambda value: value["last_request_id"] == "new-request")
        self.assertEqual(rejected["last_request_error"], "Unknown administrator action")
        self.assertEqual(len(self.launches()), 1)

    def test_maintenance_marker_prevents_initial_backend_launch(self):
        supervisor._atomic_json(self.home / "desktop" / "admin" / "maintenance.json", {"reason": "test maintenance"})
        process, lines = self.launch()
        self.assertEqual(process.wait(timeout=10), 78)
        status = self.status()
        self.assertEqual(status["state"], "paused")
        self.assertFalse(status["restart"]["allowed"])
        self.assertEqual(lines, [])
        self.assertEqual(self.launches(), [])
        self.assertFalse((self.home / "web.json").exists())

    def test_maintenance_appearing_after_crash_blocks_automatic_and_manual_recovery(self):
        process, lines = self.launch()
        self.status(lambda value: value["state"] == "healthy")
        supervisor._atomic_json(self.home / "desktop" / "admin" / "maintenance.json", {"reason": "repair"})
        self.status(lambda value: value["state"] == "paused")
        (self.home / "crash.flag").touch()
        wait_for(lambda: not supervisor._pid_alive(self.launches()[0]["pid"]))
        request = supervisor.request_action(self.home, "recover")
        status = self.status(lambda value: value["last_request_id"] == request["request_id"])
        self.assertFalse(status["restart"]["allowed"])
        self.assertIsNotNone(status["last_request_error"])
        self.assertEqual(len(self.launches()), 1)

    def test_dead_backend_port_temporarily_occupied_then_check_and_recover(self):
        process, lines = self.launch(recovery_delay=.5)
        first = self.status(lambda value: value["state"] == "healthy")
        (self.home / "crash.flag").touch()
        wait_for(lambda: not supervisor._pid_alive(first["service_pid"]))
        listener = socket.socket()
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", supervisor._url(first["url"])[1]))
        listener.listen()
        try:
            blocked = self.status(lambda value: value["state"] == "degraded" and not value["restart"]["allowed"])
            self.assertEqual(blocked["service_pid"], first["service_pid"])
            self.assertEqual(len(self.launches()), 1)
        finally:
            listener.close()
        check = supervisor.request_action(self.home, "check")
        self.status(lambda value: value["last_request_id"] == check["request_id"] and value["restart"]["allowed"])
        recover = supervisor.request_action(self.home, "recover")
        final = self.status(lambda value: value["state"] == "healthy" and value["last_request_id"] == recover["request_id"])
        self.assertEqual(final["url"], first["url"])
        self.assertEqual(final["recoveries"], 1)

    def test_probe_and_bootstrap_verified_dead_previous_service_keep_its_port(self):
        backend, lines = self.launch(backend=True)
        wait_for(lambda: lines)
        old_url = lines[0]
        backend.stdin.write("shutdown\n");backend.stdin.flush()
        self.assertEqual(backend.wait(timeout=10), 0)
        supervisor._atomic_json(self.home / "desktop" / "admin" / "status.json",
                                {"instance_id": "old-instance", "state": "stopped", "supervisor_pid": backend.pid,
                                 "service_pid": backend.pid, "url": old_url})
        probe = supervisor.probe_home(self.home, timeout=.12)
        self.assertEqual(probe["kind"], "dead")
        self.assertTrue(probe["can_start"])
        process, supervisor_lines = self.launch()
        status = self.status(lambda value: value["state"] == "healthy")
        self.assertEqual(status["url"], old_url)
        self.assertNotEqual(status["service_pid"], backend.pid)

    def test_probe_defers_to_live_supervisor(self):
        process, lines = self.launch()
        self.status(lambda value: value["state"] == "healthy")
        probe = supervisor.probe_home(self.home, timeout=.12)
        self.assertFalse(probe["can_start"])
        self.assertEqual(probe["supervisor_pid"], process.pid)

    def test_real_desktop_backend_isolated_sources_health_and_shutdown(self):
        process, lines = self.launch(real=True)
        status = self.status(lambda value: value["state"] == "healthy")
        self.assertNotEqual(status["service_pid"], process.pid)
        self.assertTrue((self.home / "crew.db").exists())
        wait_for(lambda: lines)
        self.assertEqual(lines, [status["url"]])
        process.stdin.write("shutdown\n");process.stdin.flush()
        self.assertEqual(process.wait(timeout=15), 0)
        self.assertFalse(supervisor._pid_alive(status["service_pid"]))
        self.assertEqual(self.status()["state"], "stopped")


class SupervisorUtilityTests(unittest.TestCase):
    def test_loader_does_not_create_home_and_controls_have_fixed_allowlist(self):
        with tempfile.TemporaryDirectory(prefix="crew-admin-loader-") as directory:
            home = Path(directory) / "absent"
            self.assertIsNone(supervisor.read_status(home))
            self.assertFalse(home.exists())
            for action in ("shutdown", "shell", "", None):
                with self.subTest(action=action), self.assertRaises(supervisor.SupervisorError):
                    supervisor.request_action(home, action)
            self.assertFalse(home.exists())

    def test_log_and_incident_history_are_bounded(self):
        with tempfile.TemporaryDirectory(prefix="crew-admin-log-") as directory:
            admin = supervisor.Supervisor(directory)
            for index in range(supervisor.MAX_EVENTS + 30):
                admin._event("warning", str(index) + "z" * 2000)
            admin._publish()
            status = supervisor.read_status(directory)
            self.assertEqual(len(status["incidents"]), supervisor.MAX_INCIDENTS)
            path = Path(directory) / "desktop" / "admin" / "events.jsonl"
            self.assertLessEqual(path.stat().st_size, supervisor.MAX_EVENTS_BYTES)
            self.assertLessEqual(len(path.read_text().splitlines()), supervisor.MAX_EVENTS)
            reloaded = supervisor.Supervisor(directory)
            self.assertNotEqual(reloaded.instance_id, admin.instance_id)
            self.assertEqual(list(reloaded.incidents), list(admin.incidents))
            self.assertEqual(reloaded._requests, set())
            self.assertIsNone(reloaded.last_request_id)

    def test_url_is_local_root_without_redirect_credentials_or_project_context(self):
        self.assertEqual(supervisor._url("http://127.0.0.1:1234/"), ("http://127.0.0.1:1234", 1234))
        for value in ("https://127.0.0.1:1234", "http://example.com:1234", "http://user@127.0.0.1:1234",
                      "http://127.0.0.1:1234/?project=else", "http://127.0.0.1:1234/path", "file:///private"):
            with self.subTest(value=value), self.assertRaises(supervisor.SupervisorError):
                supervisor._url(value)

    def test_live_backend_never_runs_port_occupancy_probe(self):
        with tempfile.TemporaryDirectory(prefix="crew-admin-port-") as directory:
            admin = supervisor.Supervisor(directory)
            admin.process = mock.Mock()
            admin.process.poll.return_value = None
            with mock.patch.object(supervisor.socket, "socket", side_effect=AssertionError("No bind for a live backend")):
                self.assertFalse(admin._port_free("http://127.0.0.1:12345"))

    def test_broken_parent_output_pipes_do_not_raise_or_stop_administrator(self):
        for error in (False, True):
            broken = mock.Mock()
            broken.write.side_effect = BrokenPipeError("desktop exited")
            key = "stderr" if error else "stdout"
            with mock.patch.object(sys, key, broken):
                supervisor._print("disconnected", error=error)
                replacement = getattr(sys, key)
            replacement.close()


if __name__ == "__main__":
    unittest.main()
