"""Qt transition lifecycle, background coverage and bounded live-log regressions."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import time
import ctypes
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt, QEvent, QPointF, QPoint, QAbstractAnimation
from PySide6.QtGui import QColor, QPainter, QWheelEvent
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QPushButton, QVBoxLayout
from PySide6.QtTest import QTest
from app_motion import MotionController, ContentReveal, reduced_motion_requested
from ui_widgets import BannerWidget, StripedProgressBar, StatusBarWidget, ProcessPanel, LiveLogWidget, MarqueeWidget, SweepButton, PillSelector
import ui_theme as T

class SolidContent(QWidget):
    def __init__(self):
        super().__init__(); self.color=QColor('#3e596b')
        self.button=QPushButton('Convert',self); self.button.setGeometry(25,25,100,32)
        self.clicks=0; self.button.clicked.connect(self.clicked)
    def clicked(self): self.clicks+=1
    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),self.color);p.end()

class AppMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.w=QMainWindow();self.w.resize(640,420);self.content=SolidContent();self.w.setCentralWidget(self.content)
        self.motion=None
    def tearDown(self):
        if self.motion:self.motion.dispose()
        self.w.close();self.w.deleteLater();QTest.qWait(20)
    def create(self,reduced=False):
        self.motion=MotionController(self.w,self.content,reduced_motion=reduced)
        return self.motion
    def test_startup_single_window_full_opaque_cover_then_clean_release(self):
        m=self.create();events=[];m.startup_finished.connect(lambda:events.append('ready'));m.startup()
        QTest.qWait(40)
        visible=[w for w in self.app.topLevelWidgets() if w.isVisible()]
        self.assertEqual(visible,[self.w]);self.assertFalse(m.overlay.isWindow())
        self.assertTrue(m.overlay.testAttribute(Qt.WA_OpaquePaintEvent))
        self.assertEqual(m.overlay.geometry(),self.w.rect())
        self.assertEqual(m.overlay.snapshot.size(),self.content.size())
        self.assertEqual(m.overlay.phase,'startup')
        m._animation.pause()
        frames=[]
        for t in (0.,220.,520.,800.,1080.,T.MOTION_STARTUP_MS-1.):
            m._frame(t)
            frame=m.overlay.grab().toImage();frames.append(frame)
            for x,y in ((0,0),(639,0),(0,419),(639,419),(3,140)):
                self.assertEqual(frame.pixelColor(x,y).alpha(),255)
        self.assertEqual(frames[0].pixelColor(0,0).name(),'#171a15')
        self.assertNotEqual(frames[0],frames[2])
        orange=sum(frames[2].pixelColor(x,y).name()==T.MOTION_ORANGE
                   for y in range(0,420,12) for x in range(0,640,12))
        self.assertGreater(orange,20,'The middle frame should contain the expanding orange wipe')
        self.assertNotEqual(frames[2],frames[-1])
        QTest.mouseClick(self.content.button,Qt.LeftButton)
        self.assertEqual(self.content.clicks,0)
        m._animation.resume();QTest.qWait(T.MOTION_STARTUP_MS+80)
        self.assertEqual(events,['ready']);self.assertEqual(m.state,'ready')
        self.assertIsNone(m.overlay);self.assertIsNone(m._animation)
        self.assertEqual(self.content.graphicsEffect(),None)
        self.assertEqual(self.w.grab().toImage().pixelColor(300,300).name(),self.content.color.name())
        QTest.mouseClick(self.content.button,Qt.LeftButton);self.assertEqual(self.content.clicks,1)
        m.startup();self.assertEqual(events,['ready'])
    def test_reduced_motion_has_no_overlay_or_animation(self):
        m=self.create(True);ready=[];m.startup_finished.connect(lambda:ready.append(True));m.startup()
        self.assertEqual(ready,[True]);self.assertEqual(m.state,'ready');self.assertIsNone(m.overlay)
        closed=[];m.shutdown(lambda:closed.append(True));m.shutdown(lambda:closed.append(False))
        self.assertEqual(closed,[True]);self.assertTrue(m.closed);self.assertIsNone(m._animation)
    def test_shutdown_during_startup_cancels_old_finish_and_callback_once(self):
        m=self.create();ready=[];closed=[];m.startup_finished.connect(lambda:ready.append(True));m.startup();QTest.qWait(30)
        m.shutdown(lambda:closed.append(True));m.shutdown(lambda:closed.append(False))
        self.assertIsNotNone(m.overlay);self.assertTrue(m.overlay.isVisible());self.assertTrue(self.content.isEnabled())
        self.assertEqual(m.overlay.phase,'shutdown')
        QTest.qWait(150)
        self.assertEqual(self.w.windowOpacity(),1.,'Native fade belongs only to the final shutdown beat')
        QTest.mouseClick(self.content.button,Qt.LeftButton);self.assertEqual(self.content.clicks,0)
        QTest.qWait(T.MOTION_SHUTDOWN_MS+80)
        self.assertEqual(closed,[True]);self.assertTrue(m.closed);self.assertEqual(ready,[]);self.assertIsNone(m.overlay)
        self.assertIsNone(m._animation);QTest.qWait(T.MOTION_STARTUP_MS+40);self.assertEqual(ready,[])
    def test_dispose_cancels_callback(self):
        m=self.create();m.startup();closed=[];m.shutdown(lambda:closed.append(True));m.dispose();QTest.qWait(T.MOTION_SHUTDOWN_MS+40)
        self.assertEqual(closed,[]);self.assertIsNone(m.overlay);self.assertIsNone(m._animation)
    def test_explicit_reduced_motion_override(self):
        with patch.dict(os.environ,{'CONVERTER_REDUCED_MOTION':'1'}):self.assertTrue(reduced_motion_requested())
        with patch.dict(os.environ,{'CONVERTER_REDUCED_MOTION':'false'}):self.assertFalse(reduced_motion_requested())
        with patch.dict(os.environ,{'TEXTURE_CONVERTER_REDUCED_MOTION':'1'}):
            os.environ.pop('CONVERTER_REDUCED_MOTION',None)
            self.assertTrue(reduced_motion_requested())

    def test_default_motion_does_not_read_windows_reduction_preference(self):
        with patch.dict(os.environ):
            os.environ.pop('CONVERTER_REDUCED_MOTION',None)
            os.environ.pop('TEXTURE_CONVERTER_REDUCED_MOTION',None)
            with patch.object(ctypes,'windll',create=True) as windows:
                self.assertFalse(reduced_motion_requested())
                windows.user32.SystemParametersInfoW.assert_not_called()

    def test_hidden_startup_and_minimized_shutdown_finish_once(self):
        m=self.create();ready=[];closed=[];m.startup_finished.connect(lambda:ready.append(True))
        m.startup();self.w.hide();QTest.qWait(T.MOTION_STARTUP_MS+80)
        self.assertEqual(m.state,'ready');self.assertEqual(ready,[True]);self.assertIsNone(m.overlay)
        self.w.show();self.w.showMinimized();m.shutdown(lambda:closed.append(True))
        QTest.qWait(T.MOTION_SHUTDOWN_MS+80)
        self.assertEqual(closed,[True]);self.assertTrue(m.closed);self.assertIsNone(m._animation)

    def test_failed_close_callback_restores_live_content_and_accepts_retry(self):
        m=self.create();m.startup();QTest.qWait(T.MOTION_STARTUP_MS+60)
        failures=[];m.shutdown_failed.connect(failures.append)
        def fail():raise RuntimeError('synthetic native close failure')
        m.shutdown(fail);QTest.qWait(T.MOTION_SHUTDOWN_MS+80)
        self.assertEqual(m.state,'ready');self.assertFalse(m.closed)
        self.assertEqual(self.w.windowOpacity(),1.);self.assertIsNone(m.overlay)
        self.assertEqual(failures,['synthetic native close failure'])
        self.assertFalse(m._input_blocked)
        QTest.mouseClick(self.content.button,Qt.LeftButton);self.assertEqual(self.content.clicks,1)
        closed=[];m.shutdown(lambda:closed.append(True));QTest.qWait(T.MOTION_SHUTDOWN_MS+80)
        self.assertEqual(closed,[True]);self.assertTrue(m.closed)

    def test_page_reveal_is_replaceable_and_releases_on_hide(self):
        self.w.show();reveal=ContentReveal(self.w,enabled=lambda:True)
        reveal.reveal(self.content);QTest.qWait(20)
        self.assertIsNotNone(reveal.overlay)
        self.assertTrue(reveal.overlay.testAttribute(Qt.WA_TransparentForMouseEvents))
        reveal.reveal(self.content);QTest.qWait(20)
        self.assertEqual(len(self.content.findChildren(QWidget,'',Qt.FindDirectChildrenOnly)),2)
        self.w.hide();self.assertIsNone(reveal.overlay)
        self.assertFalse(reveal._pending.isActive())
        reveal.enabled=lambda:False;self.w.show();reveal.reveal(self.content)
        self.assertIsNone(reveal.overlay);self.assertFalse(reveal._pending.isActive())

    def test_dialog_collapse_blocks_input_and_hidden_finish_restores_enabled_state(self):
        self.w.show();reveal=ContentReveal(self.w,enabled=lambda:True,kind='dialog');finished=[]
        reveal.collapse(self.content,lambda:finished.append(True));QTest.qWait(30)
        self.assertFalse(self.content.isEnabled());self.assertIsNotNone(reveal.overlay)
        self.w.hide();QTest.qWait(20)
        self.assertEqual(finished,[True]);self.assertTrue(self.content.isEnabled())
        self.assertIsNone(reveal.overlay);self.assertFalse(reveal._pending.isActive())
        QTest.qWait(T.MOTION_DIALOG_CLOSE_MS+40);self.assertEqual(finished,[True])

class WidgetMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.w=QWidget();self.w.resize(940,760);self.layout=QVBoxLayout(self.w)
        self.widgets=[]
    def tearDown(self):self.w.close();self.w.deleteLater();QTest.qWait(20)
    def add(self,widget):self.widgets.append(widget);self.layout.addWidget(widget);widget.set_reduced_motion(False) if hasattr(widget,'set_reduced_motion') else None;return widget
    def test_idle_and_hidden_widgets_have_no_active_frame_timers(self):
        banner=self.add(BannerWidget());status=self.add(StatusBarWidget());log=self.add(LiveLogWidget());progress=self.add(StripedProgressBar());marquee=self.add(MarqueeWidget('TEST'))
        self.w.show();QTest.qWait(80)
        self.assertTrue(all(not w._timer.isActive() for w in self.widgets))
        status.set_state('running');progress.set_progress(.5);marquee.set_running(True);log.append_entry('OK','mesh.dds')
        self.assertTrue(all(w._timer.isActive() for w in (status,progress,marquee,log)))
        self.w.hide();QTest.qWait(20)
        self.assertTrue(all(not w._timer.isActive() for w in self.widgets))
        self.w.show();QTest.qWait(250);status.set_state('done');progress.set_progress(1.,False);marquee.set_running(False)
        QTest.qWait(1600)
        self.assertTrue(all(not w._timer.isActive() for w in self.widgets))
    def test_deactivated_window_suspends_frame_clocks_until_reactivation(self):
        status=self.add(StatusBarWidget());progress=self.add(StripedProgressBar());marquee=self.add(MarqueeWidget('TEST'))
        self.w.show();QTest.qWait(30)
        status.set_state('running');progress.set_progress(.5);marquee.set_running(True)
        self.assertTrue(all(widget._timer.isActive() for widget in self.widgets))
        QApplication.sendEvent(self.w,QEvent(QEvent.WindowDeactivate))
        self.assertTrue(all(not widget._timer.isActive() for widget in self.widgets))
        QApplication.sendEvent(self.w,QEvent(QEvent.WindowActivate))
        self.assertTrue(all(widget._timer.isActive() for widget in self.widgets))
    def test_log_bounded_batch_one_update_and_reading_anchor(self):
        log=self.add(LiveLogWidget());self.w.show();QTest.qWait(30)
        with patch.object(log,'update',wraps=log.update) as update:
            log.append_entries([('OK',f'{i}.dds','converted') for i in range(5000)])
            self.assertEqual(update.call_count,1)
        self.assertEqual(len(log._entries),2000);self.assertEqual(log._total_entries,5000)
        self.assertEqual(log._scroll,0)
        log._scroll=100
        old=list(log._entries)[len(log._entries)-log._visible_count()-log._scroll][1]
        log.append_entries([('OK',f'new-{i}.dds','') for i in range(40)])
        new=list(log._entries)[len(log._entries)-log._visible_count()-log._scroll][1]
        self.assertEqual(old,new);self.assertFalse(log._timer.isActive())
        log._scroll=0;log.append_entry('INFO','tail');self.assertTrue(log._timer.isActive())
        QTest.qWait(int(log.ARRIVAL_SECONDS*1000)+60);self.assertFalse(log._timer.isActive())
    def test_wheel_up_pauses_follow_and_down_returns_to_tail(self):
        log=self.add(LiveLogWidget());self.w.show();log.append_entries([('OK',str(i),'') for i in range(100)])
        def wheel(delta):
            event=QWheelEvent(QPointF(20,50),QPointF(20,50),QPoint(),QPoint(0,delta),Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)
            QApplication.sendEvent(log,event)
        wheel(120);self.assertEqual(log._scroll,3);wheel(-120);self.assertEqual(log._scroll,0)
    def test_same_state_does_not_reset_progress_or_timing(self):
        status=self.add(StatusBarWidget());panel=self.add(ProcessPanel());self.w.show()
        status.set_state('running');stamp=status._t0;status.set_progress_info(12,50);status.set_state('running')
        panel.set_stage('running');panel.set_progress(12,50,'a.dds');panel.set_stage('running')
        self.assertEqual(status._t0,stamp);self.assertEqual(status._done,12);self.assertEqual(panel._done,12)
        self.assertEqual(panel.height(),72);self.assertFalse(hasattr(panel,'_timer'))
    def test_reduced_motion_settles_counters_and_never_starts_log_timer(self):
        banner=self.add(BannerWidget());log=self.add(LiveLogWidget());self.w.show()
        banner.set_reduced_motion(True);banner.set_count(123,400)
        log.set_reduced_motion(True);log.set_running(True);log.append_entry('OK','a.dds')
        self.assertEqual(banner._count_disp,123.);self.assertFalse(banner._timer.isActive());self.assertFalse(log._timer.isActive())

    def test_button_requires_press_and_supports_keyboard_and_cancel(self):
        button=self.add(SweepButton('Convert'));self.w.show();QTest.qWait(20);clicks=[]
        button.clicked.connect(lambda:clicks.append(True))
        QTest.mouseRelease(button,Qt.LeftButton);self.assertEqual(clicks,[])
        QTest.mouseClick(button,Qt.LeftButton);self.assertEqual(len(clicks),1)
        QTest.keyClick(button,Qt.Key_Space);QTest.keyClick(button,Qt.Key_Return)
        self.assertEqual(len(clicks),3)
        QTest.mousePress(button,Qt.LeftButton);button.setEnabled(False)
        QTest.mouseRelease(button,Qt.LeftButton);self.assertEqual(len(clicks),3)
        self.assertEqual(button._anim.state(),QAbstractAnimation.Stopped);self.assertEqual(button._sweep,0.)

    def test_interaction_animations_settle_when_hidden_or_suspended(self):
        pills=self.add(PillSelector([('a','Quality'),('b','Space')]))
        button=self.add(SweepButton('Convert'));self.w.show();QTest.qWait(20)
        pills.set_key('b');button._hover=True;button._animate_to(1.)
        self.w.hide();self.assertEqual(pills._pos,1.);self.assertEqual(pills._anim.state(),QAbstractAnimation.Stopped)
        self.assertEqual(button._anim.state(),QAbstractAnimation.Stopped);self.assertEqual(button._sweep,0.)
        self.w.show();QTest.keyClick(pills,Qt.Key_Home);self.assertEqual(pills.current_key(),'a')
        pills.set_motion_suspended(True);self.assertEqual(pills._pos,0.)
        self.assertEqual(pills._anim.state(),QAbstractAnimation.Stopped)
        button.set_reduced_motion(True);button._hover=True;button._animate_to(1.)
        self.assertEqual(button._sweep,1.);self.assertEqual(button._anim.state(),QAbstractAnimation.Stopped)

if __name__=='__main__':unittest.main()
