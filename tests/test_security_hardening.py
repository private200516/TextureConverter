"""Owned offline fixtures for file, decoder and worker security boundaries."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import nullcontext
from unittest.mock import patch
import numpy as np
from PIL import Image, UnidentifiedImageError
import dds
import semantic_dds
import texture_api
from config import Config
from material_jobs import scan_jobs, safe_relative
from nif_preview_data import NifReader, PreviewError, clean_reference
from preview_process import (load_scene_isolated, preview_job_isolated, read_wire,
                             write_wire, PreviewProcessError)
import preview_process
import security_io
from test_nif_preview import fixture, write_material


class OwnedFiles(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='texture-security-test-')
        self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def junction(self,link,target):
        if os.name=='nt':
            result=subprocess.run([os.environ.get('COMSPEC','C:/Windows/System32/cmd.exe'),'/d','/c','mklink','/J',str(link),str(target)],
                                  stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,shell=False)
            if result.returncode:self.skipTest('Cannot create owned junction fixture')
            self.addCleanup(lambda:link.rmdir() if link.exists() else None)
        else:
            link.symlink_to(target,target_is_directory=True)
            self.addCleanup(lambda:link.unlink(missing_ok=True))
    def job(self):
        source=self.root/'source';(source/'textures').mkdir(parents=True)
        dds.encode_fallback(np.full((8,8,4),100,np.uint8),'BC3_UNORM',str(source/'textures/test_d.dds'))
        return scan_jobs(source)[0]


class PathBoundaries(OwnedFiles):
    def test_windows_device_ads_and_normalization_aliases_rejected(self):
        for value in ('CON','NUL.dds','textures/COM1.dds','AUX.bgsm','LPT².dds','textures/file.dds:stream',
                      'textures/name.','textures/name ','textures//x.dds','textures/./x.dds','textures/../x.dds',
                      '\\\\?\\C:\\outside.dds','textures/evil\0.dds','textures/a?.dds'):
            with self.subTest(value=value),self.assertRaises(ValueError):safe_relative(value)
        self.assertEqual(safe_relative('textures/普通贴图_d.dds'),'textures/普通贴图_d.dds')
        with self.assertRaises(PreviewError):clean_reference('Textures/NUL.dds','textures')
    def test_job_identifier_cannot_escape_manifest_or_preview_directory(self):
        job=self.job();job['id']='../../owned'
        output=self.root/'output'
        with self.assertRaisesRegex(ValueError,'identifier'):texture_api.run_job(job,output,options={'mode':'off','texconv_path':False})
        self.assertFalse(output.exists())
    def test_source_junction_rejected_after_scan(self):
        job=self.job();textures=Path(job['asset_root'])/'textures';textures.rename(textures.with_name('real'))
        self.junction(textures,textures.with_name('real'))
        with self.assertRaisesRegex(ValueError,'Reparse|symlink'):texture_api.run_job(job,self.root/'output',options={'mode':'off','texconv_path':False})
        self.assertFalse((self.root/'output').exists())
    def test_output_junction_cannot_redirect_publication(self):
        job=self.job();output=self.root/'output';outside=self.root/'outside'
        output.mkdir();outside.mkdir();(outside/'sentinel').write_bytes(b'unchanged')
        self.junction(output/'textures',outside)
        with self.assertRaisesRegex(ValueError,'Reparse|symlink'):texture_api.run_job(job,output,options={'mode':'off','texconv_path':False})
        self.assertEqual([p.name for p in outside.iterdir()],['sentinel'])
        self.assertEqual((outside/'sentinel').read_bytes(),b'unchanged')
    @unittest.skipUnless(os.name=='nt','Windows name sharing boundary')
    def test_pinned_directory_cannot_be_replaced_during_commit(self):
        directory=self.root/'output';directory.mkdir()
        with security_io.pinned_path(directory):
            with self.assertRaises(PermissionError):directory.rename(self.root/'moved')
        directory.rename(self.root/'moved')
    @unittest.skipUnless(os.name=='nt','Windows source sharing boundary')
    def test_open_source_excludes_writer_and_replacement(self):
        source=self.root/'source.dds';source.write_bytes(b'original')
        with security_io.open_read(source) as stream:
            with self.assertRaises(PermissionError):source.open('wb')
            with self.assertRaises(PermissionError):source.rename(self.root/'moved')
            self.assertEqual(stream.read(),b'original')
    def test_exclusive_publication_preserves_racing_destination(self):
        source=self.root/'source';target=self.root/'target'
        source.write_bytes(b'new');target.write_bytes(b'prior')
        with self.assertRaises(FileExistsError):security_io.publish_new(source,target)
        self.assertEqual(target.read_bytes(),b'prior')
    def test_material_stage_hardlink_is_not_truncated(self):
        from material_codec import Material,serialize
        job=self.job();source=Path(job['asset_root']);(source/'materials').mkdir()
        (source/'materials/test.bgsm').write_bytes(serialize(Material('BGSM',22)))
        material=scan_jobs(source,include_textures=False)[0]
        outside=self.root/'outside';outside.write_bytes(b'original')
        original=tempfile.mkdtemp
        def stage(*args,**kwargs):
            directory=Path(original(*args,**kwargs));(directory/'materials').mkdir()
            os.link(outside,directory/'materials/test.bgsm');return str(directory)
        with patch('texture_api.tempfile.mkdtemp',side_effect=stage):
            with self.assertRaises(FileExistsError):texture_api.run_job(material,self.root/'output',options={'mode':'off','texconv_path':False})
        self.assertEqual(outside.read_bytes(),b'original')
    def test_semantic_scratch_hardlink_is_not_truncated(self):
        outside=self.root/'outside';outside.write_bytes(b'original');target=self.root/'prepared.dds';os.link(outside,target)
        with self.assertRaises(FileExistsError):semantic_dds._write_uncompressed([np.zeros((4,4,4),np.uint8)],target)
        self.assertEqual(outside.read_bytes(),b'original')
    def test_texconv_cross_volume_publication_cannot_truncate_hardlink(self):
        outside=self.root/'outside';outside.write_bytes(b'original');target=self.root/'out.dds';os.link(outside,target)
        work=self.root/'scratch';work.mkdir()
        def complete(command,cancel_event=None):
            dds.encode_fallback(np.zeros((4,4,4),np.uint8),'BC3_UNORM',str(work/'out.dds'),False)
            return subprocess.CompletedProcess(command,0,'','')
        with patch('dds.tempfile.mkdtemp',return_value=str(work)),patch('dds._run_texconv',side_effect=complete):
            with self.assertRaises(FileExistsError):dds.encode_with_texconv('unused',np.zeros((4,4,4),np.uint8),'BC3_UNORM',str(target),False)
        self.assertEqual(outside.read_bytes(),b'original')
    def test_fallback_replaces_name_without_truncating_hardlink_referent(self):
        outside=self.root/'outside';outside.write_bytes(b'original');target=self.root/'out.dds';os.link(outside,target)
        dds.encode_fallback(np.zeros((4,4,4),np.uint8),'BC3_UNORM',str(target),False)
        self.assertEqual(outside.read_bytes(),b'original');self.assertFalse(target.samefile(outside))


class AllocationBoundaries(OwnedFiles):
    def test_scan_stops_with_explicit_entry_budget(self):
        for index in range(5):(self.root/(str(index)+'.txt')).write_text('owned fixture')
        with patch('material_jobs.MAX_SCAN_ENTRIES',3):
            with self.assertRaisesRegex(ValueError,'SCAN_ENTRY_LIMIT'):scan_jobs(self.root)
    def test_nif_reader_bounds_actual_read_without_trusting_stat(self):
        class Growing(io.BytesIO):
            def read(self,size=-1):
                if not 0<=size<=17:raise AssertionError('unbounded NIF read')
                return super().read(size)
        with patch('nif_preview_data.MAX_NIF_BYTES',16),patch('security_io.open_read',return_value=nullcontext(Growing(b'x'*32))):
            with self.assertRaisesRegex(PreviewError,'limit'):NifReader(self.root/'grown.nif')
    def test_extreme_dds_rejected_before_native_decoder(self):
        path=self.root/'bomb.dds';path.write_bytes(dds._dds_header(16384,16384,1,'BC7_UNORM',16)+b'\0'*16)
        with patch('dds.Image.open',side_effect=AssertionError('must not enter native decoder')):
            with self.assertRaisesRegex(dds.DDSError,'budget'):dds.decode_dds_pixels(str(path))
    def test_spoofed_preparsed_dds_info_is_not_trusted(self):
        path=self.root/'normal.dds';dds.encode_fallback(np.zeros((4,4,4),np.uint8),'BC3_UNORM',str(path),False)
        info=dds.parse_dds_header(str(path));info.width=8192
        with self.assertRaisesRegex(dds.DDSError,'changed'):dds.decode_dds_pixels(str(path),info)
    def test_oversized_row_pitch_rejected_before_payload_read(self):
        import struct
        raw=bytearray(dds._dds_header(2,2,1,'R8G8B8A8_UNORM',16));flags=struct.unpack_from('<I',raw,8)[0]
        struct.pack_into('<I',raw,8,flags|8);struct.pack_into('<I',raw,20,140*1024*1024)
        path=self.root/'pitch.dds';path.write_bytes(raw)
        with patch('dds._read_bytes',side_effect=AssertionError('must not allocate payload')):
            with self.assertRaisesRegex(ValueError,'payload budget'):semantic_dds.read_mip_payloads(path)
    def test_png_entry_does_not_probe_disguised_gif_plugin(self):
        import PIL.GifImagePlugin
        path=self.root/'disguised.png';Image.new('L',(2,2),1).save(path,format='GIF')
        with patch.object(PIL.GifImagePlugin.GifImageFile,'_open',side_effect=AssertionError('GIF must not be probed')):
            with self.assertRaisesRegex(ValueError,'PNG header'):texture_api._mask({'edit_mask_path':str(path)},'manual_mask','edit_mask_path',(2,2))
    def test_json_byte_depth_duplicates_and_nonfinite_limits(self):
        for text in ('['*65+'0'+']'*65,'{"ao_strength":1,"ao_strength":2}','{"ao_strength":NaN}','{"ao_strength":1e999}'):
            with self.subTest(text=text[:30]),self.assertRaises(ValueError):Config.from_json(text)
        with self.assertRaisesRegex(ValueError,'limit'):security_io.json_bounded(' '*33,limit=32)
    def test_png_uint16_masks_work_with_native_current_pillow(self):
        from mask_model import MaskModel
        path=self.root/'gray16.png';pixels=np.array([[0,257],[32768,65535]],np.uint16)
        Image.fromarray(pixels).save(path,format='PNG')
        mask=MaskModel((2,2));mask.load('edit',path)
        np.testing.assert_array_equal(mask.layers['edit'],[[0,1],[128,255]])
        actual=texture_api._mask({'edit_mask_path':str(path)},'manual_mask','edit_mask_path',(2,2))
        np.testing.assert_allclose(actual,pixels.astype(np.float64)/65535)
    def test_ipc_rejects_object_dtype_before_numpy_decode(self):
        (self.root/'response.json').write_text(json.dumps({'$array':0,'shape':[1],'dtype':'|O','offset':0,'nbytes':8}))
        (self.root/'response.bin').write_bytes(b'not code')
        with patch('preview_process.np.frombuffer',side_effect=AssertionError('object array must not decode')):
            with self.assertRaisesRegex(PreviewProcessError,'type/shape'):read_wire(self.root,'response')
    def test_ipc_preserves_shared_texture_arrays_without_duplicate_allocations(self):
        pixels=np.zeros((4,4,4),np.uint8)
        write_wire(self.root,'roundtrip',{'a':pixels,'b':pixels})
        result=read_wire(self.root,'roundtrip')
        self.assertIs(result['a'],result['b']);self.assertEqual((self.root/'roundtrip.bin').stat().st_size,64)


class PreviewIsolation(OwnedFiles):
    def test_bundled_texconv_bc7_native_roundtrip_without_overwrite_switch(self):
        tool=Path(dds.__file__).parent/'tools/texconv.exe'
        if not tool.is_file():self.skipTest('Bundled texconv unavailable')
        output=self.root/'bc7.dds';info={}
        dds.encode_dds(np.full((8,8,4),127,np.uint8),'BC7_UNORM',str(output),texconv_path=str(tool),codec_info=info)
        self.assertEqual(info.get('backend'),'texconv');self.assertEqual(dds.parse_dds_header(str(output)).fmt,'BC7_UNORM')
        self.assertEqual(dds.decode_dds_pixels(str(output)).shape,(8,8,4))
    def fixture(self):
        path=self.root/'model.nif';fixture(path);write_material(self.root,(10,20,30,255))
        pixels=np.full((8,8,4),(10,20,30,255),np.uint8)
        dds.encode_fallback(pixels,'BC1_UNORM',str(self.root/'textures/test/color.dds'),True)
        return path
    def run_recorded(self,fn):
        actual=subprocess.Popen;owned=[]
        def spawn(*args,**kwargs):
            process=actual(*args,**kwargs);owned.append((process,args,kwargs));return process
        with patch('preview_process.subprocess.Popen',side_effect=spawn):result=fn()
        return result,owned
    def test_normal_native_dds_scene_runs_in_child_and_is_read_only(self):
        path=self.fixture();before={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        scene,owned=self.run_recorded(lambda:load_scene_isolated(path,self.root))
        self.assertEqual(scene.triangles,1);self.assertEqual(scene.meshes[0].vertices.shape,(3,12))
        self.assertEqual(scene.meshes[0].material.textures['base'][1].shape,(8,8,4))
        process,args,options=owned[0];self.assertNotEqual(process.pid,os.getpid());self.assertEqual(process.returncode,0)
        self.assertFalse(options['shell']);self.assertIn('-I',args[0]);self.assertFalse(Path(args[0][-1]).exists())
        for file,payload in before.items():self.assertEqual(file.read_bytes(),payload)
    def test_normal_metal_preview_runs_through_data_only_worker(self):
        job=self.job();result=preview_job_isolated(job,{'mode':'off','texconv_path':False})
        self.assertEqual(result['source_shape'],[8,8]);self.assertTrue(result['views'])
    def test_8192_native_dds_uses_small_mip_inside_worker(self):
        path=self.fixture();texture=self.root/'textures/test/color.dds';w=h=8192;sizes=[]
        while True:
            sizes.append(max(1,(w+3)//4)*max(1,(h+3)//4)*8)
            if w==1 and h==1:break
            w=max(1,w//2);h=max(1,h//2)
        with texture.open('wb') as stream:
            header=dds._dds_header(8192,8192,len(sizes),'BC1_UNORM',sizes[0])
            stream.write(header);stream.truncate(len(header)+sum(sizes))
        scene=load_scene_isolated(path,self.root)
        self.assertEqual(scene.meshes[0].material.textures['base'][1].shape,(1024,1024,4))
    def test_pre_cancel_never_spawns(self):
        event=threading.Event();event.set()
        with patch('preview_process.subprocess.Popen',side_effect=AssertionError('must not spawn')):
            with self.assertRaises(InterruptedError):load_scene_isolated('missing.nif',cancel=event)
    def test_timeout_reaps_owned_child_and_removes_ipc(self):
        path=self.fixture();actual=subprocess.Popen;owned=[]
        def spawn(*args,**kwargs):
            process=actual(*args,**kwargs);owned.append((process,Path(args[0][-1])));return process
        with patch('preview_process.subprocess.Popen',side_effect=spawn):
            with self.assertRaisesRegex(PreviewProcessError,'timed out'):load_scene_isolated(path,self.root,timeout=0)
        self.assertIsNotNone(owned[0][0].poll());self.assertFalse(owned[0][1].exists())
    def test_active_cancel_reaps_owned_child_and_removes_ipc(self):
        path=self.fixture();actual=subprocess.Popen;owned=[];event=threading.Event()
        def spawn(*args,**kwargs):
            process=actual(*args,**kwargs);owned.append((process,Path(args[0][-1])));event.set();return process
        with patch('preview_process.subprocess.Popen',side_effect=spawn):
            with self.assertRaises(InterruptedError):load_scene_isolated(path,self.root,cancel=event)
        self.assertIsNotNone(owned[0][0].poll());self.assertFalse(owned[0][1].exists())
    def test_abnormal_worker_exit_is_reported_without_parent_failure(self):
        path=self.fixture();actual=subprocess.Popen;owned=[]
        def spawn(command,**kwargs):
            process=actual([sys.executable,'-I','-c','import sys,os;sys.stdin.buffer.read(1);os._exit(23)'],**kwargs)
            owned.append((process,Path(command[-1])));return process
        with patch('preview_process.subprocess.Popen',side_effect=spawn):
            with self.assertRaisesRegex(PreviewProcessError,'exit 23'):load_scene_isolated(path,self.root)
        self.assertEqual(owned[0][0].poll(),23);self.assertFalse(owned[0][1].exists())
    @unittest.skipUnless(os.name=='nt','Windows Job Object memory boundary')
    def test_job_memory_cap_is_enforced_before_worker_parsing(self):
        path=self.fixture();actual=subprocess.Popen;owned=[]
        code='import sys,os;sys.stdin.buffer.read(1)\ntry: value=bytearray(256*1024*1024)\nexcept MemoryError: os._exit(42)\nos._exit(43)'
        def spawn(command,**kwargs):
            process=actual([sys.executable,'-I','-c',code],**kwargs);owned.append((process,Path(command[-1])));return process
        with patch('preview_process.MAX_PROCESS_BYTES',64*1024*1024),patch('preview_process.subprocess.Popen',side_effect=spawn):
            with self.assertRaisesRegex(PreviewProcessError,'exit 42'):load_scene_isolated(path,self.root)
        self.assertEqual(owned[0][0].poll(),42);self.assertFalse(owned[0][1].exists())


if __name__=='__main__':unittest.main()
