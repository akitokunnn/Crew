"""Short-lived desktop pipe bridge; the administrator owns its own lifetime.

This process owns no HOME lock or business state. EOF releases only this bridge;
an explicit shutdown is acknowledged only after the real administrator exits.
"""
import queue
import subprocess
import sys
import threading


def _binary(stream):
    return getattr(stream, 'buffer', stream)


def _relay(source, target):
    try:
        while True:
            block = source.read(65536)
            if not block:
                return
            target.write(block)
            target.flush()
    except (OSError, ValueError):
        # A disconnected frontend does not own the administrator's shutdown.
        return


def run(command, *, stdin=None, stdout=None, stderr=None, process_factory=subprocess.Popen):
    """Return the bridge exit code; the entrypoint uses os._exit for daemon I/O."""
    incoming = _binary(sys.stdin if stdin is None else stdin)
    outgoing = _binary(sys.stdout if stdout is None else stdout)
    errors = _binary(sys.stderr if stderr is None else stderr)
    options = {'stdin': subprocess.PIPE, 'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE,
               'bufsize': 0, 'close_fds': True}
    if sys.platform == 'win32':
        options['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options['start_new_session'] = True
    try:
        child = process_factory(command, **options)
    except OSError as error:
        errors.write(('Crew frontend bridge could not start administrator: ' + str(error) + '\n').encode('utf-8'))
        errors.flush()
        return 1
    relays = []
    for source, target in ((child.stdout, outgoing), (child.stderr, errors)):
        thread = threading.Thread(target=_relay, args=(source, target), daemon=True, name='crew-frontend-output')
        thread.start()
        relays.append(thread)
    inputs = queue.SimpleQueue()

    def read_inputs():
        try:
            while True:
                line = incoming.readline()
                if not line:
                    break
                inputs.put(line)
        except (OSError, ValueError):
            pass
        finally:
            inputs.put(None)

    threading.Thread(target=read_inputs, daemon=True, name='crew-frontend-input').start()
    shutdown = False
    while True:
        status = child.poll()
        if status is not None:
            # Preserve startup failures (including ownership exit 75) and all
            # output already produced by the exited administrator.
            for thread in relays:
                thread.join(1)
            return status
        try:
            line = inputs.get(timeout=.02)
        except queue.Empty:
            continue
        if line is None:
            try:
                child.stdin.close()
            except (OSError, ValueError):
                pass
            if not shutdown:
                return 0
            # stdin EOF following "shutdown" must not acknowledge the request
            # early. The real owner still has to stop its services and exit.
            continue
        if line.strip() == b'shutdown':
            shutdown = True
        try:
            child.stdin.write(line)
            child.stdin.flush()
        except (OSError, ValueError):
            # An owner may exit between poll and write; keep its actual status.
            # A shutdown never turns a broken pipe into false stop success.
            continue
