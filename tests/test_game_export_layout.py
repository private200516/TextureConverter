"""Shared game-root exports and selectable on-disk reports."""
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from config import Config
from material_codec import Material, parse, serialize
from material_jobs import game_output_root, scan_jobs
from security_io import publish_new
from texture_api import run_job, run_jobs


class GameExportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.source = root/'source'/'Materials'/'Architecture'/'Clinic'/'wall.bgsm'
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(serialize(Material('BGSM', 22, {'Alpha': .35})))
        self.job, = scan_jobs(self.source, include_textures=False, inspect_texture_files=False)
        self.out = root/'Shared Data'
        self.expected = self.out/'Materials'/'Architecture'/'Clinic'/'wall.bgsm'
        self.cfg = Config(input_dir=str(self.source), output_dir=str(self.out), generate_report=False)

    def files(self):
        return {p.relative_to(self.out).as_posix().lower() for p in self.out.rglob('*') if p.is_file()}

    def test_silent_export_reuses_identical_material_and_preserves_mesh(self):
        mesh = self.out/'meshes'/'Architecture'/'Clinic'/'wall.nif'
        mesh.parent.mkdir(parents=True)
        mesh.write_bytes(b'previously exported mesh')
        events = []
        log = run_jobs([self.job], self.cfg, log_cb=lambda *event: events.append(event))
        timestamp = self.expected.stat().st_mtime_ns
        repeated = run_jobs([self.job], self.cfg)
        self.assertEqual((log.ok, repeated.ok, log.report_dir), (1, 1, None))
        self.assertEqual(Path(log.output_dir), self.out)
        self.assertEqual(parse(self.expected).version, 2)
        self.assertEqual(timestamp, self.expected.stat().st_mtime_ns)
        self.assertEqual(self.files(), {'meshes/architecture/clinic/wall.nif','materials/architecture/clinic/wall.bgsm'})
        self.assertFalse(any(p.name.startswith('.') for p in self.out.rglob('*')))
        self.assertTrue(any(level=='OK' for level,_,_ in events))

    def test_material_only_then_grouped_export_reuses_authored_references(self):
        import dds
        import numpy as np
        texture_dir = self.source.parents[3]/'textures'/'Props'
        texture_dir.mkdir(parents=True)
        data = np.full((8,8,4), 128, np.uint8)
        refs = {'DiffuseTexture':'Props/Item_d.dds', 'SpecularTexture':'Props/Item_r.dds',
                'LightingTexture':'Props/Item_l.dds'}
        for ref in refs.values():
            dds.encode_fallback(data,'BC3_UNORM',str(texture_dir/Path(ref).name))
        self.source.write_bytes(serialize(Material('BGSM',22,refs)))
        self.cfg.metal_options={'texconv_path':False}
        first = run_jobs(scan_jobs(self.source,include_textures=False),self.cfg)
        material_bytes = self.expected.read_bytes()
        combined = run_jobs(scan_jobs(self.source),self.cfg)
        self.assertEqual((first.errors, combined.errors, combined.ok), (0,0,1))
        self.assertEqual(self.expected.read_bytes(),material_bytes)
        fields = parse(material_bytes).textures()
        self.assertEqual(fields['DiffuseTexture'], 'Props/Item_d.dds')
        self.assertEqual(fields['SmoothSpecTexture'], 'Props/Item_s.dds')
        self.assertTrue(all((self.out/'textures'/path).is_file() for path in fields.values()))
        self.cfg.output_dir = str(self.out.with_name('reverse-order'))
        first = run_jobs(scan_jobs(self.source),self.cfg)
        second = run_jobs(scan_jobs(self.source,include_textures=False),self.cfg)
        self.assertEqual((first.errors, second.errors, second.ok), (0,0,1))
        self.assertEqual((Path(self.cfg.output_dir)/self.expected.relative_to(self.out)).read_bytes(),material_bytes)

    def test_report_toggle_preserves_other_runs_and_mesh_reports(self):
        self.cfg.generate_report = True
        other = self.out/'reports'/'mesh_existing.json'
        other.parent.mkdir(parents=True)
        other.write_bytes(b'mesh report')
        first = run_jobs([self.job], self.cfg)
        second = run_jobs([self.job], self.cfg)
        self.assertNotEqual(first.report_dir, second.report_dir)
        for run in (first,second):
            reports = Path(run.report_dir)
            self.assertEqual(reports.parent, self.out/'reports')
            payload = json.loads((reports/'report.json').read_bytes())
            self.assertEqual(payload['results'][0]['status'], 'committed')
            self.assertTrue((reports/'COMPLETE').is_file())
            self.assertTrue((reports/'config_snapshot.json').is_file())
            self.assertTrue((reports/'conversion_log.txt').is_file())
        self.assertFalse((self.out/'.manifests').exists())
        before = {p:p.read_bytes() for p in (self.out/'reports').rglob('*') if p.is_file()}
        self.cfg.generate_report = False
        self.assertIsNone(run_jobs([self.job], self.cfg).report_dir)
        self.assertEqual(before, {p:p.read_bytes() for p in (self.out/'reports').rglob('*') if p.is_file()})

    def test_conflict_produces_live_error_without_touching_existing_file(self):
        self.expected.parent.mkdir(parents=True)
        self.expected.write_bytes(b'user content')
        log = run_jobs([self.job], self.cfg)
        self.assertEqual((log.errors, log.ok, log.report_dir), (1,0,None))
        self.assertIn('Conflicting existing output', log.entries[-1].message)
        self.assertEqual(self.expected.read_bytes(), b'user content')
        self.assertEqual(len(self.files()), 1)

    def test_rollback_without_disk_markers(self):
        cancel = threading.Event()
        def cancel_on_publish(source, target):
            publish_new(source, target)
            cancel.set()
        with patch('texture_api.publish_new', side_effect=cancel_on_publish):
            log = run_jobs([self.job], self.cfg, cancel_event=cancel)
        self.assertEqual((log.ok, log.errors), (0,0))
        self.assertFalse(self.files())
        self.assertFalse(any(p.name.startswith('.') for p in self.out.rglob('*')))

    def test_two_publishers_share_identical_output(self):
        ready = threading.Barrier(2)
        def run():
            ready.wait(10)
            return run_job(self.job,self.out,generate_report=False)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a,b = [pool.submit(run) for _ in range(2)]
            self.assertEqual([f.result(timeout=20)['status'] for f in (a,b)], ['committed','committed'])
        self.assertEqual(self.files(), {'materials/architecture/clinic/wall.bgsm'})

    def test_asset_folder_must_be_normalized_by_caller_and_source_is_protected(self):
        for leaf in ('Meshes','materials','TEXTURES'):
            requested = self.out/leaf
            self.assertEqual(game_output_root(requested), self.out)
            self.cfg.output_dir = str(requested)
            with self.assertRaisesRegex(ValueError,'parent'):
                run_jobs([self.job],self.cfg)
            self.assertFalse(self.out.exists())
        self.cfg.output_dir = str(self.source.parents[3]/'unrelated-output')
        with self.assertRaisesRegex(ValueError,'overlap'):
            run_jobs([self.job],self.cfg)
        self.assertFalse(Path(self.cfg.output_dir).exists())

    def test_material_cannot_publish_over_mesh_or_external_path(self):
        for path in ('meshes/test.nif','not-an-asset.txt','../outside.bgsm'):
            tampered = dict(self.job, relative_path=path)
            with self.assertRaises(ValueError):
                run_jobs([tampered],self.cfg)
        self.assertFalse(self.out.exists())

    def test_blank_inputs_or_outputs_are_rejected_before_writing(self):
        for source,destination in (('',str(self.out)),(str(self.source),''),(str(self.out/'missing'),str(self.out))):
            with self.subTest(source=source,destination=destination):
                cfg=Config(input_dir=source, output_dir=destination,generate_report=False)
                with self.assertRaises((ValueError,FileNotFoundError)):
                    run_jobs([],cfg)
        self.assertFalse(self.out.exists())

    def test_dry_run_without_reports_leaves_no_output_files(self):
        self.assertEqual(run_jobs([self.job],self.cfg,dry_run=True).ok, 1)
        self.assertEqual(self.files(),set())
        self.assertFalse(self.out.exists())

    def test_isolated_compatibility_remains_available_with_both_report_settings(self):
        self.cfg.output_layout = 'isolated'
        for enabled in (False, True):
            self.cfg.generate_report = enabled
            log = run_jobs([self.job],self.cfg)
            run = Path(log.output_dir)
            self.assertEqual(run.parent,self.out)
            self.assertTrue((run/'converted'/self.expected.relative_to(self.out)).is_file())
            self.assertEqual((run/'converted'/'.manifests').exists(), enabled)
            self.assertEqual((run/'reports').exists(), enabled)
            self.assertEqual((run/'COMPLETE').exists(), enabled)
