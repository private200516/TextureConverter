"""Preview loader lifetime tests. Rendering is verified separately on native GL."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import tempfile,time,unittest
try:
    from PySide6.QtWidgets import QApplication
    from nif_preview import NifPreviewPanel
    import i18n
    QT=True
except ImportError:QT=False
from test_nif_preview import fixture

@unittest.skipUnless(QT,'Qt unavailable')
class PreviewUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='preview-ui-test-')
        self.nif=Path(self.temp.name)/'tiny.nif';fixture(self.nif)
        self.panel=NifPreviewPanel()
    def finish(self):
        deadline=time.monotonic()+5
        while self.panel.is_busy() and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.002)
        self.app.processEvents()
        self.assertFalse(self.panel.is_busy(),'Loader did not finish')
    def tearDown(self):
        self.panel.cancel();self.finish();self.panel.shutdown();self.panel.close()
        self.panel.deleteLater();self.app.processEvents();self.temp.cleanup()
    def test_load_busy_signals_and_scene(self):
        states=[];self.panel.busy_changed.connect(states.append)
        self.assertTrue(self.panel.open_nif(str(self.nif)))
        self.assertTrue(self.panel.is_busy());self.assertFalse(self.panel.open_button.isEnabled())
        self.finish()
        self.assertEqual(states,[True,False]);self.assertIsNotNone(self.panel._scene)
        self.assertTrue(self.panel.open_button.isEnabled())
    def test_failure_clears_previous_model(self):
        self.panel.open_nif(str(self.nif));self.finish();self.assertIsNotNone(self.panel._scene)
        self.panel.open_nif(str(self.nif.parent/'missing.nif'))
        self.assertIsNone(self.panel._scene);self.assertIsNone(self.panel.viewport.scene)
        self.finish();self.assertTrue(self.panel._failed);self.assertIsNone(self.panel._scene)
    def test_shutdown_cancels_without_destroying_running_thread(self):
        self.panel.open_nif(str(self.nif))
        self.assertFalse(self.panel.shutdown())
        self.assertTrue(self.panel._worker.cancel_event.is_set())
        self.finish();self.assertTrue(self.panel.shutdown())
        self.assertFalse(self.panel.open_nif(str(self.nif)))
    def test_root_update_while_busy_is_rejected_without_change(self):
        self.assertTrue(self.panel.set_resource_roots('old-converted','old-source'))
        self.panel.open_nif(str(self.nif))
        self.assertFalse(self.panel.set_resource_roots('new-converted','new-source'))
        self.assertEqual(self.panel.converted_edit.text(),'old-converted')
        self.assertEqual(self.panel.source_edit.text(),'old-source')
        self.finish()
    def test_changed_root_invalidates_rendered_materials_until_reload(self):
        self.panel.open_nif(str(self.nif));self.finish()
        self.assertIsNotNone(self.panel._scene)
        self.assertTrue(self.panel.set_resource_roots(str(self.nif.parent/'new-output'),''))
        self.assertIsNone(self.panel._scene);self.assertIsNone(self.panel.viewport.scene)
        self.assertTrue(self.panel._needs_reload)
        previous=i18n.LANG
        try:
            i18n.LANG='en';self.panel.retranslate()
            self.assertIn('click Reload',self.panel.status_label.text())
        finally:i18n.LANG=previous
        self.panel.open_nif(str(self.nif));self.finish()
        self.assertFalse(self.panel._needs_reload);self.assertIsNotNone(self.panel._scene)
    def test_equivalent_root_does_not_invalidate_loaded_scene(self):
        root=str(self.nif.parent)
        self.panel.set_resource_roots(root,'')
        self.panel.open_nif(str(self.nif));self.finish();scene=self.panel._scene
        self.panel.set_resource_roots(root+'/.','')
        self.assertIs(self.panel._scene,scene);self.assertFalse(self.panel._needs_reload)
    def test_manual_root_edit_invalidates_on_editing_finished(self):
        self.panel.open_nif(str(self.nif));self.finish()
        self.panel.source_edit.setText(str(self.nif.parent/'other-source'))
        self.panel.source_edit.editingFinished.emit()
        self.assertIsNone(self.panel._scene);self.assertIsNone(self.panel.viewport.scene)
        self.assertTrue(self.panel._needs_reload)
    def test_compact_browse_and_secondary_buttons_use_ghost_style(self):
        for button in (self.panel.converted_pick,self.panel.source_pick):
            self.assertEqual(button.width(),40)
            self.assertEqual(button.text(),'…')
            self.assertIn('padding:0px',button.styleSheet())
        for button in (self.panel.reload_button,self.panel.fit_button,self.panel.details_button):
            self.assertEqual(button.objectName(),'ghost')
        self.assertGreaterEqual(self.panel.mode_combo.minimumWidth(),190)
    def test_resource_roots_and_language(self):
        self.panel.set_resource_roots('converted-folder','source-folder')
        self.assertEqual(self.panel.converted_edit.text(),'converted-folder')
        self.assertEqual(self.panel.source_edit.text(),'source-folder')
        previous=i18n.LANG
        try:
            i18n.LANG='en';self.panel.retranslate();self.assertEqual(self.panel.open_button.text(),'Open NIF')
            i18n.LANG='zh';self.panel.retranslate();self.assertEqual(self.panel.open_button.text(),'选择 NIF')
        finally:i18n.LANG=previous

if __name__=='__main__':unittest.main()
