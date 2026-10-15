"""Independent small binary fixtures for the local, read-only model preview."""
import hashlib
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
import numpy as np
import dds
from material_codec import Material, serialize
from nif_preview_data import (load_scene, load_preview_texture, ResourceResolver,
                              clean_reference, PreviewError, NifReader)

P = lambda fmt,*values: struct.pack('<'+fmt,*values)
def string(value):
    data=value.encode('utf8');return P('I',len(data))+data
IDENTITY=(0.,0.,0.,1.,0.,0.,0.,1.,0.,0.,0.,1.,1.)

def fixture(path, version=130, reference='Materials/Test/sample.bgsm', transform=None, triangles=None):
    strings=['Scene','Triangle',reference]
    def net(name): return P('iIi',name,0,-1)
    def av(name, matrix): return net(name)+P('I13fi',14,*matrix,-1)
    root=av(0,transform or IDENTITY)+P('Ii',1,1)
    descriptor=7|(4<<8)|(5<<16)|(6<<24)|(0x42B<<44)
    raw=b''.join(P('4f2e4B4B',*point,0.,*uv,128,0,128,0,255,255,255,255)
                 for point,uv in [((-1.,0.,0.),(0.,0.)),((1.,0.,0.),(1.,0.)),((0.,0.,2.),(.5,1.))])
    raw+=P('3H',*(triangles or (0,1,2)))
    shape=av(1,IDENTITY)+P('4f',0.,0.,0.,2.)
    if version==155:shape+=P('6f',0.,0.,0.,2.,2.,2.)
    shape+=P('iiiQIHI',-1,2,-1,descriptor,1,3,len(raw))+raw
    shader=net(2)
    if version==130:shader=P('I',0)+shader
    types=['NiNode','BSTriShape','BSLightingShaderProperty']
    blocks=[root,shape,shader]
    header=b'Gamebryo File Format, Version 20.2.0.7\n'+P('IBIII',0x14020007,1,12,3,version)
    header+=b'\0'+(P('I',0) if version>130 else b'\0')+b'\0\0'
    header+=P('H',3)+b''.join(string(s) for s in types)+P('3H',0,1,2)+P('3I',*[len(b) for b in blocks])
    header+=P('II',len(strings),max(map(len,strings)))+b''.join(string(s) for s in strings)+P('I',0)
    path.write_bytes(header+b''.join(blocks)+P('Ii',1,0))

