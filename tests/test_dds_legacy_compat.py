"""Independent raw DDS fixtures; run only in the approved offline guest."""
import hashlib
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np
import dds
import semantic_dds


def legacy(bits, masks, pixels, width=2, height=1, mips=1, pf_flags=0x41,
           pitch=None, linear_size=False):
    # No production header writer is used, so channel and pitch expectations
    # are independent of the implementation under test.
    row = width * bits // 8 if pitch is None else pitch
    flags = 0x1007 | (0x80000 if linear_size else 0x8)
    if mips > 1: flags |= 0x20000
    field = width * height * bits // 8 if linear_size else row
    values = [124, flags, height, width, field, 0, mips] + [0] * 11
    values += [32, pf_flags, 0, bits, *masks]
    values += [0x1000 | (0x400008 if mips > 1 else 0), 0, 0, 0, 0]
    return b'DDS ' + struct.pack('<31I', *values) + pixels


RGBA = (0xff, 0xff00, 0xff0000, 0xff000000)
BGRA = (0xff0000, 0xff00, 0xff, 0xff000000)
BGR = (0xff0000, 0xff00, 0xff, 0)


class LegacyDdsCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def check(self, data, expected, fmt, levels=False):
        path = self.root / '中文 legacy.dds'; path.write_bytes(data)
        before = hashlib.sha256(data).hexdigest()
        with patch.object(dds, 'Image', None):
            info = dds.parse_dds_header(str(path)); self.assertEqual(info.fmt, fmt)
            self.assertEqual(info.data_offset, 128)
            actual = semantic_dds.decode_mip_levels(path) if levels else dds.decode_dds_pixels(str(path), info)
        if levels:
            self.assertEqual(len(actual), len(expected))
            for image, wanted in zip(actual, expected):
                np.testing.assert_array_equal(image, np.array(wanted, np.uint8))
        else:
            np.testing.assert_array_equal(actual, np.array(expected, np.uint8))
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_legacy_rgba32_masks_keep_samples_in_rgba_order(self):
        self.check(legacy(32, RGBA, bytes((13, 71, 203, 19, 241, 2, 87, 222))),
                   [[[13, 71, 203, 19], [241, 2, 87, 222]]], 'R8G8B8A8_UNORM')

    def test_legacy_bgra32_masks_swizzle_once_and_keep_alpha(self):
        self.check(legacy(32, BGRA, bytes((203, 71, 13, 19, 87, 2, 241, 222))),
                   [[[13, 71, 203, 19], [241, 2, 87, 222]]], 'B8G8R8A8_UNORM')

    def test_rgbx_declares_opaque_alpha_instead_of_padding(self):
        for masks, raw, fmt in ((RGBA[:3] + (0,), bytes((13, 71, 203, 0, 241, 2, 87, 42)), 'R8G8B8A8_UNORM'),
                                (BGR, bytes((203, 71, 13, 0, 87, 2, 241, 42)), 'B8G8R8A8_UNORM')):
            with self.subTest(fmt=fmt):
                self.check(legacy(32, masks, raw, pf_flags=0x40),
                           [[[13, 71, 203, 255], [241, 2, 87, 255]]], fmt)

    def test_bgr24_uses_three_bytes_and_opaque_alpha(self):
        self.check(legacy(24, BGR, bytes((203, 71, 13, 87, 2, 241)), pf_flags=0x40),
                   [[[13, 71, 203, 255], [241, 2, 87, 255]]], 'B8G8R8_UNORM')

    def test_luminance8_replicates_sample_without_gamma_change(self):
        self.check(legacy(8, (0xff, 0, 0, 0), bytes((7, 203)), pf_flags=0x20000),
                   [[[7, 7, 7, 255], [203, 203, 203, 255]]], 'R8_UNORM')

    def test_linear_size_is_not_treated_as_row_pitch(self):
        raw = bytes((3, 2, 1, 6, 5, 4, 9, 8, 7, 12, 11, 10))
        self.check(legacy(24, BGR, raw, height=2, pf_flags=0x40, linear_size=True),
                   [[[1, 2, 3, 255], [4, 5, 6, 255]], [[7, 8, 9, 255], [10, 11, 12, 255]]],
                   'B8G8R8_UNORM')

    def test_padded_single_mip_bgr_rows_decode_without_crossing_padding(self):
        data = legacy(24, BGR, bytes((3, 2, 1, 99, 6, 5, 4, 88)), width=1,
                      height=2, pf_flags=0x40, pitch=4)
        expected = [[[1, 2, 3, 255]], [[4, 5, 6, 255]]]
        self.check(data, expected, 'B8G8R8_UNORM')
        self.check(data, [expected], 'B8G8R8_UNORM', levels=True)
        self.assertEqual(semantic_dds.validate_mip_layout(self.root / '中文 legacy.dds')[1], [(1, 2, 128, 8)])

    def test_bgr24_all_mip_levels_keep_channels(self):
        raw = bytes((3, 2, 1, 6, 5, 4, 9, 8, 7, 12, 11, 10, 15, 14, 13))
        self.check(legacy(24, BGR, raw, height=2, mips=2, pf_flags=0x40, linear_size=True),
                   [[[[1, 2, 3, 255], [4, 5, 6, 255]], [[7, 8, 9, 255], [10, 11, 12, 255]]],
                    [[[13, 14, 15, 255]]]], 'B8G8R8_UNORM', levels=True)

    def test_rgbx_all_mip_levels_keep_opaque_alpha(self):
        raw = bytes((1, 2, 3, 0, 4, 5, 6, 42, 7, 8, 9, 99, 10, 11, 12, 1, 13, 14, 15, 0))
        self.check(legacy(32, RGBA[:3] + (0,), raw, height=2, mips=2, pf_flags=0x40),
                   [[[[1, 2, 3, 255], [4, 5, 6, 255]], [[7, 8, 9, 255], [10, 11, 12, 255]]],
                    [[[13, 14, 15, 255]]]], 'R8G8B8A8_UNORM', levels=True)

    def test_unknown_masks_and_inconsistent_alpha_are_rejected(self):
        cases = ((32, (0, 0, 0, 0), 0x40), (32, RGBA[:3] + (0,), 0x41),
                 (32, RGBA, 0x40), (24, BGR, 0x41), (8, (0x0f, 0, 0, 0), 0x20000))
        for bits, masks, flags in cases:
            with self.subTest(bits=bits, masks=masks, flags=flags):
                path = self.root / 'invalid.dds'; path.write_bytes(legacy(bits, masks, b'\0' * 8, pf_flags=flags))
                with self.assertRaises(dds.DDSError): dds.parse_dds_header(str(path))

    def test_truncated_byte_payload_and_extra_mip_data_are_rejected(self):
        path = self.root / 'truncated.dds'
        path.write_bytes(legacy(24, BGR, bytes((3, 2, 1, 6, 5)), pf_flags=0x40))
        with self.assertRaises(dds.DDSError): dds.decode_dds_pixels(str(path))
        with self.assertRaises(ValueError): semantic_dds.validate_mip_layout(path)
        path.write_bytes(legacy(24, BGR, bytes((3, 2, 1, 6, 5, 4, 99)), pf_flags=0x40))
        with self.assertRaisesRegex(ValueError, 'Unexpected payload'): semantic_dds.validate_mip_layout(path)

    def test_ambiguous_padded_multi_mip_chain_is_explicitly_rejected(self):
        path = self.root / 'ambiguous.dds'
        path.write_bytes(legacy(24, BGR, b'\0' * 20, height=2, mips=2, pf_flags=0x40, pitch=8))
        with self.assertRaisesRegex(ValueError, 'ambiguous lower-level'):
            semantic_dds.validate_mip_layout(path)

    def test_byte_decoder_cancel_never_falls_back_to_pillow(self):
        path = self.root / 'cancel.dds'; path.write_bytes(legacy(24, BGR, b'\0' * 6, pf_flags=0x40))
        event = threading.Event(); event.set()
        with patch.object(dds, 'Image', None), self.assertRaises(dds.CancellationRequested):
            dds.decode_dds_pixels(str(path), cancel_event=event)
        with self.assertRaises(dds.CancellationRequested):
            semantic_dds.decode_mip_levels(path, cancel_event=event)


if __name__ == '__main__':
    unittest.main()
