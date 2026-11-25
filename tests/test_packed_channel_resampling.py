import threading
import unittest

import numpy as np
from PIL import Image

import dds
import material
from texture_api import _baseline


class PackedChannels(unittest.TestCase):
    def test_glow_alpha_does_not_mask_smaller_smoothness_plane(self):
        light = np.zeros((2, 2, 4), np.uint8)
        light[..., 0] = [[34, 100], [170, 230]]
        reflectance = np.full((4, 4, 4), 60, np.uint8)
        actual = material.build_specular(light, reflectance)
        expected = np.asarray(Image.fromarray(light[..., 0]).resize((4, 4), Image.Resampling.LANCZOS))
        np.testing.assert_array_equal(actual[..., 1], expected)
        self.assertTrue(np.all(actual[..., 0] == 60))
        light[..., 3] = [[0, 255], [120, 1]]
        np.testing.assert_array_equal(material.build_specular(light, reflectance), actual)

    def test_glow_alpha_does_not_mask_smaller_reflection_plane(self):
        smaller = np.zeros((2, 2, 4), np.uint8)
        smaller[..., :3] = [40, 110, 220]
        light = np.full((4, 4, 4), 125, np.uint8)
        result = material.build_specular(light, smaller)
        self.assertTrue(np.all(result[..., 0] == round(40*.2126+110*.7152+220*.0722)))
        self.assertTrue(np.all(result[..., 1] == 125))

    def test_ao_keeps_diffuse_grid_and_coverage_independent_of_glow(self):
        diffuse = np.full((2, 3, 4), 200, np.uint8)
        diffuse[..., 3] = [[0, 24, 88], [100, 128, 255]]
        light = np.zeros((8, 8, 4), np.uint8)
        light[..., 1] = 128
        shaded = material.apply_ao_to_diffuse(diffuse, light, .5)
        np.testing.assert_array_equal(shaded[..., 3], diffuse[..., 3])
        self.assertTrue(np.all(shaded[..., :3] == 150))
        light[..., 3] = 255
        np.testing.assert_array_equal(material.apply_ao_to_diffuse(diffuse, light, .5), shaded)

    def test_ao_shadows_do_not_mark_bright_paint_as_metal(self):
        color = np.array([[[95,92,89,200], [17,15,12,255]]], np.uint8)
        light = np.zeros_like(color)
        refl = np.full_like(color, 68)
        base = _baseline({'d':color, 'r':refl, 'l':light},
                         {'ao_mode':True, 'ao_strength':.7, 'metal_compensation':True, 'metal_strength':.5},
                         headers={'r':{'fmt':'BC4_UNORM'}, 'd':{'fmt':'BC1_UNORM_SRGB'}}, diagnostics=[])
        shaded = material.apply_ao_to_diffuse(color, light, .7)
        np.testing.assert_array_equal(base['d'][0,0], shaded[0,0])
        self.assertGreater(base['d'][0,1,0], shaded[0,1,0])
        np.testing.assert_array_equal(base['d'][...,3], color[...,3])

    def test_cancel_before_spatial_resampling(self):
        done = threading.Event()
        done.set()
        a = np.ones((2, 2, 4), np.uint8)
        b = np.ones((4, 4, 4), np.uint8)
        with self.assertRaises(dds.CancellationRequested):
            material.build_specular(a, b, cancel_event=done)
        with self.assertRaises(dds.CancellationRequested):
            material.apply_ao_to_diffuse(a, b, .3, cancel_event=done)
