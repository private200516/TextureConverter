"""Regression fixtures for byte-equivalent DXGI uncompressed sRGB aliases."""
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import dds


def fixture(dxgi, payload):
    # Independent 2x1 DX10 RGBA/BGRA header, one mip and one ordinary 2D image.
    fields = [124, 0x100F, 1, 2, 8, 0, 1] + [0] * 11
    fields += [32, 4, int.from_bytes(b'DX10', 'little'), 0, 0, 0, 0, 0]
    fields += [0x1000, 0, 0, 0, 0]
    return b'DDS ' + struct.pack('<31I', *fields) + struct.pack('<5I', dxgi, 3, 0, 1, 0) + payload


class SrgbUncompressedCompatibilityTests(unittest.TestCase):
    def test_rgba_alias_preserves_samples_alpha_and_transfer_tag(self):
        raw = bytes((13, 71, 203, 19, 241, 2, 87, 222))
        expected = np.array([[[13, 71, 203, 19], [241, 2, 87, 222]]], dtype=np.uint8)
        self._check_alias(29, 'R8G8B8A8_UNORM_SRGB', raw, expected)

    def test_bgra_alias_swizzles_channels_without_gamma_or_alpha_changes(self):
        raw = bytes((203, 71, 13, 19, 87, 2, 241, 222))
        expected = np.array([[[13, 71, 203, 19], [241, 2, 87, 222]]], dtype=np.uint8)
        self._check_alias(91, 'B8G8R8A8_UNORM_SRGB', raw, expected)

    def _check_alias(self, dxgi, tag, raw, expected):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / '中文 sRGB 纹理.dds'
            payload = fixture(dxgi, raw)
            path.write_bytes(payload)
            before = hashlib.sha256(payload).hexdigest()
            # These byte layouts must not need a Pillow DXGI implementation.
            with patch.object(dds, 'Image', None):
                info = dds.parse_dds_header(str(path))
                self.assertEqual(info.fmt, tag)
                self.assertEqual(info.data_offset, 148)
                self.assertTrue(info.dx10)
                actual = dds.decode_dds_pixels(str(path), info)
            np.testing.assert_array_equal(actual, expected)
            self.assertEqual(actual.dtype, np.dtype('uint8'))
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
            self.assertEqual(dds.parse_dds_header(str(path)).fmt, tag)


if __name__ == '__main__':
    unittest.main()
