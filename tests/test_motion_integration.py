"""Read-only main integration checks for animation close races and final layout."""
import os,tempfile,unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from PySide6.QtWidgets import QApplication,QAbstractButton
from PySide6.QtTest import QTest
from PySide6.QtCore import QTimer,Qt
import main

class MainMotionIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='texture-motion-integration-')
        self.old_config=os.environ.get('TEXTURE_CONVERTER_CONFIG')
        os.environ['TEXTURE_CONVERTER_CONFIG']=str(Path(self.temp.name)/'config.json')
        self.w=main.MainWindow();self.w.setFixedSize(1060,940);self.w.motion.reduced_motion=False
        self.calls=[];self.w.show_about=lambda:self.calls.append('about')
    def tearDown(self):
        self.w.motion.dispose();self.w._closing=True;self.w.close();self.w.deleteLater();QTest.qWait(30)
        if self.old_config is None:os.environ.pop('TEXTURE_CONVERTER_CONFIG',None)
        else:os.environ['TEXTURE_CONVERTER_CONFIG']=self.old_config
        self.temp.cleanup()
    def test_about_never_opens_when_close_follows_startup_finish(self):
        self.w._show_first_run_about=True
        self.w.motion.startup_finished.connect(lambda:QTimer.singleShot(10,self.w.close))
        self.w.motion.startup();QTest.qWait(1400)
        self.assertEqual(self.calls,[]);self.assertTrue(self.w.motion.closed);self.assertFalse(self.w.isVisible())
        self.assertTrue(all(not timer.isActive() for timer in self.w.findChildren(QTimer)))
    def test_immediate_close_cancels_startup_and_second_close_is_idempotent(self):
        self.w._show_first_run_about=True;self.w.motion.startup();QTest.qWait(20)
        self.w.close();self.w.close();QTest.qWait(360)
        self.assertEqual(self.calls,[]);self.assertEqual(self.w.motion.state,'closed')
        self.assertFalse(self.w.isVisible());self.assertIsNone(self.w.motion.overlay)
    def test_minimized_widgets_suspend_frame_timers_and_restore(self):
        self.w.show();self.w.statusbar.set_state('running');self.w.banner.set_count(30,90)
        self.w.logview.append_entry('OK','sample.dds');QTest.qWait(20)
        self.assertTrue(self.w.statusbar._timer.isActive())
        self.w.showMinimized();QTest.qWait(50)
        self.assertFalse(self.w.statusbar._timer.isActive());self.assertFalse(self.w.logview._timer.isActive())
        self.assertFalse(self.w.banner._timer.isActive())
        self.w.showNormal();QTest.qWait(60);self.assertTrue(self.w.statusbar._timer.isActive())
    def test_four_tabs_and_bilingual_accordion_titles_fit(self):
        self.w.show();self.assertEqual(self.w.tabs.count(),4);self.assertEqual(self.w.tabs.currentIndex(),0)
        for language,expected in (('en','OPTIONS'),('zh','选项')):
            self.w.lang_pills.set_key(language,animate=False);self.w.tabs.setCurrentIndex(self.w.advanced_tab_index);QTest.qWait(80)
            self.assertEqual(self.w.advanced.itemText(0),expected)
            buttons=[b for b in self.w.advanced.findChildren(QAbstractButton) if b.metaObject().className()=='QToolBoxButton']
            self.assertEqual(len(buttons),4)
            for button in buttons:
                self.assertTrue(button.text());self.assertGreaterEqual(button.height(),34)
                self.assertGreater(button.width(),button.fontMetrics().horizontalAdvance(button.text())+28)
            self.assertTrue(self.w.statusbar.rect().contains(self.w.btn_start.geometry()))
            self.assertEqual(self.w.proc_panel.height(),72)
    def test_completed_main_idle_has_no_subsecond_timer(self):
        self.w.motion.startup();QTest.qWait(1050)
        self.assertEqual(self.w.motion.state,'ready')
        self.assertEqual([(type(t.parent()).__name__,t.interval()) for t in self.w.findChildren(QTimer) if t.isActive() and t.interval()<1000],[])

if __name__=='__main__':unittest.main()
