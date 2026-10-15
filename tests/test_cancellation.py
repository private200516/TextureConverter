"""Cancellation regressions; execute only in the approved offline guest.

External encoder tests replace Popen with owned fakes and never launch an EXE.
Pipeline tests use small generated DDS files and isolated temporary directories.
"""
import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import dds
import semantic_dds
import texture_api
from config import Config
from material_jobs import scan_jobs


class OwnedEncoder:
    def __init__(self, event=None, kill_required=False, wait_error=False):
        self.event = event
        self.kill_required = kill_required
        self.wait_error = wait_error
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.waits = []

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.killed or self.terminated and not self.kill_required:
            self.returncode = -1
            return self.returncode
        if self.wait_error:
            self.wait_error = False
            raise OSError('simulated wait failure')
        if self.event is not None:
            self.event.set()
        raise subprocess.TimeoutExpired('fake-owned-encoder', timeout)

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


class EncoderCancellationTests(unittest.TestCase):
    def test_pre_cancel_never_starts_encoder(self):
        event = threading.Event(); event.set()
        with patch('dds.subprocess.Popen') as spawn:
            with self.assertRaises(dds.CancellationRequested):
                dds._run_texconv(['unused'], event)
        spawn.assert_not_called()

    def test_cancel_stops_reaps_only_owned_process_without_pipes(self):
        event = threading.Event(); owned = OwnedEncoder(event)
        unrelated = OwnedEncoder()
        with patch('dds.subprocess.Popen', return_value=owned) as spawn:
            with self.assertRaises(dds.CancellationRequested):
                dds._run_texconv(['unused'], event)
        self.assertTrue(owned.terminated)
        self.assertIsNotNone(owned.returncode)
        self.assertFalse(unrelated.terminated or unrelated.killed)
        self.assertLessEqual(owned.waits[0], .1)
        options = spawn.call_args.kwargs
        self.assertFalse(options['shell'])
        self.assertNotEqual(options['stdout'], subprocess.PIPE)
        self.assertNotEqual(options['stderr'], subprocess.PIPE)

    def test_cancel_escalates_only_owned_encoder_then_reaps(self):
        event = threading.Event(); owned = OwnedEncoder(event, kill_required=True)
        with patch('dds.subprocess.Popen', return_value=owned):
            with self.assertRaises(dds.CancellationRequested):
                dds._run_texconv(['unused'], event)
        self.assertTrue(owned.terminated and owned.killed)
        self.assertEqual(owned.returncode, -1)
        self.assertEqual(owned.waits[-2:], [2, 2])

    def test_timeout_reaps_encoder(self):
        owned = OwnedEncoder()
        with patch('dds.subprocess.Popen', return_value=owned):
            with self.assertRaisesRegex(dds.DDSError, 'timed out'):
                dds._run_texconv(['unused'], timeout=0)
        self.assertTrue(owned.terminated)
        self.assertIsNotNone(owned.returncode)

    def test_unexpected_wait_error_also_reaps_encoder(self):
        owned = OwnedEncoder(wait_error=True)
        with patch('dds.subprocess.Popen', return_value=owned):
            with self.assertRaisesRegex(OSError, 'simulated'):
                dds._run_texconv(['unused'])
        self.assertTrue(owned.terminated)
        self.assertIsNotNone(owned.returncode)

    def test_encoder_diagnostic_budget_stops_owned_process(self):
        owned = OwnedEncoder()
        def spawn(*args, **kwargs):
            kwargs['stdout'].write(b'x' * (4 * 1024 * 1024 + 1))
            kwargs['stdout'].flush()
            return owned
        with patch('dds.subprocess.Popen', side_effect=spawn):
            with self.assertRaisesRegex(dds.DDSError, 'diagnostic output'):
                dds._run_texconv(['unused'])
        self.assertTrue(owned.terminated)
        self.assertIsNotNone(owned.returncode)

    def test_successful_encoder_keeps_diagnostics_bounded(self):
        owned = OwnedEncoder()
        def complete(timeout=None):
            owned.returncode = 0; return 0
        owned.wait = complete
        def spawn(*args, **kwargs):
            kwargs['stdout'].write(b'a' * 100000); kwargs['stdout'].flush()
            kwargs['stderr'].write(b'warning'); kwargs['stderr'].flush()
            return owned
        with patch('dds.subprocess.Popen', side_effect=spawn):
            result = dds._run_texconv(['unused'])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(result.stdout), 65536)
        self.assertEqual(result.stderr, 'warning')
        self.assertFalse(owned.terminated or owned.killed)

    def test_permission_exit_race_is_ignored_only_when_owned_process_exited(self):
        owned = OwnedEncoder()
        def terminate():
            owned.returncode = 0
            raise PermissionError('process already exited')
        owned.terminate = terminate
        dds._stop_encoder(owned)
        self.assertEqual(owned.returncode, 0)
        alive = OwnedEncoder()
        def denied(): raise PermissionError('still alive')
        alive.terminate = denied
        with self.assertRaises(dds.EncoderCleanupError): dds._stop_encoder(alive)

    def test_cancel_is_not_a_fallback_request(self):
        rgba = np.zeros((4, 4, 4), np.uint8)
        with patch('dds.os.path.isfile', return_value=True), \
             patch('dds.encode_with_texconv', side_effect=dds.CancellationRequested()), \
             patch('dds.encode_fallback') as fallback:
            with self.assertRaises(dds.CancellationRequested):
                dds.encode_dds(rgba, 'BC3_UNORM', 'unused.dds', texconv_path='unused')
        fallback.assert_not_called()

    def test_ordinary_codec_failure_still_uses_fallback(self):
        rgba = np.zeros((4, 4, 4), np.uint8)
        with patch('dds.os.path.isfile', return_value=True), \
             patch('dds.encode_with_texconv', side_effect=dds.DDSError('unsupported codec')), \
             patch('dds.encode_fallback', return_value='BC3_UNORM') as fallback:
            self.assertEqual(dds.encode_dds(rgba, 'BC3_UNORM', 'unused.dds', texconv_path='unused'),
                             ('BC3_UNORM', None))
        fallback.assert_called_once()

    def test_failed_encoder_cleanup_never_starts_fallback(self):
        with patch('dds.os.path.isfile', return_value=True), \
             patch('dds.encode_with_texconv', side_effect=dds.EncoderCleanupError('not reaped')), \
             patch('dds.encode_fallback') as fallback:
            with self.assertRaises(dds.EncoderCleanupError):
                dds.encode_dds(np.zeros((4, 4, 4), np.uint8), 'BC3_UNORM',
                               'unused.dds', texconv_path='unused')
        fallback.assert_not_called()

    def test_cancelled_texconv_result_is_not_moved_to_output(self):
        event = threading.Event()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); work = root / 'encoder'; work.mkdir()
            output = root / 'out.dds'
            def complete(command, cancel_event=None):
                (work / 'out.dds').write_bytes(b'unpublished DDS')
                event.set()
                return subprocess.CompletedProcess(command, 0, '', '')
            with patch('dds.tempfile.mkdtemp', return_value=str(work)), \
                 patch('dds._run_texconv', side_effect=complete):
                with self.assertRaises(dds.CancellationRequested):
                    dds.encode_with_texconv('unused', np.zeros((4, 4, 4), np.uint8),
                                            'BC3_UNORM', str(output), cancel_event=event)
            self.assertFalse(output.exists())
            self.assertFalse(work.exists())


