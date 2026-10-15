"""Independent regressions for reviewed approval, source identity and UI masks."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import copy
import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import numpy as np
from PIL import Image
import dds
from config import Config
from material_codec import Material,parse,serialize
from material_jobs import scan_jobs
from texture_api import preview_job,run_job,run_jobs,_prepare
try:
    from PySide6.QtWidgets import QApplication,QLineEdit
    from PySide6.QtTest import QTest
    from metal_ui import MetalPanel,MetalDialog
    HAS_QT=True
except ImportError:
    HAS_QT=False


class WorkflowReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if HAS_QT:cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='mc-workflow-review-')
        self.root=Path(self.temp.name);self.src=self.root/'inputs';self.out=self.root/'outputs'
        self.make_asset(self.src)
        self.job=scan_jobs(self.src)[0]
        self.opts={'mode':'auto','allow_experimental':True,'color_contract_override':'linear_linear',
                   'diffuse_fmt':'BC3_UNORM','texconv_path':False,'addressing':'wrap'}
        self.opts['reviewed_source_hashes']=preview_job(self.job,self.opts)['metadata']['hashes']
    def tearDown(self):self.temp.cleanup()
    def make_asset(self,root):
        (root/'materials').mkdir(parents=True);(root/'textures').mkdir()
        for role,value in [('d',1),('r',200),('l',160)]:
            data=np.full((32,32,4),value,np.uint8);data[...,3]=255
            if role=='d':data[...,3]=np.arange(32,dtype=np.uint8)[:,None]*8
            (root/'textures'/('steel_'+role+'.dds')).write_bytes(dds._dds_header(32,32,1,'R8G8B8A8_UNORM',data.nbytes)+data.tobytes())
        mat=Material('BGSM',22,dict(DiffuseTexture='textures/steel_d.dds',SpecularTexture='textures/steel_r.dds',LightingTexture='textures/steel_l.dds',SpecularEnabled=True))
        (root/'materials'/'steel.bgsm').write_bytes(serialize(mat))

    def test_global_off_overrides_saved_job_mode_and_approval(self):
        cfg=Config(input_dir=str(self.src),output_dir=str(self.out),metal_mode='off',
                   diffuse_fmt='BC3_UNORM',metal_options={'texconv_path':False})
        cfg.job_overrides={self.job['id']:dict(self.opts)}
        log=run_jobs([self.job],cfg)
        report=json.loads((Path(log.output_dir)/'reports/report.json').read_text())
        result=report['results'][0]
        self.assertEqual(result['metal']['mode'],'off')
        self.assertFalse(result['metal']['applied'])
        self.assertIn('DISABLED_LEGACY_BASELINE',result['metal']['diagnostic_codes'])

    def test_experimental_approval_revoked_when_dds_changes(self):
        path=self.src/'textures/steel_r.dds';data=bytearray(path.read_bytes());data[148]=220;path.write_bytes(data)
        result=run_job(self.job,self.out,options=self.opts)
        self.assertFalse(result['metal']['applied'])
        self.assertIn('REVIEWED_SOURCE_CHANGED',result['metal']['diagnostic_codes'])

    def test_identical_relative_names_in_other_root_do_not_reuse_job_id(self):
        other=self.root/'other';self.make_asset(other)
        otherjob=scan_jobs(other)[0]
        self.assertNotEqual(self.job['id'],otherjob['id'])
        result=run_job(otherjob,self.out,options=self.opts)
        self.assertFalse(result['metal']['applied'])
        self.assertIn('REVIEWED_SOURCE_CHANGED',result['metal']['diagnostic_codes'])

    def test_declared_source_material_change_requires_rescan(self):
        path=self.src/'materials/steel.bgsm';mat=parse(path);mat.fields['UScale']=2;path.write_bytes(serialize(mat))
        with self.assertRaisesRegex(ValueError,'MATERIAL_CHANGED_SINCE_SCAN'):
            run_job(self.job,self.out,options=self.opts)
        self.assertFalse((self.out/'materials/steel.bgsm').exists())
        self.assertFalse(list(self.out.glob('.staging-*')))

    def test_cancel_after_first_rename_rolls_back_group(self):
        event=threading.Event();original=Path.rename;calls=[]
        def cancel_after_commit(path,target):
            result=original(path,target)
            if '.staging-' in str(path):calls.append(str(target));event.set()
            return result
        with patch.object(Path,'rename',cancel_after_commit):
            from texture_api import Cancelled
            with self.assertRaises(Cancelled):run_job(self.job,self.out,options=self.opts,cancel_event=event)
        self.assertTrue(calls)
        self.assertFalse(list(self.out.rglob('*.dds')))
        self.assertFalse(list(self.out.rglob('*.bgsm')))
        self.assertFalse(list(self.out.glob('.staging-*')))
        self.assertFalse(list(self.out.glob('.manifests/*.json')))

    def test_write_failure_after_first_rename_rolls_back_group(self):
        original=Path.rename;calls=[]
        def fail_second(path,target):
            if '.staging-' in str(path):
                calls.append(str(target))
                if len(calls)==2:raise OSError('Synthetic disk full')
            return original(path,target)
        with patch.object(Path,'rename',fail_second):
            with self.assertRaisesRegex(OSError,'Synthetic disk full'):run_job(self.job,self.out,options=self.opts)
        self.assertFalse(list(self.out.rglob('*.dds')))
        self.assertFalse(list(self.out.rglob('*.bgsm')))
        self.assertFalse(list(self.out.glob('.manifests/*.json')))

    def test_reverse_dds_is_explicitly_rejected_before_deploy(self):
        with self.assertRaisesRegex(ValueError,'REVERSE_DDS_UNSUPPORTED'):
            run_job(self.job,self.out,direction='4-to-76',options=self.opts)
        self.assertFalse(self.out.exists())

    @unittest.skipUnless(HAS_QT,'Qt runtime unavailable')
    def test_paint_refresh_apply_keeps_protection_and_export_alpha(self):
        owner=SimpleNamespace(in_edit=QLineEdit(str(self.src)))
        panel=MetalPanel(owner);panel.mode.setCurrentIndex(1)
        old_appdata=os.environ.get('LOCALAPPDATA');os.environ['LOCALAPPDATA']=str(self.root/'appdata')
        dialog=MetalDialog(panel,str(self.src));errors=[];dialog.error=lambda message:errors.append(message)
        def wait():
            until=time.monotonic()+10
            while dialog.work is not None and time.monotonic()<until:QTest.qWait(20)
            self.assertIsNone(dialog.work)
        try:
            dialog.show();wait();dialog.list.setCurrentRow(0)
            dialog.transfer.setCurrentIndex(dialog.transfer.findData('linear_linear'));dialog.approve.setChecked(True)
            dialog.preview();wait();self.assertFalse(errors,errors)
            dialog.model.layers['protect'][8:24,8:24]=255;dialog.mask_changed()
            self.assertTrue(dialog.dirty)
            dialog.preview();wait()
            self.assertTrue(dialog.dirty,'Refreshing preview must not clear unsaved mask changes')
            dialog.apply(False);self.assertFalse(errors,errors)
            saved=panel.overrides[self.job['id']]
            self.assertIn('protect_mask_path',saved)
            with Image.open(saved['protect_mask_path']) as image:mask=np.asarray(image).copy()
            self.assertTrue(np.all(mask[8:24,8:24]==255))
            options=dict(saved,texconv_path=False,diffuse_fmt='BC3_UNORM',addressing='wrap')
            source,base,result,headers,hashes,diagnostics=_prepare(self.job,options,'export')
            self.assertTrue(result.report['applied'],result.report)
            protected=mask==255
            np.testing.assert_array_equal(result.diffuse[protected],base['d'][protected]/255.)
            np.testing.assert_array_equal(result.diffuse[...,3],base['d'][...,3]/255.)
            info=run_job(self.job,self.out,options=options)
            self.assertTrue(info['metal']['applied'])
            self.assertTrue(info['metal']['alpha_preserved'])
            self.assertTrue((self.out/'materials/steel.bgsm').is_file())
            target=parse(self.out/'materials/steel.bgsm');decoded=dds.decode_dds_pixels(str(self.out/target.fields['DiffuseTexture']))
            # This is a block-aligned protected rectangle: compare with the same
            # backend baseline, allowing ordinary alpha compression separately.
            from semantic_dds import encode_semantic_dds
            baseline=self.root/'baseline.dds'
            encode_semantic_dds(base['d'],'BC3_UNORM',baseline,semantic='color',transfer_function='linear',addressing='wrap')
            baseline_read=dds.decode_dds_pixels(str(baseline))
            np.testing.assert_array_equal(decoded[protected],baseline_read[protected])
        finally:
            dialog.accept();dialog.deleteLater();panel.deleteLater();owner.in_edit.deleteLater();QTest.qWait(30)
            if old_appdata is None:os.environ.pop('LOCALAPPDATA',None)
            else:os.environ['LOCALAPPDATA']=old_appdata


if __name__=='__main__':unittest.main()
