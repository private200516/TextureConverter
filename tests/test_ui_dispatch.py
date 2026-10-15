"""UI dispatch and task-selection regressions; no dependency on real game assets."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from ui_events import UiEventBuffer

class MailboxTests(unittest.TestCase):
    def test_large_burst_is_bounded_but_counters_are_exact(self):
        box=UiEventBuffer(512)
        for i in range(100000):
            box.log('WARNING' if i%2 else 'ERROR',str(i),'item')
            box.progress(i+1,100000,str(i))
        self.assertEqual(box.pending(),512)
        first=box.drain(80)
        self.assertEqual((first['warnings'],first['errors']),(50000,50000))
        self.assertEqual(first['omitted'],99488)
        self.assertEqual(first['progress'],(100000,100000,'99999'))
        self.assertEqual(first['logs'][0][1],'99488')
        last=box.drain(512)
        self.assertEqual(last['logs'][-1][1],'99999')
        self.assertIsNone(last['progress']);self.assertEqual(box.pending(),0)
    def test_display_payload_cannot_grow_without_bound(self):
        box=UiEventBuffer(1);box.log('INFO','p'*10000,'m'*20000)
        row=box.drain()['logs'][0]
        self.assertEqual(len(row[1]),2048);self.assertEqual(len(row[2]),4096)
    def test_threaded_producer_keeps_last_progress(self):
        from concurrent.futures import ThreadPoolExecutor
        box=UiEventBuffer(64)
        def write():
            for i in range(10000):box.log('INFO',str(i));box.progress(i,10000,'x')
        with ThreadPoolExecutor(max_workers=1) as pool:
            task=pool.submit(write)
            while not task.done():box.drain(20)
            task.result()
        # A final producer update cannot be lost after the consumer's last drain.
        box.progress(10000,10000,'done');self.assertEqual(box.drain()['progress'][0],10000)

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import Qt
import main
from metal_ui import MetalDialog

class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'TEXTURE_CONVERTER_CONFIG':str(Path(self.tmp.name)/'config.json'),'LOCALAPPDATA':self.tmp.name});self.env.start()
        self.w=main.MainWindow()
        with patch.object(MetalDialog,'_start',return_value=True):self.dialog=MetalDialog(self.w.metal_panel,self.tmp.name)
        self.dialog.scanned([{'id':'a','name':'Alpha'},{'id':'b','name':'Beta'}])
        self.dialog.list.setCurrentRow(0)
    def tearDown(self):
        self.dialog.dirty=False;self.dialog.reject();self.dialog.deleteLater()
        self.w._closing=True;self.w.close();self.w.deleteLater();self.app.processEvents()
        self.env.stop();self.tmp.cleanup()
    def test_compact_home_and_optional_inspection(self):
        self.assertEqual(self.w.tabs.count(),4);self.assertEqual(self.w.tabs.currentIndex(),0)
        self.w._validate=lambda: self.w.cfg
        calls=[];self.w._run_convert=lambda cfg:calls.append(cfg)
        self.w.on_start();self.assertEqual(calls,[self.w.cfg])
    def test_filter_retains_dirty_preview(self):
        sentinel=object();self.dialog.model=sentinel;self.dialog.dirty=True
        self.dialog.filter.setText('Beta');self.dialog.fill_list()
        self.assertEqual(self.dialog.current['id'],'a');self.assertIs(self.dialog.model,sentinel)
        self.assertTrue(self.dialog.dirty);self.assertEqual(self.dialog.list.count(),1)
    def test_switch_can_cancel_without_losing_mask(self):
        self.dialog.dirty=True
        with patch.object(QMessageBox,'question',return_value=QMessageBox.Cancel):self.dialog.list.setCurrentRow(1)
        self.assertEqual(self.dialog.current['id'],'a');self.assertTrue(self.dialog.dirty)
    def test_stale_preview_is_rejected(self):
        self.dialog.preview_ready({'_preview_job_id':'b','thumbnails':{'fake':None}})
        self.assertEqual(self.dialog.views,{});self.assertIsNone(self.dialog.preview_key)
    def test_large_log_opens_outside_event_loop(self):
        path=Path(self.tmp.name)/'conversion_log.txt';path.write_text('entry\n')
        self.w._last_output=self.tmp.name
        with patch.object(os,'startfile') as launch:self.w.on_view_log()
        launch.assert_called_once_with(str(path))

if __name__=='__main__':unittest.main()
