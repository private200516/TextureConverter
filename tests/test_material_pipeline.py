import unittest, tempfile, struct, json, threading, sys, hashlib
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image
from material_codec import Material,MaterialError,parse,serialize
from material_converter import convert_material,convert_file
from material_jobs import scan_jobs,safe_relative
from texture_api import run_job,run_jobs,preview_job,Cancelled
from config import Config
import dds

class MaterialCodecTests(unittest.TestCase):
    def test_all_versions_both_conditional_layouts(self):
        for version in range(1,23):
            for kind in ('BGSM','BGEM'):
                for active in (False,True):
                    m=Material(kind,version,dict(EmitEnabled=active,Terrain=active,GlassEnabled=active,Alpha=.35,SpecularColor=(.125,.5,.75)))
                    b=serialize(m);self.assertEqual(serialize(parse(b)),b)
    def test_field_conversion_preserves_common_parameters(self):
        m=Material('BGSM',22,dict(DiffuseTexture='textures/x.dds',Alpha=.25,TwoSided=True,UScale=2.,SpecularMult=.7,Smoothness=.42,EmitEnabled=True,EmittanceColor=(.25,.5,.75)))
        out,info=convert_material(m);decoded=parse(serialize(out))
        for key in ('Alpha','TwoSided','UScale','SpecularMult','Smoothness','EmittanceColor'):
            self.assertEqual(decoded.fields[key],parse(serialize(m)).fields[key])
        self.assertEqual(decoded.version,2)
    def test_bgem_glass_reported_not_silent(self):
        m=Material('BGEM',22,dict(GlassEnabled=True,GlassFresnelColor=(1.,.5,.25),GlassBlurScaleFactor=1.,BaseColor=(.2,.4,.6),EnvironmentMapping=True))
        out,info=convert_material(m);self.assertTrue(out.fields['EnvironmentMapping']);self.assertIn('GlassEnabled',info['unmapped_fields'])
    def test_truncation_length_and_trailer(self):
        b=serialize(Material('BGSM',22))
        for bad in (b[:-1],b+b'garbage',b[:60]+struct.pack('<I',0xffffffff)+b[64:]):
            with self.assertRaises(MaterialError):parse(bad)
    def test_no_source_or_existing_output_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            src=Path(td)/'a.bgsm';src.write_bytes(serialize(Material('BGSM',22)))
            with self.assertRaises(MaterialError):convert_file(src,src)
            dest=Path(td)/'b.bgsm';dest.write_bytes(b'existing')
            with self.assertRaises(FileExistsError):convert_file(src,dest)
            self.assertEqual(dest.read_bytes(),b'existing')
    def test_unsafe_paths(self):
        for s in ('../foo.dds','/foo.dds','C:/foo.dds','//server/foo.dds','textures/../../foo.dds'):
            with self.assertRaises(ValueError):safe_relative(s)