class InProcessCancellationTests(unittest.TestCase):
    def test_fallback_stops_between_block_batches_before_publication(self):
        event = threading.Event(); original = dds._encode_bc1_many; batches = []
        def encode(batch):
            batches.append(len(batch)); result = original(batch); event.set(); return result
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / 'out.dds'
            with patch('dds._encode_bc1_many', side_effect=encode):
                with self.assertRaises(dds.CancellationRequested):
                    dds.encode_fallback(np.full((256, 256, 4), 127, np.uint8),
                                        'BC1_UNORM', str(output), False, cancel_event=event)
            self.assertEqual(batches, [2048])
            self.assertFalse(output.exists())
            self.assertFalse(list(Path(td).glob('.dds-*')))

    def test_unset_event_keeps_ordinary_encoded_bytes_identical(self):
        rgba = np.arange(16 * 8 * 4, dtype=np.uint16).reshape(16, 8, 4).astype(np.uint8)
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / 'a.dds'; b = Path(td) / 'b.dds'
            dds.encode_fallback(rgba, 'BC3_UNORM', str(a))
            dds.encode_fallback(rgba, 'BC3_UNORM', str(b), cancel_event=threading.Event())
            self.assertEqual(a.read_bytes(), b.read_bytes())

    def test_semantic_mips_stop_between_levels(self):
        event = threading.Event(); original = semantic_dds._box; calls = []
        def box(*args, **kwargs):
            result = original(*args, **kwargs); calls.append(1); event.set(); return result
        with patch('semantic_dds._box', side_effect=box):
            with self.assertRaises(dds.CancellationRequested):
                semantic_dds.build_semantic_mips(np.full((16, 16, 4), 127, np.uint8),
                                                cancel_event=event)
        self.assertEqual(len(calls), 1)

    def test_semantic_encoder_uses_cancellable_external_boundary(self):
        with tempfile.TemporaryDirectory() as td:
            tool = Path(td) / 'unused.exe'; tool.write_bytes(b'data, not executed')
            output = Path(td) / 'out.dds'
            with patch('dds._run_texconv', side_effect=dds.CancellationRequested()), \
                 patch('dds._encode_level_fallback') as fallback:
                with self.assertRaises(dds.CancellationRequested):
                    semantic_dds.encode_semantic_dds(np.full((8, 8, 4), 127, np.uint8),
                        'BC3_UNORM', output, texconv_path=str(tool), cancel_event=threading.Event())
            self.assertFalse(output.exists())
            fallback.assert_not_called()

    def test_private_semantic_cleanup_cannot_mask_fatal_encoder_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); tool = root / 'unused.exe'; tool.write_bytes(b'not executed')
            scratch = root / 'private'; scratch.mkdir(); cleanup_attempts = []
            class LockedScratch:
                def __init__(self, *args, **kwargs):
                    self.ignore = kwargs.get('ignore_cleanup_errors', False)
                def __enter__(self): return str(scratch)
                def __exit__(self, typ, value, tb):
                    cleanup_attempts.append(PermissionError('owned encoder file remains locked'))
                    if not self.ignore: raise cleanup_attempts[-1]
                    return False
            with patch('semantic_dds.tempfile.TemporaryDirectory', LockedScratch), \
                 patch('dds._run_texconv', side_effect=dds.EncoderCleanupError('cannot reap')):
                with self.assertRaises(dds.EncoderCleanupError):
                    semantic_dds.encode_semantic_dds(np.full((8, 8, 4), 127, np.uint8),
                        'BC3_UNORM', root / 'out.dds', texconv_path=str(tool))
            self.assertEqual(len(cleanup_attempts), 1)
            self.assertFalse((root / 'out.dds').exists())

    def test_readback_cancellation_is_not_reported_as_success(self):
        event = threading.Event(); original = dds.decode_dds_pixels
        def read(*args, **kwargs):
            result = original(*args, **kwargs); event.set(); return result
        options = texture_api._options(extra={'mode': 'off', 'texconv_path': False,
                                             'diffuse_fmt': 'BC3_UNORM'})
        options.update(texture_api._codec_context(False))
        with tempfile.TemporaryDirectory() as td, patch('dds.decode_dds_pixels', side_effect=read):
            with self.assertRaises(dds.CancellationRequested):
                texture_api._encode(np.full((8, 8, 4), 127, np.uint8), 'd',
                                    Path(td) / 'out.dds', options, event)


class GroupRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.source = self.root / 'input'; (self.source / 'textures').mkdir(parents=True)
        self.output = self.root / 'output'
        for index in range(2):
            rgba = np.full((8, 8, 4), 95 + index * 20, np.uint8); rgba[..., 3] = 255
            dds.encode_fallback(rgba, 'BC3_UNORM', str(self.source / 'textures' / ('item%d_d.dds' % index)))
        self.jobs = scan_jobs(self.source)
        self.options = {'mode': 'off', 'texconv_path': False, 'diffuse_fmt': 'BC3_UNORM'}

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self, root):
        return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in root.rglob('*') if p.is_file()}

    def test_later_cancel_retains_prior_commit_and_reports_no_error(self):
        event = threading.Event(); original = texture_api._encode; calls = []
        before = self.snapshot(self.source)
        def encode(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                event.set(); raise dds.CancellationRequested()
            return original(*args, **kwargs)
        cfg = Config(input_dir=str(self.source), output_dir=str(self.output), metal_mode='off',
                     metal_options={'texconv_path': False, 'diffuse_fmt': 'BC3_UNORM'})
        with patch('texture_api._encode', side_effect=encode):
            log = texture_api.run_jobs(self.jobs, cfg, cancel_event=event)
        run = Path(log.output_dir); report = json.loads((run / 'reports' / 'report.json').read_text())
        self.assertTrue(report['cancelled']); self.assertEqual(report['errors'], 0)
        self.assertEqual(report['processed'], 1)
        self.assertEqual([r['status'] for r in report['results']], ['committed'])
        converted = run / 'converted'
        self.assertEqual(len(list(converted.rglob('*.dds'))), 1)
        self.assertTrue((converted / '.manifests' / (self.jobs[0]['id'] + '.json')).exists())
        self.assertFalse((converted / '.manifests' / (self.jobs[1]['id'] + '.json')).exists())
        self.assertFalse(list(converted.glob('.staging-*')))
        self.assertFalse(list(converted.rglob('*.tmp')))
        self.assertTrue((run / 'PARTIAL').exists()); self.assertFalse((run / 'COMPLETE').exists())
        self.assertEqual(before, self.snapshot(self.source))

    def test_cancel_after_asset_move_rolls_back_only_current_group(self):
        texture_api.run_job(self.jobs[0], self.output, options=self.options)
        before = self.snapshot(self.output); event = threading.Event(); rename = Path.rename
        def move(path, target):
            result = rename(path, target)
            if '.staging-' in str(path): event.set()
            return result
        with patch.object(Path, 'rename', move):
            with self.assertRaises(dds.CancellationRequested):
                texture_api.run_job(self.jobs[1], self.output, options=self.options, cancel_event=event)
        self.assertEqual(before, self.snapshot(self.output))
        self.assertFalse(list(self.output.glob('.staging-*')))

    def test_cancel_before_manifest_publication_cleans_pending_manifest(self):
        texture_api.run_job(self.jobs[0], self.output, options=self.options)
        before = self.snapshot(self.output); event = threading.Event(); dumps = json.dumps
        def serialize(value, *args, **kwargs):
            if isinstance(value, dict) and value.get('status') == 'committed': event.set()
            return dumps(value, *args, **kwargs)
        with patch('texture_api.json.dumps', side_effect=serialize):
            with self.assertRaises(dds.CancellationRequested):
                texture_api.run_job(self.jobs[1], self.output, options=self.options, cancel_event=event)
        self.assertEqual(before, self.snapshot(self.output))
        self.assertFalse(list(self.output.rglob('*.tmp')))
        self.assertFalse(list(self.output.glob('.staging-*')))

    def test_cancel_after_manifest_commit_keeps_committed_row_and_assets(self):
        event = threading.Event(); replace = Path.replace
        def publish(path, target):
            result = replace(path, target)
            if path.parent.name == '.manifests' and path.suffix == '.tmp': event.set()
            return result
        cfg = Config(input_dir=str(self.source), output_dir=str(self.output), metal_mode='off',
                     metal_options={'texconv_path': False, 'diffuse_fmt': 'BC3_UNORM'})
        with patch.object(Path, 'replace', publish):
            log = texture_api.run_jobs(self.jobs, cfg, cancel_event=event)
        run = Path(log.output_dir); report = json.loads((run / 'reports' / 'report.json').read_text())
        self.assertTrue(report['cancelled']); self.assertEqual(report['errors'], 0)
        self.assertEqual(report['processed'], 1)
        self.assertEqual(report['results'][0]['status'], 'committed')
        converted = run / 'converted'
        self.assertTrue((converted / '.manifests' / (self.jobs[0]['id'] + '.json')).exists())
        for relative in report['results'][0]['outputs']:
            self.assertTrue((converted / relative).is_file())
        self.assertFalse((converted / '.manifests' / (self.jobs[1]['id'] + '.json')).exists())
        self.assertFalse(list(converted.glob('.staging-*')))
        self.assertFalse(list(converted.rglob('*.tmp')))

    def test_failed_encoder_cleanup_stops_batch_without_fake_cancellation(self):
        event = threading.Event(); started = []
        def encode(*args, **kwargs):
            started.append(1); raise dds.EncoderCleanupError('owned encoder still alive')
        cfg = Config(input_dir=str(self.source), output_dir=str(self.output), metal_mode='off',
                     metal_options={'texconv_path': False, 'diffuse_fmt': 'BC3_UNORM'})
        with patch('texture_api._encode', side_effect=encode):
            with self.assertRaises(dds.EncoderCleanupError):
                texture_api.run_jobs(self.jobs, cfg, cancel_event=event)
        self.assertEqual(len(started), 1); self.assertFalse(event.is_set())
        runs = list(self.output.glob('run_*')); self.assertEqual(len(runs), 1)
        report = json.loads((runs[0] / 'reports' / 'report.json').read_text())
        self.assertFalse(report['cancelled']); self.assertEqual(report['errors'], 1)
        self.assertEqual(report['processed'], 1)
        self.assertEqual(report['results'][0]['status'], 'failed')
        self.assertIn('owned encoder still alive', report['results'][0]['error'])
        self.assertTrue((runs[0] / 'PARTIAL').exists()); self.assertFalse((runs[0] / 'COMPLETE').exists())
        self.assertFalse(list((runs[0] / 'converted').glob('.staging-*')))

    def test_fatal_encoder_cleanup_keeps_previous_committed_group(self):
        event = threading.Event(); original = texture_api._encode; started = []
        def encode(*args, **kwargs):
            started.append(1)
            if len(started) == 2: raise dds.EncoderCleanupError('cannot reap owned encoder')
            return original(*args, **kwargs)
        cfg = Config(input_dir=str(self.source), output_dir=str(self.output), metal_mode='off',
                     metal_options={'texconv_path': False, 'diffuse_fmt': 'BC3_UNORM'})
        with patch('texture_api._encode', side_effect=encode):
            with self.assertRaises(dds.EncoderCleanupError):
                texture_api.run_jobs(self.jobs, cfg, cancel_event=event)
        run = next(self.output.glob('run_*'))
        report = json.loads((run / 'reports' / 'report.json').read_text())
        self.assertFalse(event.is_set()); self.assertFalse(report['cancelled'])
        self.assertEqual(report['errors'], 1); self.assertEqual(report['processed'], 2)
        self.assertEqual([r['status'] for r in report['results']], ['committed', 'failed'])
        for relative in report['results'][0]['outputs']:
            self.assertTrue((run / 'converted' / relative).is_file())
        self.assertTrue((run / 'converted' / '.manifests' / (self.jobs[0]['id'] + '.json')).exists())
        self.assertFalse((run / 'converted' / '.manifests' / (self.jobs[1]['id'] + '.json')).exists())
        self.assertFalse(list((run / 'converted').glob('.staging-*')))
        self.assertTrue((run / 'PARTIAL').exists())


if __name__ == '__main__':
    unittest.main()
