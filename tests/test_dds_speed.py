"""Codec arithmetic parity and complete-chain validation regression tests."""
import unittest,tempfile,struct
from pathlib import Path
from unittest.mock import patch
import numpy as np
import dds,semantic_dds as sd,texture_api,material
from material_codec import Material,serialize
from material_jobs import scan_jobs

class BlockParityTests(unittest.TestCase):
    def test_bc4_unsigned_signed_palette_parity(self):
        rng=np.random.default_rng(301)
        blocks=rng.integers(0,256,(1031,8),dtype=np.uint8)
        blocks[:256,0]=np.arange(256,dtype=np.uint8)
        blocks[:256,1]=np.arange(255,-1,-1,dtype=np.uint8)
        for signed in (False,True):
            actual=dds._decode_bc4_many(blocks,signed)
            expected=np.stack([dds._decode_bc4_block(x.tobytes(),signed).ravel() for x in blocks])
            np.testing.assert_array_equal(actual,expected)

    def test_bc1_transparency_and_opaque_palette_parity(self):
        blocks=np.random.default_rng(177).integers(0,256,(1031,8),dtype=np.uint8)
        blocks[0,:4]=0;blocks[1,:4]=255
        for force in (False,True):
            colors,alpha=dds._decode_bc1_many(blocks,force)
            for index,block in enumerate(blocks):
                reference,opacity=dds._decode_bc1_colors(block.tobytes(),force)
                np.testing.assert_array_equal(colors[index],reference.reshape(16,3))
                np.testing.assert_array_equal(alpha[index],255 if opacity is None else opacity.reshape(16)*255)

    def test_fallback_encoded_bytes_match_scalar_including_equal_endpoints(self):
        rng=np.random.default_rng(513)
        blocks=rng.integers(0,256,(501,16,4),dtype=np.uint8)
        for index,value in enumerate((0,1,7,127,128,254,255)):
            blocks[index]=value
        colors=dds._encode_bc1_many(blocks)
        alpha=dds._encode_bc4_many(blocks[...,3])
        for index,block in enumerate(blocks):
            self.assertEqual(colors[index].tobytes(),dds._encode_bc1_block(block.reshape(4,4,4)))
            self.assertEqual(alpha[index].tobytes(),dds._encode_bc4_block(block[...,3].reshape(4,4)))

    def test_partial_blocks_and_batch_boundary_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'edge.dds'
            for width,height in ((1,1),(7,13),(257,133)):
                bw,bh=dds._blocks_dims(width,height)
                raw=np.random.default_rng(width).integers(0,256,(bh*bw,16),dtype=np.uint8)
                path.write_bytes(dds._dds_header(width,height,1,'BC3_UNORM',raw.nbytes)+raw.tobytes())
                actual=dds.decode_dds_pixels(path)
                expected=np.zeros((bh*4,bw*4,4),np.uint8)
                for index,block in enumerate(raw):
                    y,x=divmod(index,bw)
                    expected[y*4:y*4+4,x*4:x*4+4,:3]=dds._decode_bc1_colors(block[8:].tobytes(),True)[0].astype(np.uint8)
                    expected[y*4:y*4+4,x*4:x*4+4,3]=dds._decode_bc4_block(block[:8].tobytes(),False).astype(np.uint8)
                np.testing.assert_array_equal(actual,expected[:height,:width])

    def test_normal_signed_endpoint_correction_matches_scalar(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'signed.dds';rng=np.random.default_rng(311)
            raw=rng.integers(0,256,(129,16),dtype=np.uint8)
            raw[:3,0]=128;raw[:3,1]=(129,128,0)
            path.write_bytes(dds._dds_header(516,4,1,'BC5_SNORM',raw.nbytes)+raw.tobytes())
            actual=sd.decode_normal_dds(path)
            for index,block in enumerate(raw):
                for channel in range(2):
                    b=block[channel*8:channel*8+8].tobytes();e0,e1=struct.unpack('<bb',b[:2]);v0,v1=max(e0,-127),max(e1,-127)
                    palette=([v0,v1]+[((7-i)*v0+i*v1)/7 for i in range(1,7)]) if e0>e1 else ([v0,v1]+[((5-i)*v0+i*v1)/5 for i in range(1,5)]+[-127,127])
                    bits=int.from_bytes(b[2:],'little');values=np.array([palette[(bits>>(3*i))&7] for i in range(16)]).reshape(4,4)
                    reference=np.clip((values/127+1)*127.5,0,255).astype(np.uint8)
                    np.testing.assert_array_equal(actual[:,index*4:index*4+4,channel],reference)

    def test_layout_rejects_trailing_and_truncated_lower_mip(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'mips.dds';a=np.zeros((8,8,4),np.uint8)
            dds.encode_fallback(a,'BC5_UNORM',str(path));good=path.read_bytes()
            sd.validate_mip_layout(path)
            for bad in (good[:-1],good+b'garbage'):
                path.write_bytes(bad)
                with self.assertRaises(ValueError):sd.validate_mip_layout(path)

    def test_specular_formula_and_rounding_unchanged(self):
        rng=np.random.default_rng(772);l=rng.integers(0,256,(193,127,4),dtype=np.uint8);r=rng.integers(0,256,l.shape,dtype=np.uint8)
        lf=l.astype(np.float64)/255;rf=r.astype(np.float64)/255;old=np.zeros(l.shape,np.float64)
        old[...,0]=material.luminance709(rf[...,:3]);old[...,1]=lf[...,0];old[...,3]=1
        np.testing.assert_array_equal(material.build_specular(l,r),material.to_uint8(old))

    def test_off_export_avoids_reconstruction_and_skips_unused_masks(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'textures').mkdir();(root/'materials').mkdir()
            a=np.full((8,8,4),153,np.uint8)
            for role in ('d','r','l'):dds.encode_fallback(a,'BC3_UNORM',str(root/'textures'/('metal_'+role+'.dds')))
            fields=dict(DiffuseTexture='textures/metal_d.dds',SpecularTexture='textures/metal_r.dds',LightingTexture='textures/metal_l.dds')
            (root/'materials/a.bgsm').write_bytes(serialize(Material('BGSM',22,fields)))
            job=scan_jobs(root)[0];opts={'mode':'off'}
            preview=texture_api._prepare(job,opts,'preview')[2]
            with patch('texture_api.reconstruct',side_effect=AssertionError('disabled export must not reconstruct')):
                export=texture_api._prepare(job,opts,'export')[2]
            expected=dict(preview.report,intent='export',protection_coverage=None,masks_evaluated=False)
            self.assertEqual(export.report,expected)
            self.assertIsNone(export.diffuse)
            with self.assertRaises(ValueError):texture_api._prepare(job,dict(opts,reconstruction_strength=float('nan')),'export')
            skipped=texture_api._prepare(job,dict(opts,protect_mask=np.full((8,8),float('nan')),
                                               edit_mask_path=str(root/'missing.png')),'export')[2]
            self.assertFalse(skipped.report['masks_evaluated'])
            self.assertIsNone(skipped.report['protection_coverage'])

    def test_texconv_temp_directory_removed_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory)/'work';work.mkdir()
            with patch('dds.tempfile.mkdtemp',return_value=str(work)),patch('dds._run_texconv',side_effect=dds.DDSError('texconv timed out')):
                with self.assertRaises(dds.DDSError):dds.encode_with_texconv('unused',np.zeros((4,4,4),np.uint8),'BC3_UNORM',str(Path(directory)/'out.dds'))
            self.assertFalse(work.exists())

if __name__=='__main__':unittest.main()