class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);self.src=self.base/'input';self.out=self.base/'output'
        (self.src/'materials').mkdir(parents=True);(self.src/'textures').mkdir()
        a=np.full((8,8,4),255,np.uint8);a[...,:3]=8
        r=np.full((8,8,4),200,np.uint8);r[...,3]=255
        l=np.full((8,8,4),128,np.uint8);l[...,3]=255
        for name,data in [('odd_color',a),('odd_reflection',r),('odd_lighting',l)]:dds.encode_fallback(data,'BC3_UNORM',str(self.src/'textures'/(name+'.dds')),True)
        self.mat=Material('BGSM',22,dict(DiffuseTexture='textures/odd_color.dds',SpecularTexture='textures/odd_reflection.dds',LightingTexture='textures/odd_lighting.dds',SpecularEnabled=True))
        (self.src/'materials/a.bgsm').write_bytes(serialize(self.mat))
    def tearDown(self):self.temp.cleanup()
    def test_slots_win_over_suffix_and_preview_alpha(self):
        jobs=scan_jobs(self.src);self.assertEqual(len(jobs),1);self.assertEqual(set(jobs[0]['texture_paths']),{'d','r','l'})
        result=preview_job(jobs[0],{'mode':'auto','color_contract_override':'linear_linear'})
        self.assertEqual(result['source_shape'],(8,8));self.assertTrue(np.all(result['views']['alpha']==1))
    def test_group_readback_and_source_unchanged(self):
        before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.src.rglob('*') if p.is_file()}
        job=scan_jobs(self.src)[0];result=run_job(job,self.out,options={'mode':'off','texconv_path':False})
        self.assertTrue((self.out/'materials/a.bgsm').exists());target=parse(self.out/'materials/a.bgsm')
        self.assertTrue((self.out/target.fields['SmoothSpecTexture']).exists());self.assertEqual(result['status'],'committed')
        for p,h in before.items():self.assertEqual(hashlib.sha256(Path(p).read_bytes()).hexdigest(),h)
    def test_missing_dependency_no_partial_material(self):
        self.mat.fields['EnvmapTexture']='textures/missing.dds';self.mat.fields['WrinklesTexture']='textures/missing.dds'
        (self.src/'materials/a.bgsm').write_bytes(serialize(self.mat));job=scan_jobs(self.src)[0]
        with self.assertRaises(ValueError):run_job(job,self.out,options={'mode':'off','texconv_path':False})
        self.assertFalse((self.out/'materials/a.bgsm').exists());self.assertFalse(list(self.out.glob('.staging-*')))
    def test_cancel_discards_staging(self):
        event=threading.Event();event.set()
        with self.assertRaises(Cancelled):run_job(scan_jobs(self.src)[0],self.out,cancel_event=event)
        self.assertFalse(self.out.exists())
    def test_dry_run_no_game_outputs(self):
        job=scan_jobs(self.src)[0];info=run_job(job,self.out,dry_run=True)
        self.assertTrue(info['dry_run']);self.assertFalse(self.out.exists())
    def test_shared_variants_stable(self):
        (self.src/'materials/b.bgsm').write_bytes(serialize(self.mat));jobs=scan_jobs(self.src)
        cfg=Config(input_dir=str(self.src),output_dir=str(self.out),metal_mode='manual',metal_options={'color_contract_override':'linear_linear','allow_experimental':True,'manual_mask':np.ones((8,8)),'texconv_path':False,'diffuse_fmt':'BC3_UNORM'})
        cfg.job_overrides={jobs[1]['id']:{'reconstruction_strength':.2}}
        log=run_jobs(jobs,cfg);self.assertEqual(log.errors,0)
        run=Path(log.output_dir)/'converted';a=parse(run/'materials/a.bgsm');b=parse(run/'materials/b.bgsm')
        self.assertNotEqual(a.fields['DiffuseTexture'],b.fields['DiffuseTexture'])
        again=run_jobs(jobs,cfg);a2=parse(Path(again.output_dir)/'converted/materials/a.bgsm')
        self.assertEqual(a2.fields['DiffuseTexture'],a.fields['DiffuseTexture'])
    def test_cache_reuses_identical_committed_textures(self):
        (self.src/'materials/b.bgsm').write_bytes(serialize(self.mat));jobs=scan_jobs(self.src)
        cfg=Config(input_dir=str(self.src),output_dir=str(self.out),metal_mode='off',metal_options={'texconv_path':False})
        log=run_jobs(jobs,cfg);self.assertEqual(log.errors,0)
        results=json.loads((Path(log.output_dir)/'reports/report.json').read_text())['results']
        self.assertFalse(results[0].get('texture_cache_reused',False));self.assertTrue(results[1]['texture_cache_reused'])
    def test_cancel_during_encode_cleans_group(self):
        import texture_api
        event=threading.Event();encode=texture_api._encode
        def cancel_after(*args,**kwargs):
            result=encode(*args,**kwargs);event.set();return result
        with patch('texture_api._encode',side_effect=cancel_after):
            with self.assertRaises(Cancelled):run_job(scan_jobs(self.src)[0],self.out,options={'mode':'off','texconv_path':False},cancel_event=event)
        self.assertFalse(list(self.out.rglob('*.dds')));self.assertFalse(list(self.out.glob('.staging-*')))
    def test_reviewed_source_change_revokes_reconstruction(self):
        job=scan_jobs(self.src)[0];preview=preview_job(job,{'mode':'manual','color_contract_override':'linear_linear','manual_mask':np.ones((8,8))})
        review=preview['metadata']['hashes'];path=self.src/'textures/odd_reflection.dds'
        original=path.read_bytes();path.write_bytes(original[:-1]+bytes([original[-1]^1]))
        result=run_job(job,self.out,options={'mode':'manual','allow_experimental':True,'manual_mask':np.ones((8,8)),'color_contract_override':'linear_linear','reviewed_source_hashes':review,'diffuse_fmt':'BC3_UNORM','texconv_path':False})
        self.assertFalse(result['metal']['applied']);self.assertIn('REVIEWED_SOURCE_CHANGED',result['metal']['diagnostic_codes'])
    def test_reviewed_manual_reconstruction_applies(self):
        job=scan_jobs(self.src)[0];opts={'mode':'manual','manual_mask':np.ones((8,8)),'color_contract_override':'linear_linear','allow_experimental':True,'diffuse_fmt':'BC3_UNORM','texconv_path':False}
        opts['reviewed_source_hashes']=preview_job(job,opts)['metadata']['hashes']
        result=run_job(job,self.out,options=opts);self.assertTrue(result['metal']['applied'])
    def test_output_under_resource_sibling_rejected(self):
        cfg=Config(input_dir=str(self.src/'materials'),output_dir=str(self.src/'textures/new'))
        with self.assertRaises(ValueError):run_jobs(scan_jobs(self.src/'materials'),cfg)
        self.assertFalse((self.src/'textures/new').exists())
    def test_rgb_mask_rejected(self):
        mask=self.base/'mask.png';Image.new('RGB',(8,8),'white').save(mask)
        with self.assertRaises(ValueError):preview_job(scan_jobs(self.src)[0],{'mode':'manual','edit_mask_path':str(mask)})

if __name__=='__main__':unittest.main()

