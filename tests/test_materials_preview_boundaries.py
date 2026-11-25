import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from config import Config, load_config, save_config
from material_codec import Material, MaterialError, parse, serialize
from material_converter import convert_file
from material_jobs import scan_jobs
from preview_process import PreviewProcessError, read_wire, write_wire
from texture_api import run_jobs


class MaterialsOnlyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.input = self.root / 'data'
        self.input.mkdir()
        self.material = self.input / 'item.bgsm'
        self.material.write_bytes(serialize(Material('BGSM', 22, {'DiffuseTexture': 'item.dds'})))

    def test_texture_files_do_not_consume_material_selection_quota(self):
        (self.input / 'extras.dds').write_bytes(b'DDS')
        with patch('material_jobs.MAX_SCAN_FILES', 1):
            jobs = scan_jobs(self.input, include_textures=False)
            self.assertEqual(len(jobs), 1)
            with self.assertRaisesRegex(ValueError, 'SCAN_FILE_LIMIT'):
                scan_jobs(self.input)

    def test_export_and_analysis_need_no_pixel_codec_or_user_masks(self):
        textures = self.input / 'textures'
        textures.mkdir()
        (textures / 'item.dds').write_bytes(b'available but never decoded')
        jobs = scan_jobs(self.input, include_textures=False)
        for analyze in (False, True):
            with self.subTest(analyze=analyze), patch('texture_api._codec_context', side_effect=AssertionError), patch('texture_api.preview_job', side_effect=AssertionError):
                cfg = Config(input_dir=str(self.input), output_dir=str(self.root/f'output-{analyze}'), metal_mode='manual',
                             metal_options={'edit_mask_path': str(self.root/'absent.png')})
                log = run_jobs(jobs, cfg, analyze_only=analyze)
                self.assertEqual(log.errors, 0)
                run = Path(log.output_dir)
                self.assertFalse(list(run.rglob('*.dds')))
                material = run/'materials/item.bgsm'
                if analyze:
                    self.assertFalse(material.exists())
                    self.assertFalse((run/'previews').exists())
                    report = json.loads((Path(log.report_dir)/'report.json').read_bytes())
                    self.assertEqual(report['results'][0]['status'], 'analyzed')
                else:
                    self.assertEqual(parse(material).version, 2)

    def test_standalone_material_points_to_separately_generated_standard_map(self):
        fields = {'SpecularTexture':'Props/Tool_R.DDS', 'LightingTexture':'props/tool_l.dds'}
        for lighting, expected in [('props/tool_l.dds','props/tool_s.dds'), ('other/tool_l.dds','')]:
            with self.subTest(lighting=lighting):
                fields['LightingTexture'] = lighting
                self.material.write_bytes(serialize(Material('BGSM', 22, fields)))
                cfg = Config(input_dir=str(self.input), output_dir=str(self.root/('output-'+('known' if expected else 'unknown'))))
                log = run_jobs(scan_jobs(self.input, include_textures=False), cfg)
                self.assertEqual(log.errors, 0)
                run = Path(log.output_dir)
                target = parse(run/'materials/item.bgsm')
                self.assertEqual(target.fields['SmoothSpecTexture'], expected)
                self.assertFalse(list(run.rglob('*.dds')))
                report = json.loads((Path(log.report_dir)/'report.json').read_bytes())
                warning = 'EXTERNAL_PACKED_SPECULAR' if expected else 'PACKED_SPECULAR_REQUIRED'
                self.assertTrue(any(warning in item for item in report['results'][0]['warnings']))

    def test_colliding_material_temp_survives_exclusive_open_failure(self):
        name = Mock(hex='reserved')
        existing = self.root / '.material-reserved.tmp'
        existing.write_bytes(b'existing')
        with patch('material_converter.uuid.uuid4', return_value=name):
            with self.assertRaises(FileExistsError):
                convert_file(self.material, self.root/'result.bgsm')
        self.assertEqual(existing.read_bytes(), b'existing')

    def test_flat_editor_schema_rejects_nested_values_before_decoding(self):
        with patch('security_io.json.loads', side_effect=AssertionError):
            with self.assertRaisesRegex(MaterialError, 'nesting'):
                parse(b'{"eAlphaBlendMode":"None","fAlpha":[[1]]}', json_kind='BGSM', json_version=22)


class PreferenceWriteTests(unittest.TestCase):
    def test_directory_preferences_must_be_usable_strings(self):
        for key in ('materials_input', 'input_dir', 'output_dir'):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                Config.from_json(json.dumps({key: ['invalid']}))
        self.assertEqual(Config.from_json('{}').materials_input, '')

    def test_atomic_save_preserves_other_name_and_last_complete_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            other = root/'other.json'
            target = root/'config.json'
            other.write_bytes(b'original')
            os.link(other, target)
            with patch('config.config_path', return_value=str(target)):
                save_config(Config(lang='en'))
                self.assertEqual(other.read_bytes(), b'original')
                self.assertEqual(load_config().lang, 'en')
                with patch('config.os.replace', side_effect=PermissionError):
                    save_config(Config(lang='zh'))
                self.assertEqual(load_config().lang, 'en')
                self.assertFalse(list(root.glob('.settings-*.tmp')))


class PreviewWireTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def write_frame(self, descriptor, data):
        (self.root/'response.json').write_text(json.dumps(descriptor))
        (self.root/'response.bin').write_bytes(data)

    def test_non_scalar_dtype_is_rejected_before_numpy_deserializes_it(self):
        self.write_frame({'$array':0,'dtype':[('component','f4')],'shape':[1],'offset':0,'nbytes':4}, bytes(4))
        with patch('preview_process.np.dtype', side_effect=AssertionError):
            with self.assertRaisesRegex(PreviewProcessError, 'type/shape'):
                read_wire(self.root, 'response')

    def test_array_records_use_exactly_the_payload_once(self):
        record = {'$array':0,'dtype':'|u1','shape':[1],'offset':0,'nbytes':1}
        for records in (record, [record, dict(record, **{'$array':1})]):
            with self.subTest(records=records):
                self.write_frame(records, b'ab')
                with self.assertRaises(PreviewProcessError):
                    read_wire(self.root, 'response')

    def test_wire_preserves_scalar_empty_non_native_and_shared_arrays(self):
        big_endian = np.arange(6, dtype='>i2').reshape(2,3)
        source = [np.array(7), np.zeros((0,2)), big_endian, big_endian]
        write_wire(self.root, 'request', source)
        result = read_wire(self.root, 'request')
        for actual, expected in zip(result, source):
            np.testing.assert_array_equal(actual, expected)
            self.assertEqual(actual.dtype.str, expected.dtype.str)
        self.assertIs(result[2], result[3])
