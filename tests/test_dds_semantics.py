"""DDS numeric tests on generated fixtures, with real bundled texconv if present."""
import hashlib
import struct
import tempfile
import unittest
from pathlib import Path
import numpy as np
from PIL import Image
import dds
import semantic_dds as sd

TOOL=Path(__file__).resolve().parents[1]/'tools'/'texconv.exe'


def block_signed(e0,e1,index=0):
    bits=sum(index<<(3*i) for i in range(16))
    return struct.pack('<bb',e0,e1)+bits.to_bytes(6,'little')


def snorm_fixture(path,endpoints):
    blobs=[]
    for value in endpoints:blobs.append(block_signed(value,127)+block_signed(0,127))
    payload=b''.join(blobs)
    path.write_bytes(dds._dds_header(len(endpoints)*4,4,1,'BC5_SNORM',len(payload))+payload)


class DDSSemanticTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='mc-dds-tests-');self.path=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def test_T08_snorm_axes_map_once_and_roundtrip(self):
        src=self.path/'source_n.dds';snorm_fixture(src,[-127,0,127])
        before=hashlib.sha256(src.read_bytes()).hexdigest()
        decoded=sd.decode_normal_dds(src)
        self.assertEqual(decoded[0,0,0],0)
        self.assertEqual(decoded[0,4,0],127)
        self.assertEqual(decoded[0,8,0],255)
        self.assertTrue(np.all(decoded[...,1]==127))
        target=self.path/'normal.dds'
        sd.encode_semantic_dds(decoded,'BC5_UNORM',target,mipmaps=False,semantic='normal')
        recovered=dds.decode_dds_pixels(str(target))
        np.testing.assert_array_equal(recovered[...,:2],decoded[...,:2])
        self.assertEqual(hashlib.sha256(src.read_bytes()).hexdigest(),before)
        self.assertEqual(dds.parse_dds_header(str(target)).fmt,'BC5_UNORM')

    def test_T08_minus128_clamped_before_interpolation(self):
        src=self.path/'minus128.dds'
        payload=block_signed(-128,127,2)+block_signed(0,127)
        src.write_bytes(dds._dds_header(4,4,1,'BC5_SNORM',len(payload))+payload)
        new=sd.decode_normal_dds(src);legacy=dds.decode_dds_pixels(str(src))
        # (-1 * 4 + 1)/5 = -.6 -> UNORM .2 -> 51, not 50.
        self.assertEqual(new[0,0,0],51)
        self.assertEqual(legacy[0,0,0],50)

    def test_T08_legal_tilted_normals_mips_follow_unit_vector_mean(self):
        rgba=np.full((8,8,4),255,dtype=np.uint8)
        # Alternating unit normals (.8,0,.6) and (0,.8,.6).
        rgba[::2,:,0]=230;rgba[::2,:,1]=128
        rgba[1::2,:,0]=128;rgba[1::2,:,1]=230
        levels=sd.build_semantic_mips(rgba,'normal','linear')
        np.testing.assert_array_equal(levels[0],rgba)
        xy=rgba[:2,0,:2].astype(float)/255*2-1
        xyz=np.column_stack((xy,np.sqrt(1-np.sum(xy*xy,axis=1))))
        expected=xyz.mean(axis=0);expected/=np.linalg.norm(expected)
        actual=levels[1][0,0,:2]/255*2-1
        self.assertLess(float(np.max(np.abs(actual-expected[:2]))),1/127)
        self.assertGreater(actual[0],.47) # Ordinary RG averaging incorrectly gives about .4.
        self.assertEqual([x.shape[:2] for x in levels],[(8,8),(4,4),(2,2),(1,1)])

    def test_T09_real_bc5_channels_not_swapped(self):
        rgba=np.zeros((8,8,4),dtype=np.uint8);rgba[...,3]=255
        rgba[:,:4,0]=32;rgba[:,4:,0]=224;rgba[:4,:,1]=64;rgba[4:,:,1]=192
        out=self.path/'channels.dds'
        sd.encode_semantic_dds(rgba,'BC5_UNORM',out,semantic='scalar')
        # Pillow is a separate decoder from dds.py.
        with Image.open(out) as image:independent=np.asarray(image.convert('RGBA'))
        np.testing.assert_array_equal(independent[...,:2],rgba[...,:2])
        self.assertEqual(dds.parse_dds_header(str(out)).mipmaps,4)

    def test_T11_color_mips_use_linear_light_and_alpha_is_separate(self):
        rgba=np.zeros((4,4,4),dtype=np.uint8);rgba[:,2:,:3]=255;rgba[...,3]=255
        levels=sd.build_semantic_mips(rgba,'color','srgb')
        self.assertTrue(np.all(levels[-1][...,:3]==188)) # sRGB(.5), not arithmetic 128.
        numeric=sd.build_semantic_mips(rgba,'scalar','linear')
        self.assertTrue(np.all(numeric[-1][...,:3]==128))
        rgba[:,:2,3]=0
        levels=sd.build_semantic_mips(rgba,'color','srgb')
        self.assertTrue(np.all(levels[-1][...,:3]==255))
        self.assertEqual(levels[-1][0,0,3],128)

    def test_T11_no_old_mips_and_bounded_chain_payload(self):
        baseline=np.full((16,16,4),255,dtype=np.uint8);baseline[...,:3]=0
        rebuilt=baseline.copy();rebuilt[...,:3]=150
        a=self.path/'a.dds';b=self.path/'b.dds'
        for src,dest in [(baseline,a),(rebuilt,b)]:
            sd.encode_semantic_dds(src,'BC3_UNORM',dest,semantic='color',transfer_function='srgb')
        old=sd.decode_mip_levels(a);new=sd.decode_mip_levels(b)
        self.assertEqual(len(new),5)
        for index,(x,y) in enumerate(zip(old,new)):
            self.assertGreater(int(y[...,:3].mean()),140,'old dark mip remains at '+str(index))
            self.assertLess(int(x[...,:3].mean()),8)
        corrupt=self.path/'truncated.dds';corrupt.write_bytes(b.read_bytes()[:-1])
        with self.assertRaisesRegex(ValueError,'Truncated'):sd.read_mip_payloads(corrupt)

    def test_T11_compression_boundary_increment_is_not_algorithm_leakage(self):
        base=np.zeros((16,16,4),dtype=np.uint8);base[...,:3]=37;base[...,3]=255
        y,x=np.indices((16,16));base[...,0]=37+x*2;base[...,1]=37+y*2;base[...,2]=37+x+y
        edited=base.copy();edited[3:10,3:10,:3]=180
        mask=np.zeros((16,16),bool);mask[3:10,3:10]=True
        np.testing.assert_array_equal(edited[~mask],base[~mask])
        a=self.path/'base.dds';b=self.path/'edit.dds'
        for src,dest in [(base,a),(edited,b)]:sd.encode_semantic_dds(src,'BC3_UNORM',dest,mipmaps=False,semantic='color')
        av=dds.decode_dds_pixels(str(a)).astype(int);bv=dds.decode_dds_pixels(str(b)).astype(int)
        delta=np.abs(bv-av)
        # Blocks outside the edited area's intersecting BC blocks are stable.
        unaffected=np.ones((16,16),bool);unaffected[:12,:12]=False
        self.assertEqual(int(delta[unaffected].max()),0)
        # Border pixels sharing an edited block may change under compression.
        protected_shared=(~mask)&(~unaffected)
        self.assertGreater(float(delta[protected_shared,:3].mean()),0)

    def test_T11_tiling_uniform_seam_and_exact_size(self):
        rgba=np.full((12,8,4),128,dtype=np.uint8);rgba[...,3]=255
        levels=sd.build_semantic_mips(rgba,'color','linear','wrap')
        self.assertEqual([x.shape[:2] for x in levels],[(12,8),(6,4),(3,2),(1,1)])
        for level in levels:np.testing.assert_array_equal(level[:,0],level[:,-1])
        target=self.path/'arbitrary.dds'
        sd.encode_semantic_dds(rgba,'BC3_UNORM',target,semantic='color',addressing='wrap')
        self.assertEqual((dds.parse_dds_header(str(target)).height,dds.parse_dds_header(str(target)).width),(12,8))

    def test_contract_rejects_silent_resize_or_format_downgrade(self):
        rgba=np.full((5,5,4),128,dtype=np.uint8)
        with self.assertRaisesRegex(ValueError,'no automatic resize'):
            sd.encode_semantic_dds(rgba,'BC5_UNORM',self.path/'bad.dds',semantic='normal')
        with self.assertRaisesRegex(dds.DDSError,'no silent downgrade'):
            sd.encode_semantic_dds(np.zeros((4,4,4),dtype=np.uint8),'BC7_UNORM',self.path/'bc7.dds')

    @unittest.skipUnless(TOOL.exists(),'Bundled texconv absent')
    def test_bundled_texconv_retains_prepared_normal_mips(self):
        rgba=np.full((8,8,4),255,dtype=np.uint8)
        rgba[::2,:,0]=230;rgba[::2,:,1]=128;rgba[1::2,:,0]=128;rgba[1::2,:,1]=230
        expected=sd.build_semantic_mips(rgba,'normal','linear')
        out=self.path/'native-normal.dds'
        used,warn=sd.encode_semantic_dds(rgba,'BC5_UNORM',out,texconv_path=str(TOOL),semantic='normal')
        self.assertEqual(used,'BC5_UNORM');self.assertIsNone(warn)
        levels=sd.decode_mip_levels(out)
        for actual,wanted in zip(levels[1:],expected[1:]):np.testing.assert_allclose(actual[...,:2],wanted[...,:2],atol=1)

    @unittest.skipUnless(TOOL.exists(),'Bundled texconv absent')
    def test_bundled_texconv_color_tag_does_not_double_gamma(self):
        rgba=np.full((8,8,4),128,dtype=np.uint8);rgba[...,3]=255
        for fmt in ('BC7_UNORM','BC7_UNORM_SRGB'):
            out=self.path/(fmt+'.dds')
            sd.encode_semantic_dds(rgba,fmt,out,texconv_path=str(TOOL),semantic='color',transfer_function='srgb')
            for mip in sd.decode_mip_levels(out):np.testing.assert_allclose(mip[...,:3],128,atol=1)


if __name__=='__main__':unittest.main()
