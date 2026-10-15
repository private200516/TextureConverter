"""Security boundary regressions prepared for an authorized isolated test phase.
These tests were not executed during the static-only phase 5A.
"""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import dds
import texture_api
from config import Config
from material_codec import MaterialError, MAX_MATERIAL_BYTES, parse
from material_jobs import scan_jobs
from options_policy import processing_options


class ProcessingOptionBoundaryTests(unittest.TestCase):
    def test_config_rejects_executable_paths_and_internal_controls(self):
        for key, value in [('texconv_path', 'C:/untrusted/codec.exe'),
                           ('_codec_identity', {'path': 'C:/untrusted/codec.exe'}),
                           ('_texture_cache', {})]:
            for field in ('metal_options', 'job_overrides'):
                data={field: {key: value} if field=='metal_options' else {'job': {key: value}}}
                with self.subTest(key=key, field=field), self.assertRaises(ValueError):
                    Config.from_json(json.dumps(data))

    def test_programmatic_job_override_rejected_before_output_or_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            cfg=Config(input_dir=str(root/'input'),output_dir=str(root/'output'),
                       job_overrides={'job': {'texconv_path': 'C:/untrusted/codec.exe'}})
            with patch('dds.subprocess.Popen',side_effect=AssertionError('must not launch')):
                with self.assertRaisesRegex(ValueError,'EXECUTABLE_OPTION_FORBIDDEN'):
                    texture_api.run_jobs([],cfg)
            self.assertFalse((root/'output').exists())

    def test_existing_ui_parameters_and_explicit_fallback_are_preserved(self):
        settings={'mode':'manual','preset':'steel','reconstruction_strength':.65,
                  'tint_retention':.25,'specular_strength':.35,'smoothness_strength':.25,
                  'allow_experimental':True,'boundary':'erode',
                  'color_contract_override':'linear_linear','edit_mask_path':'edit.png',
                  'protect_mask_path':'protect.png','reviewed_source_hashes':{'a':'hash'},
                  'texconv_path':False}
        cfg=Config.from_json(json.dumps({'metal_options':settings,'job_overrides':{'job':settings}}))
        self.assertEqual(cfg.metal_options,settings)
        self.assertEqual(cfg.job_overrides['job'],settings)
        pixels=np.ones((2,2))
        prepared=texture_api._options(extra=dict(settings,manual_mask=pixels))
        self.assertIs(prepared['manual_mask'],pixels)

    def test_json_cannot_impersonate_internal_runtime_state(self):
        cache={};callback=lambda stage: None
        internal=texture_api._RuntimeOptions(mode='off',_texture_cache=cache,_stage_cb=callback)
        forwarded=texture_api._options(extra=internal)
        self.assertIs(forwarded['_texture_cache'],cache)
        self.assertIs(forwarded['_stage_cb'],callback)
        with self.assertRaisesRegex(ValueError,'UNSUPPORTED_PROCESSING_OPTION'):
            processing_options(dict(internal))


class MaterialReadBoundaryTests(unittest.TestCase):
    def test_path_parser_never_requests_unbounded_material_read(self):
        class LargeFile(io.BytesIO):
            def read(self, size=-1):
                if size < 0 or size > MAX_MATERIAL_BYTES+1:
                    raise AssertionError('material read escaped its allocation cap')
                return super().read(size)
        stream=LargeFile(b'x'*(MAX_MATERIAL_BYTES+2))
        with patch('material_codec.Path.open',return_value=stream):
            with self.assertRaisesRegex(MaterialError,'Material size'):
                parse(Path('oversized.bgsm'))

    def test_scanner_reports_oversized_material_as_failed_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'oversized.bgem'
            with path.open('wb') as stream:stream.truncate(MAX_MATERIAL_BYTES+1)
            jobs=scan_jobs(root,include_textures=False)
            self.assertEqual(len(jobs),1)
            self.assertTrue(any('MATERIAL_PARSE_ERROR' in x and 'Material size' in x
                                for x in jobs[0]['diagnostics']))


class MaskReadBoundaryTests(unittest.TestCase):
    def test_png_grid_mismatch_rejected_before_pixel_decode(self):
        class HeaderOnly:
            format='PNG';mode='L';size=(8192,8192)
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def convert(self,*args):raise AssertionError('pixels must not decode')
            def __array__(self,*args,**kwargs):raise AssertionError('pixels must not copy')
        with patch('texture_api.Image.open',return_value=HeaderOnly()):
            with self.assertRaisesRegex(ValueError,'MASK_SIZE_MISMATCH'):
                texture_api._mask({'edit_mask_path':'large.png'},'manual_mask','edit_mask_path',(8,8))

    def test_mask_budget_rejects_before_open(self):
        with patch('texture_api.Image.open',side_effect=AssertionError('must not open')):
            with self.assertRaisesRegex(ValueError,'MASK_MEMORY_BUDGET_EXCEEDED'):
                texture_api._mask({'edit_mask_path':'large.png'},'manual_mask','edit_mask_path',(16384,16384))

    def test_nested_non_numeric_mask_rejected_before_numpy_allocation(self):
        value=[[[1,2],[3,4]],[[5,6],[7,8]]]
        with patch('texture_api.np.asarray',side_effect=AssertionError('must not allocate')):
            with self.assertRaisesRegex(ValueError,'MASK_SIZE_MISMATCH'):
                texture_api._mask({'manual_mask':value},'manual_mask','edit_mask_path',(2,2))

    def test_disabled_mask_paths_are_not_read_for_processing_identity(self):
        job={'texture_paths':{},'material_fields':{},'_paths_resolved':True}
        baseline=texture_api._variant_fingerprint(job,{'mode':'off'})
        with patch('texture_api.sha256',side_effect=AssertionError('must not read unused mask')):
            actual=texture_api._variant_fingerprint(job,{'mode':'off','edit_mask_path':'missing.png',
                                                        'protect_mask_path':'missing2.png'})
        self.assertEqual(actual,baseline)


class CodecReportBoundaryTests(unittest.TestCase):
    def test_failed_external_codec_reports_actual_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            info={}
            with patch('dds.os.path.isfile',return_value=True), \
                 patch('dds.encode_with_texconv',side_effect=dds.DDSError('synthetic codec failure')):
                dds.encode_dds(np.zeros((4,4,4),np.uint8),'BC3_UNORM',
                               str(Path(directory)/'out.dds'),False,
                               texconv_path='unused',codec_info=info)
            self.assertEqual(info['backend'],'numpy-fallback')


if __name__=='__main__':unittest.main()
