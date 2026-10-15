"""Main-window contracts for local NIF preview without requiring a GPU in unit tests."""
import os,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Signal,QTimer
from PySide6.QtWidgets import QApplication,QWidget
from PySide6.QtTest import QTest
import main

class PreviewStub(QWidget):
    busy_changed=Signal(bool)
    def __init__(self,parent=None):
        super().__init__(parent);self.busy=False;self.roots=[];self.closed=False;self.current_roots=None
    def set_resource_roots(self,*roots):self.roots.append(roots);self.current_roots=roots
    def is_busy(self):return self.busy
    def retranslate(self):pass
    def cancel(self):QTimer.singleShot(10,self.finish)
    def finish(self):self.busy=False;self.busy_changed.emit(False)
    def shutdown(self):self.closed=True;return not self.busy

class PreviewIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.env=patch.dict(os.environ,{'TEXTURE_CONVERTER_CONFIG':str(self.root/'settings.json'),'LOCALAPPDATA':str(self.root)})
        self.env.start();self.module=patch.dict(sys.modules,{'nif_preview':types.SimpleNamespace(NifPreviewPanel=PreviewStub)})
        self.module.start();self.w=main.MainWindow();self.w.motion.reduced_motion=True
        self.w.motion.startup();QTest.qWait(10)
    def tearDown(self):
        if self.w._preview is not None:self.w._preview.busy=False
        self.w._closing=True;self.w.close();self.w.deleteLater();QTest.qWait(10)
        self.module.stop();self.env.stop();self.temp.cleanup()
    def test_lazy_preview_claims_space_and_restores_home(self):
        self.assertIsNone(self.w._preview)
        self.w.open_model_preview();QTest.qWait(20)
        self.assertIsNotNone(self.w._preview)
        self.assertFalse(self.w.logview.isVisible());self.assertFalse(self.w.statusbar.isVisible())
        self.w.tabs.setCurrentIndex(0);QTest.qWait(20)
        self.assertTrue(self.w.logview.isVisible());self.assertTrue(self.w.statusbar.isVisible())
    def test_custom_preview_roots_survive_tab_switch_but_new_run_is_offered(self):
        self.w.in_edit.setText(str(self.root/'source'));self.w.out_edit.setText(str(self.root/'output'))
        self.w.open_model_preview();preview=self.w._preview
        preview.current_roots=('user-selected','user-selected-source')
        self.w.tabs.setCurrentIndex(0);self.w.open_model_preview()
        self.assertEqual(len(preview.roots),1);self.assertEqual(preview.current_roots,('user-selected','user-selected-source'))
        run=self.root/'run_001';(run/'converted').mkdir(parents=True)
        self.w._last_output=str(run);self.w._ensure_preview()
        self.assertEqual(preview.roots[-1],(str(run/'converted'),str(self.root/'source')))
    def test_close_waits_for_loader_then_releases_preview(self):
        self.w.open_model_preview();p=self.w._preview;p.busy=True;p.busy_changed.emit(True)
        self.w.close();self.assertTrue(self.w.isVisible());self.assertTrue(self.w._close_pending)
        QTest.qWait(100);self.assertTrue(p.closed);self.assertFalse(self.w.isVisible())
    def test_root_sync_defers_while_loader_is_using_old_roots(self):
        self.w.open_model_preview();p=self.w._preview
        old=list(p.roots);p.busy=True
        self.w.in_edit.setText('new-source');self.w._ensure_preview()
        self.assertEqual(p.roots,old)
        self.w._re_enable();self.assertFalse(self.w.btn_start.isEnabled())
        p.busy=False;self.w._ensure_preview()
        self.assertEqual(p.roots[-1][1],'new-source')
    def test_home_preview_button_is_visible_in_both_languages(self):
        self.w.setFixedSize(1060,940)
        for lang in ('en','zh'):
            self.w._on_lang(lang);QTest.qWait(30)
            self.assertEqual(self.w.btn_preview.visibleRegion().boundingRect(),self.w.btn_preview.rect())
    def test_old_high_preset_keeps_identical_formats(self):
        self.w.cfg.mode='high';self.w.cfg.apply_mode();old=self.w.cfg.diffuse_fmt,self.w.cfg.normal_fmt,self.w.cfg.specular_fmt,self.w.cfg.glow_fmt
        self.w._load_into_ui();new=self.w._collect()
        self.assertEqual(new.mode,'balanced');self.assertEqual(old,(new.diffuse_fmt,new.normal_fmt,new.specular_fmt,new.glow_fmt))

if __name__=='__main__':unittest.main()
