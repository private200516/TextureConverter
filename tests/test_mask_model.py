import tempfile
from pathlib import Path
import unittest
import numpy as np
from PIL import Image
from mask_model import MaskModel

class MaskTests(unittest.TestCase):
    def test_protection_wins_and_undo(self):
        m=MaskModel((16,24),np.ones((16,24)))
        m.checkpoint('protect');m.brush('protect',10,8,3)
        self.assertEqual(m.final()[8,10],0)
        self.assertTrue(m.undo());self.assertEqual(m.final()[8,10],1)
    def test_size_mismatch_does_not_mutate(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'mask.png';Image.new('L',(4,4),255).save(p)
            m=MaskModel((16,16));before=m.layers['edit'].copy()
            with self.assertRaises(ValueError):m.load('edit',p)
            np.testing.assert_array_equal(before,m.layers['edit'])
    def test_gray_roundtrip_and_reject_rgb(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'mask.png';m=MaskModel((16,16));m.brush('edit',8,8,5);m.save('edit',p)
            n=MaskModel((16,16));n.load('edit',p);np.testing.assert_array_equal(m.layers['edit'],n.layers['edit'])
            Image.new('RGB',(16,16),'white').save(p)
            with self.assertRaises(ValueError):n.load('edit',p)
    def test_operations(self):
        candidate=np.eye(4,dtype=np.float32);m=MaskModel((4,4),candidate)
        m.action('edit','invert');np.testing.assert_array_equal(m.layers['edit'],255-(candidate*255).astype(np.uint8))
        m.action('edit','clear');self.assertFalse(m.layers['edit'].any())
        m.action('edit','restore');np.testing.assert_array_equal(m.layers['edit'],candidate*255)
    def test_undo_budget_bounded(self):
        m=MaskModel((16,16),undo_limit=600)
        for i in range(20):m.action('edit','invert')
        self.assertLessEqual(m.undo_bytes,600)

if __name__=='__main__':unittest.main()