def write_dds(path, color, size=8, mipmaps=False):
    path.parent.mkdir(parents=True,exist_ok=True)
    levels=[]
    while True:
        pixels=np.empty((size,size,4),dtype=np.uint8);pixels[:]=color;levels.append(pixels)
        if not mipmaps or size==1:break
        size=max(1,size//2)
    path.write_bytes(dds._dds_header(levels[0].shape[1],levels[0].shape[0],len(levels),'R8G8B8A8_UNORM',levels[0].nbytes)+b''.join(p.tobytes() for p in levels))

def write_material(root, color, kind='BGSM'):
    extension='.bgsm' if kind=='BGSM' else '.bgem'
    path=root/'materials/test'/('sample'+extension);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(serialize(Material(kind,2,{'DiffuseTexture' if kind=='BGSM' else 'BaseTexture':'Test/color.dds','SpecularEnabled':True})))
    write_dds(root/'textures/test/color.dds',color)
    return path

class PreviewDataTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='nif-preview-test-');self.base=Path(self.temp.name)
        self.nif=self.base/'sample.nif';fixture(self.nif)
        self.source=self.base/'source';self.output=self.base/'run'/'converted'
    def tearDown(self):self.temp.cleanup()
    def test_geometry_uv_and_parent_transform_both_versions(self):
        transform=(10.,20.,30.,1.,0.,0.,0.,1.,0.,0.,0.,1.,2.)
        for version in (130,155):
            fixture(self.nif,version,transform=transform)
            scene=load_scene(self.nif)
            np.testing.assert_allclose(scene.meshes[0].vertices[:,:3],[[8,20,30],[12,20,30],[10,20,34]])
            np.testing.assert_allclose(scene.meshes[0].vertices[:,6:8],[[0,0],[1,0],[.5,1]])
            self.assertEqual(scene.triangles,1)
    def test_converted_root_priority_and_no_source_mutation(self):
        original=write_material(self.source,(255,0,0,255))
        write_material(self.output,(0,255,0,255))
        before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in [self.nif,original,self.source/'textures/test/color.dds']}
        scene=load_scene(self.nif,self.output.parent,self.source)
        mat=scene.meshes[0].material
        self.assertEqual(mat.source,'converted')
        np.testing.assert_array_equal(mat.textures['base'][1][0,0],[0,255,0,255])
        for p,digest in before.items():self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),digest)
    def test_source_fallback_is_explicit(self):
        write_material(self.source,(255,0,0,255))
        scene=load_scene(self.nif,self.output,self.source)
        self.assertEqual(scene.meshes[0].material.source,'source fallback')
        self.assertTrue(any('source fallback/base' in d for d in scene.diagnostics))
    def test_missing_material_keeps_geometry_and_reports_reference(self):
        scene=load_scene(self.nif)
        self.assertEqual(len(scene.meshes),1)
        self.assertTrue(any('Missing material' in d for d in scene.diagnostics))
        self.assertFalse(scene.meshes[0].material.textures)
    def test_bgem_external_material(self):
        fixture(self.nif,reference='Materials/Test/sample.bgem')
        write_material(self.output,(3,40,160,255),'BGEM')
        scene=load_scene(self.nif,self.output)
        self.assertEqual(scene.meshes[0].material.kind,'BGEM')
        np.testing.assert_array_equal(scene.meshes[0].material.textures['base'][1][0,0],[3,40,160,255])
    def test_reference_traversal_absolute_unc_and_drive_rejected(self):
        for ref in ('../outside.dds','textures/../../outside.dds','C:/outside.dds','//server/share.dds','/outside.dds'):
            with self.subTest(ref=ref),self.assertRaises(PreviewError):clean_reference(ref,'textures')
    def test_editor_absolute_reference_uses_virtual_suffix_only(self):
        write_material(self.output,(9,8,7,255))
        fixture(self.nif,reference='C:/Projects/Fallout4/Data/Materials/Test/sample.bgsm')
        scene=load_scene(self.nif,self.output)
        self.assertEqual(scene.meshes[0].material.source,'converted')
        np.testing.assert_array_equal(scene.meshes[0].material.textures['base'][1][0,0],[9,8,7,255])
    def test_unsafe_nif_material_reference_rejected(self):
        fixture(self.nif,reference='../secret.bgsm')
        with self.assertRaises(PreviewError):load_scene(self.nif,self.output,self.source)
    def test_bad_index_rejected(self):
        fixture(self.nif,triangles=(0,1,60000))
        with self.assertRaises(PreviewError):load_scene(self.nif)
    def test_truncated_nif_rejected(self):
        self.nif.write_bytes(self.nif.read_bytes()[:-10])
        with self.assertRaises(PreviewError):NifReader(self.nif)
    def test_nif_size_and_geometry_budget(self):
        with patch('nif_preview_data.MAX_NIF_BYTES',16),self.assertRaises(PreviewError):NifReader(self.nif)
        with patch('nif_preview_data.MAX_TRIANGLES',0),self.assertRaises(PreviewError):load_scene(self.nif)
    def test_cancel_before_open(self):
        cancel=threading.Event();cancel.set()
        with self.assertRaises(InterruptedError):load_scene(self.base/'does-not-exist.nif',cancel=cancel)
    def test_existing_mip_selected_without_full_resolution_decode(self):
        path=self.base/'mipped.dds';write_dds(path,(10,20,30,255),64,True)
        result=load_preview_texture(path,max_side=16)
        self.assertEqual(result.shape,(16,16,4))
        np.testing.assert_array_equal(result[0,0],[10,20,30,255])
    def test_srgb_bc1_decodes_stored_samples(self):
        path=self.base/'srgb.dds'
        path.write_bytes(dds._dds_header(4,4,1,'BC1_UNORM_SRGB',8)+P('HHI',0xf800,0,0))
        result=load_preview_texture(path)
        np.testing.assert_array_equal(result[0,0],[255,0,0,255])
    def test_cube_texture_rejected(self):
        path=self.base/'cube.dds';write_dds(path,(0,0,0,255))
        data=bytearray(path.read_bytes());struct.pack_into('<I',data,112,0x200);path.write_bytes(data)
        with self.assertRaises(PreviewError):load_preview_texture(path)
    def test_root_folder_accepts_materials_subdirectory(self):
        write_material(self.output,(5,6,7,255))
        resolver=ResourceResolver(self.output/'materials')
        path,label=resolver.resolve('Data/Materials/Test/sample.bgsm','materials')
        self.assertTrue(path.is_file());self.assertEqual(label,'converted')

if __name__=='__main__':unittest.main()
