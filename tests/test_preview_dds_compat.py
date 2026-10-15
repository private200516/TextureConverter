"""Preview regressions for independently authored legacy DDS byte layouts."""
import hashlib
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np
from nif_preview_data import load_preview_texture, PreviewError


def legacy(width, height, bits, flags, masks, payload, mipmaps=1, pitch=0):
    header_flags = 0x1007 | (0x20000 if mipmaps > 1 else 0) | (8 if pitch else 0)
    fields = [124, header_flags, height, width, pitch, 0, mipmaps] + [0] * 11
    fields += [32, flags, 0, bits, *masks]
    fields += [0x1000 | (0x400008 if mipmaps > 1 else 0), 0, 0, 0, 0]
    return b'DDS ' + struct.pack('<31I', *fields) + payload


class LegacyPreviewCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / '中文 预览.dds'

    def tearDown(self):
        self.temp.cleanup()

    def preview(self, data, **options):
        self.path.write_bytes(data)
        before = hashlib.sha256(data).hexdigest()
        with patch('nif_preview_data.Image.open', side_effect=AssertionError('No Pillow DDS fallback')):
            result = load_preview_texture(self.path, **options)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        return result

    def test_rgba_masks_preserve_red_blue_and_alpha(self):
        data = legacy(1, 1, 32, 0x41, (0xff, 0xff00, 0xff0000, 0xff000000), bytes((13, 71, 203, 19)))
        np.testing.assert_array_equal(self.preview(data)[0, 0], [13, 71, 203, 19])

    def test_rgbx_padding_is_opaque(self):
        data = legacy(1, 1, 32, 0x40, (0xff0000, 0xff00, 0xff, 0), bytes((203, 71, 13, 0)))
        np.testing.assert_array_equal(self.preview(data)[0, 0], [13, 71, 203, 255])

    def test_bgr24_selects_existing_smallest_mip(self):
        payload = bytes((1, 2, 3)) * 16 + bytes((11, 12, 13)) * 4 + bytes((21, 22, 23))
        data = legacy(4, 4, 24, 0x40, (0xff0000, 0xff00, 0xff, 0), payload, mipmaps=3)
        result = self.preview(data, max_side=1)
        self.assertEqual(result.shape, (1, 1, 4))
        np.testing.assert_array_equal(result[0, 0], [23, 22, 21, 255])

    def test_luminance_replicates_samples_to_rgb(self):
        data = legacy(2, 1, 8, 0x20000, (0xff, 0, 0, 0), bytes((0, 239)))
        np.testing.assert_array_equal(self.preview(data), [[[0, 0, 0, 255], [239, 239, 239, 255]]])

    def test_explicit_padded_rows_do_not_become_pixels(self):
        data = legacy(1, 2, 24, 0x40, (0xff0000, 0xff00, 0xff, 0),
                      bytes((203, 71, 13, 99, 87, 2, 241, 88)), pitch=4)
        np.testing.assert_array_equal(self.preview(data), [[[13, 71, 203, 255]], [[241, 2, 87, 255]]])

    def test_unknown_masks_rejected_and_cancel_is_preview_interruption(self):
        self.path.write_bytes(legacy(1, 1, 32, 0x40, (1, 2, 4, 0), bytes(4)))
        with self.assertRaises(PreviewError):
            load_preview_texture(self.path)
        event = threading.Event(); event.set()
        with self.assertRaises(InterruptedError):
            load_preview_texture(self.path, event)


if __name__ == '__main__':
    unittest.main()
