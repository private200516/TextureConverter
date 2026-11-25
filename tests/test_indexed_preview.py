"""Topology stays indexed from NIF through IPC and is validated before GPU use."""
import dataclasses
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from nif_preview_data import load_scene, Reader, PreviewError
from preview_process import _validate_scene, read_wire, write_wire, PreviewProcessError
from test_nif_preview import fixture


class IndexedPreviewTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.nif = self.root/'quad.nif'

    def quad(self, game):
        # Two triangles reuse two corners. Include a UV seam as distinct vertex
        # data is preserved by source index rather than deduplicated by position.
        fixture(self.nif, version=game, triangles=(0,1,2, 2,1,3),
                vertex_uvs=[((-1,0,0),(0,0)),((1,0,0),(1,0)),
                            ((0,0,2),(.5,1)),((-1,0,0),(1,1))])

    def test_foldout_shares_indices_and_preserves_uv_seams_and_draw_order(self):
        for game in (130,155):
            with self.subTest(game=game):
                self.quad(game)
                scene = load_scene(self.nif)
                mesh = scene.meshes[0]
                self.assertEqual(mesh.vertices.shape,(4,12))
                self.assertEqual(mesh.indices.dtype,np.uint16)
                np.testing.assert_array_equal(mesh.indices,[[0,1,2],[2,1,3]])
                np.testing.assert_array_equal(mesh.vertices[0,:3],mesh.vertices[3,:3])
                self.assertFalse(np.array_equal(mesh.vertices[0,6:],mesh.vertices[3,6:]))
                name = 'scene'+str(game)
                write_wire(self.root, name, scene)
                decoded = _validate_scene(read_wire(self.root, name))
                self.assertEqual(decoded.triangles,2)
                copy = decoded.meshes[0]
                np.testing.assert_array_equal(copy.vertices[copy.indices],mesh.vertices[mesh.indices])

    def test_invalid_index_records_rejected_before_upload(self):
        fixture(self.nif)
        scene = load_scene(self.nif)
        mesh = scene.meshes[0]
        for bad in (np.array([[0,1,3]],dtype=np.uint16), mesh.indices.astype(np.int32),
                    mesh.indices.ravel(), np.empty((0,3),dtype=np.uint16), None):
            with self.subTest(bad=bad), self.assertRaises(PreviewProcessError):
                _validate_scene(dataclasses.replace(scene,meshes=[dataclasses.replace(mesh,indices=bad)]))
        with self.assertRaisesRegex(PreviewProcessError,'count mismatch'):
            _validate_scene(dataclasses.replace(scene,triangles=2))
        for cap in ('MAX_VERTICES','MAX_TRIANGLES'):
            with patch('nif_preview_data.'+cap,0), self.assertRaisesRegex(PreviewProcessError,'budget'):
                _validate_scene(scene)

    def test_node_arrays_obey_block_budget_before_unpack(self):
        reader = Reader(bytes(32))
        with patch('nif_preview_data.MAX_NIF_BLOCKS',2),patch.object(reader,'get') as unpack:
            with self.assertRaises(PreviewError):
                reader.array('i',3)
            unpack.assert_not_called()
        self.assertEqual(reader.array('i',2),(0,0))
