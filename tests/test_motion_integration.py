"""Read-only main integration checks for animation close races and final layout."""
import os,tempfile,unittest,threading
from unittest.mock import patch
from types import SimpleNamespace
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from PySide6.QtWidgets import QApplication,QAbstractButton
from PySide6.QtTest import QTest
from PySide6.QtCore import QTimer,Qt,QPoint,QRect
import main
import ui_theme as T

class MainMotionIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
    def setUp(self):
        self.motion_env=patch.dict(os.environ,{'CONVERTER_REDUCED_MOTION':'0'});self.motion_env.start()
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
        self.motion_env.stop()
    def test_about_never_opens_when_close_follows_startup_finish(self):
        self.w._show_first_run_about=True
        self.w.motion.startup_finished.connect(lambda:QTimer.singleShot(10,self.w.close))
        self.w.motion.startup();QTest.qWait(T.MOTION_STARTUP_MS+T.MOTION_SHUTDOWN_MS+180)
        self.assertEqual(self.calls,[]);self.assertTrue(self.w.motion.closed);self.assertFalse(self.w.isVisible())
        self.assertTrue(all(not timer.isActive() for timer in self.w.findChildren(QTimer)))
    def test_immediate_close_cancels_startup_and_second_close_is_idempotent(self):
        self.w._show_first_run_about=True;self.w.motion.startup();QTest.qWait(20)
        self.w.close();self.w.close();QTest.qWait(T.MOTION_SHUTDOWN_MS+100)
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
    def test_five_tabs_and_bilingual_accordion_titles_fit(self):
        self.w.show();self.assertEqual(self.w.tabs.count(),5);self.assertEqual(self.w.tabs.currentIndex(),0)
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
        self.w.motion.startup();QTest.qWait(T.MOTION_STARTUP_MS+100)
        self.assertEqual(self.w.motion.state,'ready')
        self.assertEqual([(type(t.parent()).__name__,t.interval()) for t in self.w.findChildren(QTimer) if t.isActive() and t.interval()<1000],[])

    def test_startup_snapshot_uses_the_final_nested_workspace_layout(self):
        self.w.motion.startup()
        snapshot=self.w.motion.overlay.snapshot.toImage().copy()
        content=self.w.centralWidget()
        regions={}
        for name,widget in (('brand',self.w.banner),('input',self.w.in_edit),('mode',self.w.mode_pills)):
            regions[name]=QRect(widget.mapTo(content,QPoint()),widget.size()).adjusted(8,8,-8,-8)
        QTest.qWait(T.MOTION_STARTUP_MS+100)
        ready=content.grab().toImage()
        self.assertEqual(snapshot.size(),ready.size())
        self.assertEqual(snapshot.devicePixelRatio(),ready.devicePixelRatio())
        scale=snapshot.devicePixelRatio()
        for name,region in regions.items():
            physical=QRect(round(region.x()*scale),round(region.y()*scale),
                round(region.width()*scale),round(region.height()*scale))
            with self.subTest(region=name):
                self.assertEqual(snapshot.copy(physical),ready.copy(physical),
                    'Startup must capture the same nested layout that the workspace reveals')

    def test_navigation_and_dialog_release_cached_frame_after_reveal(self):
        self.w.show();self.w.tabs.setCurrentIndex(self.w.advanced_tab_index);QTest.qWait(40)
        self.assertIsNotNone(self.w.navigation_motion.overlay)
        self.assertIsNone(self.w.tabs.currentWidget().graphicsEffect())
        for index in (0,self.w.metal_tab_index,self.w.advanced_tab_index,0):
            self.w.tabs.setCurrentIndex(index);QTest.qWait(20)
        QTest.qWait(T.MOTION_REVEAL_MS+80)
        self.assertIsNone(self.w.navigation_motion.overlay)
        self.assertFalse(self.w.navigation_motion._pending.isActive())
        dialog=main.TextDialog('About',f'{main.APP_VERSION} / {main.APP_AUTHOR} / GPT-6 Astra',self.w)
        dialog.show();QTest.qWait(30);self.assertIsNotNone(dialog.reveal.overlay)
        finished=[];dialog.finished.connect(finished.append)
        dialog.close();dialog.close()
        self.assertTrue(dialog.isVisible(),'The reverse card collapse should finish before native close')
        QTest.qWait(T.MOTION_DIALOG_CLOSE_MS+80)
        self.assertIsNone(dialog.reveal.overlay);self.assertFalse(dialog.isVisible())
        self.assertEqual(len(finished),1);dialog.deleteLater()

    def test_first_launch_about_waits_for_workspace_and_second_launch_skips_it(self):
        self.w._show_first_run_about=True
        self.w.cfg.about_seen=False
        states=[]
        def show_about():
            states.append((self.w.motion.state,self.w.motion.overlay,self.w.motion._input_blocked))
            self.w.cfg.about_seen=True
        self.w.show_about=show_about
        self.w.motion.startup();QTest.qWait(max(1,T.MOTION_STARTUP_MS-100))
        self.assertEqual(states,[])
        QTest.qWait(260)
        self.assertEqual(states,[('ready',None,False)])
        self.w.maybe_about();self.w._startup_complete();QTest.qWait(100)
        self.assertEqual(len(states),1)

    def test_busy_close_cancels_then_runs_shutdown_after_worker_finishes(self):
        from ui_events import UiEventBuffer
        running=[True];cancel=threading.Event()
        worker=SimpleNamespace(events=UiEventBuffer(),isRunning=lambda:running[0],
            converter=SimpleNamespace(cancel_event=cancel))
        self.w.motion.reduced_motion=True;self.w.motion.startup();self.w.motion.reduced_motion=False
        self.w.worker=worker;self.w._active_task=worker;self.w._begin_task('running')
        self.w.close()
        self.assertTrue(cancel.is_set());self.assertTrue(self.w._close_pending)
        self.assertTrue(self.w.isVisible());self.assertEqual(self.w.motion.state,'ready')
        running[0]=False;self.w._re_enable();QTest.qWait(T.MOTION_SHUTDOWN_MS+150)
        self.assertTrue(self.w.motion.closed);self.assertFalse(self.w.isVisible())

    def test_inspection_never_replays_previous_conversion_or_counters(self):
        from ui_events import UiEventBuffer
        old=SimpleNamespace(events=UiEventBuffer(),warning_count=9,error_count=4,isRunning=lambda:False)
        active=SimpleNamespace(events=UiEventBuffer(),isRunning=lambda:False)
        self.w.worker=old;self.w.scanner=active;self.w._active_task=active
        old.events.log('ERROR','stale previous run')
        active.events.log('INFO','current scan');active.events.progress(4,0,'current.bgsm')
        self.w.proc_panel.set_counters(9,4);self.w._begin_task('scanning')
        self.w._drain_worker_events()
        self.assertEqual((self.w.proc_panel._warn,self.w.proc_panel._err),(0,0))
        self.assertEqual(self.w.proc_panel._file,'current.bgsm')
        self.assertEqual(old.events.pending(),1)
        self.assertTrue(any('current scan' in row[1] for row in self.w.logview._entries))
        self.assertFalse(any('stale previous run' in row[1] for row in self.w.logview._entries))

if __name__=='__main__':unittest.main()
