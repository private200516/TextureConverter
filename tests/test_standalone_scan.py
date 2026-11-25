import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from material_codec import Material, serialize
from material_jobs import scan_context_matches, scan_jobs
from security_io import absolute_path, pinned_path, safe_relative


class StandaloneScanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root/'Materials').mkdir()
        (self.root/'Textures').mkdir()
        self.input = self.root/'Materials/item.bgsm'

    def write_material(self, ref):
        self.input.write_bytes(serialize(Material('BGSM', 22, {'DiffuseTexture': ref})))

    def test_export_scan_preserves_reference_without_touching_dds(self):
        self.write_material('Data/Textures/item_d.dds')
        texture = self.root/'Textures/item_d.dds'
        texture.write_bytes(b'stand-in')
        with patch('material_jobs.resolve_reference', side_effect=AssertionError('DDS lookup')):
            jobs = scan_jobs(self.input, include_textures=False, inspect_texture_files=False)
        metadata = jobs[0]['texture_references']['DiffuseTexture']
        self.assertEqual(metadata['relative'].lower(), 'textures/item_d.dds')
        self.assertIsNone(metadata['exists'])
        self.assertEqual(jobs[0]['texture_paths'], {})
        self.assertEqual(jobs[0]['diagnostics'], ['READY_FOR_MATERIAL_CONVERSION'])
        self.assertTrue(scan_context_matches(jobs.scan_context, self.input, include_textures=False,
                                            inspect_texture_files=False))
        self.assertFalse(scan_context_matches(jobs.scan_context, self.input, include_textures=False))
        for include in (False, True):
            with self.subTest(include=include):
                audited = scan_jobs(self.input, include_textures=include)
                self.assertTrue(audited[0]['texture_references']['DiffuseTexture']['exists'])
                self.assertEqual(Path(audited[0]['texture_paths']['d']), texture)

    def test_lexical_validation_stays_active_when_dds_is_not_inspected(self):
        self.write_material('../outside.dds')
        job, = scan_jobs(self.input, include_textures=False, inspect_texture_files=False)
        self.assertTrue(any(code.startswith('MATERIAL_PARSE_ERROR:') for code in job['diagnostics']))

    def test_device_aliases_and_mixed_unc_prefixes_rejected_before_io(self):
        for value in ('CON .dds', 'Textures/lpt1 .dds'):
            with self.assertRaises(ValueError):safe_relative(value)
        for value in ('/\\server/share/data', '\\/server/share/data'):
            with self.assertRaises(ValueError):absolute_path(value)

    @unittest.skipUnless(os.name=='nt', 'Windows share modes')
    def test_batch_holds_report_directory_through_conversion(self):
        from config import Config
        from texture_api import run_jobs
        self.write_material('item.dds')
        with tempfile.TemporaryDirectory(dir=self.root.parent) as target:
            out = Path(target)/'runs'
            jobs=scan_jobs(self.input,include_textures=False,inspect_texture_files=False)
            observed=[]
            def progress(done,total,label):
                if not observed and total==len(jobs):
                    reports,=out.glob('reports/texture_*')
                    with self.assertRaises(PermissionError):reports.rename(reports.with_name('replaced'))
                    observed.append(True)
            log=run_jobs(jobs,Config(input_dir=str(self.input),output_dir=str(out)),progress_cb=progress)
            self.assertEqual((observed,log.errors),([True],0))

    def test_existing_read_requires_every_component(self):
        absent = self.root/'missing'/'item.bgsm'
        with self.assertRaises(FileNotFoundError):
            with pinned_path(absent, must_exist=True):
                self.fail('a read must not start through a missing ancestor')
        with pinned_path(absent):
            self.assertFalse(absent.exists())
