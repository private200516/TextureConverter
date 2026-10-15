"""Offscreen Qt workflow tests, using real codecs and real API (no browser)."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
from pathlib import Path
import time
import unittest
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
import dds
import i18n
import main
from metal_ui import MetalDialog

class UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        os.environ['TEXTURE_CONVERTER_CONFIG']=str(self.root/'config.json')
        self._old_local=os.environ.get('LOCALAPPDATA');os.environ['LOCALAPPDATA']=str(self.root/'local')
        self.w=main.MainWindow();self.w.setFixedSize(1100,940);self.w.show()
    def tearDown(self):
        self.w._closing=True;self.w.close();self.w.deleteLater();self.app.processEvents();self.tmp.cleanup()
        if self._old_local is None:os.environ.pop("LOCALAPPDATA",None)
        else:os.environ["LOCALAPPDATA"]=self._old_local
    def wait(self,dialog):
        end=time.monotonic()+10
        while dialog.work is not None and dialog.work.isRunning() and time.monotonic()<end:QTest.qWait(20)
        QTest.qWait(50)
        self.assertFalse(dialog.work is not None and dialog.work.isRunning())
    def test_bilingual_panel_and_startup(self):
        self.assertEqual(self.w.tabs.count(),4)
        self.w.tabs.setCurrentIndex(self.w.metal_tab_index);QTest.qWait(250)
        self.assertTrue(self.w.metal_panel.open_btn.isVisible())
        self.w._on_lang('en');self.assertEqual(self.w.tabs.tabText(self.w.metal_tab_index),'Metal')
        self.w._on_lang('zh');self.assertEqual(self.w.tabs.tabText(self.w.metal_tab_index),'金属重建')
        self.w._play_intro();QTest.qWait(1000)
        self.assertTrue(self.w.minimumSize()==self.w.maximumSize())
    def test_config_options_persist(self):
        self.w.metal_panel.mode.setCurrentIndex(2)
        self.w.metal_panel.overrides={'id':{'allow_experimental':True,'reconstruction_strength':.3}}
        c=self.w._collect();self.assertEqual(c.metal_mode,'manual')
        from config import load_config
        self.assertEqual(load_config().job_overrides['id']['reconstruction_strength'],.3)
    def test_real_preview_editor_and_confirm(self):
        data=self.root/'textures';data.mkdir()
        for role,value in [('d',12),('r',200),('l',160)]:
            a=np.full((32,32,4),value,np.uint8);a[...,3]=255
            dds.encode_fallback(a,'BC3_UNORM',str(data/('steel_'+role+'.dds')),mipmaps=False)
        self.w.in_edit.setText(str(self.root));self.w.metal_panel.mode.setCurrentIndex(1)
        dialog=MetalDialog(self.w.metal_panel,str(self.root));errors=[];dialog.error=lambda x:errors.append(x)
        dialog.show();self.wait(dialog)
        self.assertEqual(dialog.list.count(),1)
        dialog.list.setCurrentRow(0);dialog.transfer.setCurrentIndex(1);dialog.approve.setChecked(True)
        dialog.preview();self.wait(dialog)
        self.assertFalse(errors,errors);self.assertEqual(len(dialog.views),10)
        self.assertEqual(dialog.model.shape,(32,32))
        dialog.model.brush('protect',16,16,5);dialog.mask_changed();dialog.apply(False)
        self.assertEqual(len(self.w.metal_panel.overrides),1)
        options=next(iter(self.w.metal_panel.overrides.values()))
        self.assertTrue(Path(options['protect_mask_path']).is_file())
        self.assertTrue(options['allow_experimental']);dialog.accept();dialog.deleteLater();QTest.qWait(10)

if __name__=='__main__':unittest.main()
