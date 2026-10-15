"""Game-resource tree discovery regressions; run only in approved guest tests."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from material_codec import Material, serialize
from material_jobs import asset_root, scan_jobs


class MaterialResourceCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write_tree(self, base, version=22):
        (base / 'Materials').mkdir(parents=True)
        (base / 'Textures').mkdir()
        texture = base / 'Textures' / 'shared.dds'
        texture.write_bytes(b'DDS test reference data')
        material = base / 'Materials' / 'sample.bgsm'
        material.write_bytes(serialize(Material('BGSM', version, {
            'DiffuseTexture': 'Textures/shared.dds'})))
        return material, texture

    def test_wrapped_assets_resolve_references_inside_discovered_tree(self):
        base = self.root / 'Wrapper' / 'Data'
        material, texture = self.write_tree(base)
        before = hashlib.sha256(material.read_bytes()).hexdigest()
        jobs = scan_jobs(self.root / 'Wrapper', include_textures=False)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['asset_root'], str(base.resolve()))
        self.assertEqual(jobs[0]['relative_path'], 'Materials/sample.bgsm')
        self.assertEqual(jobs[0]['texture_paths']['d'], str(texture.resolve()))
        self.assertNotIn('MATERIAL_REFERENCE_ERROR', ' '.join(jobs[0]['diagnostics']))
        self.assertEqual(hashlib.sha256(material.read_bytes()).hexdigest(), before)

    def test_shared_game_parent_rejected_before_ambiguous_output_queue(self):
        self.write_tree(self.root / 'Fallout 4', version=2)
        self.write_tree(self.root / 'Fallout 76', version=22)
        with self.assertRaisesRegex(ValueError, 'MULTIPLE_RESOURCE_ROOTS'):
            scan_jobs(self.root)

    def test_input_game_and_direction_remain_explicit(self):
        base = self.root / 'Fallout 4'
        self.write_tree(base, version=2)
        forward = scan_jobs(base, direction='76-to-4', include_textures=False)
        self.assertIn('SOURCE_DIRECTION_MISMATCH', ' '.join(forward[0]['diagnostics']))
        reverse = scan_jobs(base, direction='4-to-76', include_textures=False)
        self.assertNotIn('SOURCE_DIRECTION_MISMATCH', ' '.join(reverse[0]['diagnostics']))

    def test_selected_material_file_uses_only_named_asset_ancestor(self):
        base = self.root / 'Fallout 76'
        material, texture = self.write_tree(base)
        jobs = scan_jobs(material, include_textures=False)
        self.assertEqual(jobs[0]['asset_root'], str(base.resolve()))
        self.assertEqual(jobs[0]['texture_paths']['d'], str(texture.resolve()))

    def test_unrelated_parent_assets_do_not_expand_selected_resource_scope(self):
        self.write_tree(self.root / 'Unrelated')
        selected = self.root / 'Selected'
        selected.mkdir()
        # Even a parent's direct Materials/Textures pair must not be adopted
        # when the selected directory is outside those named asset trees.
        self.write_tree(self.root / 'Other')
        (self.root / 'Materials').mkdir()
        (self.root / 'Textures').mkdir()
        self.assertEqual(asset_root(selected), selected.resolve())

    def test_flat_selected_assets_keep_flat_root_and_job_identity(self):
        material = self.root / 'sample.bgsm'
        material.write_bytes(serialize(Material('BGSM', 22)))
        jobs = scan_jobs(self.root, include_textures=False)
        self.assertEqual(jobs[0]['asset_root'], str(self.root.resolve()))
        self.assertEqual(jobs[0]['relative_path'], 'materials/sample.bgsm')
        expected = hashlib.sha256((str(self.root.resolve()).lower()
            + '|materials/sample.bgsm').encode()).hexdigest()[:16]
        self.assertEqual(jobs[0]['id'], expected)


if __name__ == '__main__':
    unittest.main()
