"""FO76 scalar reflectance and diffuse-grid regression tests."""
import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np

import dds
import material
from material_codec import Material, serialize
from material_jobs import scan_jobs
from texture_api import preview_job, run_job


class MetalCompensationTests(unittest.TestCase):
    def test_blackened_scalar_metal_selectivity_and_coverage(self):
        diffuse = np.array([[[10, 13, 16, 255], [4, 5, 6, 255], [9, 40, 100, 255],
                             [16, 17, 18, 0], [103, 104, 105, 128]]], dtype=np.uint8)
        reflectance = np.full_like(diffuse, 66)
        reflectance[0, 1, 0] = 8
        result = material.apply_metal_compensation(
            diffuse, reflectance, .5, reflectance_format='BC4_UNORM',
            diffuse_format='BC1_UNORM_SRGB')
        self.assertGreater(result[0, 0, 0], 55)
        self.assertLess(result[0, 0, 0], 100)
        np.testing.assert_array_equal(result[..., 3], diffuse[..., 3])
        np.testing.assert_array_equal(result[:, 1:], diffuse[:, 1:])

    def test_mismatched_scalar_grid_ignores_its_alpha_and_respects_unknown_transfer(self):
        diffuse = np.zeros((3, 5, 4), np.uint8)
        diffuse[..., 3] = 255
        other = np.zeros((6, 10, 4), np.uint8)
        other[..., 0] = 60
        # The matte target depends on the diffuse's explicitly tagged transfer.
        outputs = [material.apply_metal_compensation(
            diffuse, other, .5, reflectance_format='R8_UNORM', diffuse_format=fmt)
            for fmt in ('BC1_UNORM_SRGB', 'BC1_UNORM')]
        self.assertEqual(outputs[0].shape, diffuse.shape)
        self.assertGreater(outputs[0][0, 0, 0], 55)
        np.testing.assert_array_equal(outputs[1], diffuse)
        np.testing.assert_array_equal(material.apply_metal_compensation(diffuse, other, 0), diffuse)

    def test_strength_validation_and_cancel(self):
        surface = np.zeros((4, 4, 4), np.uint8)
        with self.assertRaisesRegex(ValueError, 'strength'):
            material.apply_metal_compensation(surface, surface, float('nan'))
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(dds.CancellationRequested):
            material.apply_metal_compensation(surface, surface, .5, cancel_event=cancel)

    def test_crossed_source_dimensions_use_an_existing_grid(self):
        lighting = np.zeros((8, 2, 4), np.uint8)
        reflectance = np.ones((2, 9, 4), np.uint8)
        out = material.build_specular(lighting, reflectance)
        self.assertEqual(out.shape, reflectance.shape)

    def test_regular_export_and_reviewed_preview_from_single_bgsm(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / 'assets'
            textures = root / 'textures'
            materials = root / 'materials'
            textures.mkdir(parents=True)
            materials.mkdir()
            color = np.full((8, 8, 4), 12, np.uint8)
            color[..., 3] = 255
            colors = color.tobytes()
            (textures / 'steel_d.dds').write_bytes(dds._dds_header(
                8, 8, 1, 'R8G8B8A8_UNORM_SRGB', len(colors)) + colors)
            ref = np.full((8, 8), 65, np.uint8).tobytes()
            (textures / 'steel_r.dds').write_bytes(dds._dds_header(8, 8, 1, 'R8_UNORM', len(ref)) + ref)
            source = materials / 'steel.bgsm'
            source.write_bytes(serialize(Material('BGSM', 22, {
                'DiffuseTexture': 'steel_d.dds', 'SpecularTexture': 'steel_r.dds',
                'PBR': True, 'SpecularEnabled': True})))
            job, = scan_jobs(source)
            options = {'metal_compensation': True, 'diffuse_fmt': 'BC1_UNORM',
                       'generate_mipmap': False, 'texconv_path': False}
            output = base / 'out'
            report = run_job(job, output, options=options)
            self.assertEqual(report['status'], 'committed')
            result = dds.decode_dds_pixels(str(output / 'textures/steel_d.dds'))
            self.assertGreater(result[0, 0, 0], 55)
            # Advanced Auto uses the same evidence for a scalar map, and still
            # requires the established explicit review for a production export.
            preview = preview_job(job, {'mode': 'auto'})
            self.assertTrue(preview['metadata']['metal']['applied'])
            flagged = dict(job, diagnostics=['UNSUPPORTED_SHADER'])
            fail_closed = base / 'unsupported'
            run_job(flagged, fail_closed, options=options)
            preserved = dds.decode_dds_pixels(str(fail_closed / 'textures/steel_d.dds'))
            self.assertLess(preserved[0, 0, 0], 20)
