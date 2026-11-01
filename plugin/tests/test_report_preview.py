"""Report preview queue, bytes and lifecycle with temporary HOME only."""
import base64
from pathlib import Path
import struct
import tempfile
import threading
import unittest
import zlib

import tests
from crew import board, report_documents, report_preview


def png(width, height):
    def chunk(kind, body):
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body) & 0xffffffff)
    image = zlib.compress((b'\0' + b'\x20\x60\x90\xff' * width) * height)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', image) + chunk(b'IEND', b''))


class ReportPreviewTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        self.manager = report_preview.Manager(self.home, timeout=1, poll_seconds=.02)
        self.addCleanup(self.manager.close)

    def begin(self, **options):
        result = []
        thread = threading.Thread(target=lambda: result.append(self.manager.capture(
            '<h1>Isolated synthetic report</h1><script>document.body.dataset.ready="yes"</script>',
            title='Synthetic report', goal_id='goal-one', **options)))
        thread.start()
        self.addCleanup(lambda: thread.join(2))
        return thread, result

    def credentials(self):
        value = self.manager.register({})
        return {key: value[key] for key in ('worker_id', 'token')}

    def complete(self, worker, job, **options):
        return self.manager.result({**worker, 'job_id': job['id'], 'png_base64': base64.b64encode(
            png(job['width'], job['height'])).decode('ascii'), 'console_errors': [], **options})

    def test_no_desktop_is_an_explicit_failure_without_creating_artifacts(self):
        result = self.manager.capture('<h1>Synthetic</h1>', title='Test', goal_id='one')
        self.assertFalse(result['ok'])
        self.assertIn('打开 Crew 桌面', result['error'])
        self.assertEqual(list(self.home.iterdir()), [])

    def test_shared_policy_and_exact_png_are_returned_without_claiming_visual_success(self):
        worker = self.credentials()
        thread, result = self.begin(viewport='mobile')
        job = self.manager.next(worker)['job']
        self.assertEqual((job['width'], job['height']), (390, 844))
        self.assertIn(report_documents.VIEWER_POLICY.replace("'", '&#x27;'), job['html'])
        self.assertIn('sandbox="allow-scripts"', job['html'])
        self.assertIn('srcdoc=', job['html'])
        self.complete(worker, job, console_errors=['Synthetic console failure retained'])
        thread.join(2)
        self.assertFalse(thread.is_alive())
        preview = result[0]['preview']
        self.assertEqual(Path(preview['path']).parent, self.home / 'report-previews')
        self.assertEqual(Path(preview['path']).read_bytes(), png(390, 844))
        self.assertEqual(preview['console_errors'], ['Synthetic console failure retained'])
        self.assertEqual(preview['status'], 'captured')
        self.assertEqual(preview['visual_verification'], 'not_performed')
        with self.assertRaises(board.Conflict):
            self.complete(worker, job)
        self.assertEqual(len(list((self.home / 'report-previews').iterdir())), 1)

    def test_worker_authentication_and_viewport_png_validation_do_not_settle_the_job(self):
        worker = self.credentials()
        thread, result = self.begin()
        with self.assertRaises(board.Conflict):
            self.manager.next({**worker, 'token': 'different-worker'})
        job = self.manager.next(worker)['job']
        for identity in ({}, [], '', None):
            with self.subTest(identity=identity), self.assertRaises(board.BoardError):
                self.manager.result({**worker, 'job_id': identity})
        for encoded in ('not base64', base64.b64encode(b'not PNG').decode('ascii'),
                        base64.b64encode(png(1, 1)).decode('ascii')):
            with self.subTest(encoded=encoded[:20]), self.assertRaises(board.BoardError):
                self.manager.result({**worker, 'job_id': job['id'], 'png_base64': encoded})
        self.assertEqual(result, [])
        self.complete(worker, job)
        thread.join(2)
        self.assertTrue(result[0]['ok'])

    def test_worker_failure_and_close_keep_real_reasons_and_write_no_png(self):
        worker = self.credentials()
        thread, result = self.begin()
        job = self.manager.next(worker)['job']
        self.manager.result({**worker, 'job_id': job['id'], 'error': 'Synthetic renderer process exited'})
        thread.join(2)
        self.assertFalse(result[0]['ok'])
        self.assertIn('Synthetic renderer process exited', result[0]['error'])
        thread, result = self.begin()
        self.manager.next(worker)
        self.manager.close_worker(worker)
        thread.join(2)
        self.assertIn('桌面已关闭', result[0]['error'])
        self.assertFalse((self.home / 'report-previews').exists())

    def test_expired_worker_cancels_capture_and_cannot_receive_stale_jobs(self):
        self.manager.lease_seconds = .06
        worker = self.credentials()
        thread, result = self.begin()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertIn('连接已失效', result[0]['error'])
        with self.assertRaises(board.Conflict):
            self.manager.next(worker)
        replacement = self.credentials()
        self.assertEqual(self.manager.next(replacement), {'job': None})

    def test_timeout_and_service_shutdown_end_waiters_without_repeating_jobs(self):
        self.manager.timeout = .06
        worker = self.credentials()
        thread, result = self.begin()
        job = self.manager.next(worker)['job']
        thread.join(1)
        self.assertIn('不会自动重做', result[0]['error'])
        with self.assertRaises(board.Conflict):
            self.complete(worker, job)
        self.assertEqual(self.manager.next(worker), {'job': None})
        thread, result = self.begin()
        self.manager.next(worker)
        self.manager.close()
        thread.join(1)
        self.assertIn('宿主已关闭', result[0]['error'])
        self.assertFalse((self.home / 'report-previews').exists())

    def test_document_and_output_limits_reject_invalid_inputs(self):
        for document in ('', ' ' * 2, 'x' * (report_documents.MAX_HTML + 1)):
            with self.assertRaises(board.BoardError):
                self.manager.capture(document, title='Synthetic', goal_id='one')
        with self.assertRaises(board.BoardError):
            self.manager.capture('<p>Synthetic</p>', viewport='arbitrary', title='Synthetic', goal_id='one')
        worker = self.credentials()
        thread, result = self.begin()
        job = self.manager.next(worker)['job']
        with self.assertRaises(board.BoardError):
            self.manager.result({**worker, 'job_id': job['id'], 'png_base64': 'x' * (report_preview.MAX_PNG * 2)})
        self.manager.close_worker(worker)
        thread.join(1)
        self.assertFalse(result[0]['ok'])
