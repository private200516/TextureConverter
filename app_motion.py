"""Cached geometric transitions; one native window and no live-tree effects.

Startup/close keep a single screenshot and a single animation clock. All close
paths finish exactly once, including hidden windows and interrupted startup.
"""
from __future__ import annotations
import math
import os
from PySide6.QtCore import QObject, QEvent, QPoint, QPointF, QRectF, Qt, Signal, QVariantAnimation, QEasingCurve, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget, QApplication
import ui_theme as T


def brand_logo():
    """Load only the bundled toolkit mark; callers cache it for their lifetime."""
    from config import resource_path
    return QPixmap(resource_path('assets', 'toolkit-logo.png'))


def reduced_motion_requested() -> bool:
    """Only an explicit process setting disables the default visible motion."""
    value=os.environ.get('CONVERTER_REDUCED_MOTION',os.environ.get('TEXTURE_CONVERTER_REDUCED_MOTION'))
    return value is not None and value.strip().lower() not in ('','0','false','no','off')


def _unit(value,start,end):
    return max(0.,min(1.,(value-start)/max(.001,end-start)))


def _out(value):
    return 1.-(1.-value)**3


def _smooth(value):
    return value*value*(3.-2.*value)


class _ContentOverlay(QWidget):
    """Masked card/strips over a cached page; never composites a live subtree."""
    def __init__(self,content,snapshot,*,kind='page',direction=1,closing=False):
        super().__init__(content)
        self.snapshot=snapshot;self.progress=1. if closing else 0.
        self.kind=kind;self.direction=direction
        self.setAttribute(Qt.WA_OpaquePaintEvent,True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents,not closing)
        self.setFocusPolicy(Qt.NoFocus);self.setGeometry(content.rect())

    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),QColor(T.MOTION_CREAM))
        w,h=self.width(),self.height();cx,cy=w/2.,h/2.
        p.setRenderHint(QPainter.Antialiasing)
        if self.kind=='dialog':
            q=_out(self.progress);height=max(2.,h*q)
            clip=QPainterPath();clip.addRoundedRect(QRectF((1-q)*14,(h-height)/2+(1-q)*16,w-(1-q)*28,height),18*(1-q),18*(1-q))
            p.setClipPath(clip);p.translate(cx,cy+(1-q)*16);p.scale(.985+.015*q,.985+.015*q);p.translate(-cx,-cy)
            p.drawPixmap(QPointF(),self.snapshot)
        else:
            for index in range(4):
                q=_out(_unit(self.progress*1.18,index*.06,1.))
                band=QRectF(0,index*h/4,w,h/4+1)
                reveal=QRectF(0 if self.direction>0 else w*(1-q),band.y(),w*q,band.height())
                p.save();p.setClipRect(reveal)
                p.translate(cx+(1-q)*16*self.direction,cy+(1-q)*10)
                p.scale(.985+.015*q,.985+.015*q);p.translate(-cx,-cy)
                p.drawPixmap(QPointF(),self.snapshot);p.restore()
        p.end()


class ContentReveal(QObject):
    """Replaceable navigation and modal card transitions, with no idle clock."""
    def __init__(self,parent,*,enabled=None,kind='page'):
        super().__init__(parent)
        self.enabled=enabled or (lambda:not reduced_motion_requested())
        self.kind=kind;self.overlay=None;self.target=None
        self._closing=False;self._completed=None;self._target_enabled=True;self._direction=1
        self._pending=QTimer(self,interval=0,singleShot=True,timeout=self._begin)
        self._animation=QVariantAnimation(self)
        self._animation.setEasingCurve(QEasingCurve.Linear)
        self._animation.valueChanged.connect(self._frame)
        self._animation.finished.connect(self._finish)

    def reveal(self,target,direction=1):
        self.cancel();self._direction=1 if direction>=0 else -1
        self._start(target,False,None)

    def collapse(self,target,callback):
        self.cancel();self._start(target,True,callback)

    def _start(self,target,closing,callback):
        if target is None or not self.enabled() or not target.isVisible() or target.window().isMinimized():
            if callback is not None:callback()
            return
        self.target=target;self._closing=closing;self._completed=callback
        self._target_enabled=target.isEnabled()
        target.installEventFilter(self);target.window().installEventFilter(self)
        self._pending.start()

    def _begin(self):
        target=self.target
        if target is None:return
        if not self.enabled() or not target.isVisible() or target.window().isMinimized():
            self._finish();return
        if target.layout():target.layout().activate()
        self.overlay=_ContentOverlay(target,target.grab(),kind=self.kind,direction=self._direction,closing=self._closing)
        if self._closing:target.setEnabled(False)
        self.overlay.show();self.overlay.raise_()
        self._animation.setDuration(T.MOTION_DIALOG_CLOSE_MS if self._closing else T.MOTION_REVEAL_MS)
        self._animation.setStartValue(1. if self._closing else 0.)
        self._animation.setEndValue(0. if self._closing else 1.)
        self._animation.start()

    def _frame(self,value):
        if self.overlay is not None:self.overlay.progress=float(value);self.overlay.update()

    def _finish(self):
        callback,self._completed=self._completed,None
        self.cancel()
        if callback is not None:callback()

    def cancel(self):
        self._pending.stop();self._animation.stop();self._completed=None
        target,self.target=self.target,None
        if target is not None:
            target.removeEventFilter(self);target.window().removeEventFilter(self)
            if self._closing:target.setEnabled(self._target_enabled)
        self._closing=False
        overlay,self.overlay=self.overlay,None
        if overlay is not None:overlay.hide();overlay.snapshot=QPixmap();overlay.deleteLater()

    def eventFilter(self,obj,event):
        if self._closing and event.type()==QEvent.Close:
            event.ignore();return True
        if event.type() in (QEvent.Hide,QEvent.Close,QEvent.Resize,QEvent.WindowStateChange):
            if self._closing:self._finish()
            else:self.cancel()
        return False


class _StartupOverlay(QWidget):
    """Opaque ink/orange cover, circular aperture and cached workspace groups."""
    def __init__(self,window,content,title,author,*,phase='startup',snapshot=None):
        super().__init__(window)
        self.content=content;self.title=title;self.author=author;self.phase=phase
        self.elapsed=0.;self.snapshot=QPixmap() if snapshot is None else snapshot
        self.snapshot_origin=QPoint()
        self.setObjectName('converterStartupOverlay')
        self.setAttribute(Qt.WA_OpaquePaintEvent,True)
        self.setAttribute(Qt.WA_NoSystemBackground,False)
        self.setFocusPolicy(Qt.StrongFocus);self.setGeometry(window.rect())
        self._brand_font=QFont();self._brand_font.setFamilies(['Stop','Archivo Black','Inter','Microsoft YaHei UI'])
        self._brand_font.setPixelSize(max(24,min(52,int(window.width()/18))))
        self._small_font=QFont();self._small_font.setFamilies(['JetBrains Mono','Inter','HarmonyOS Sans SC','Microsoft YaHei UI']);self._small_font.setPixelSize(11)
        self._logo=brand_logo()
        self._bands=[]

    def capture(self):
        self.snapshot_origin=self.content.mapTo(self.parentWidget(),QPoint())
        self.snapshot=self.content.grab()
        layout=self.content.layout();centers=[]
        if layout:
            rectangles=[layout.itemAt(i).geometry() for i in range(layout.count()) if layout.itemAt(i).geometry().height()>0]
            centers=[(a.bottom()+b.top())/2+self.snapshot_origin.y() for a,b in zip(rectangles,rectangles[1:])]
        edges=[0.]+centers+[float(self.height())]
        self._bands=[QRectF(0,a,self.width(),b-a+1) for a,b in zip(edges,edges[1:])]

    def _registration(self,p,color,amount=1.):
        w,h=self.width(),self.height();cx,cy=w/2,h/2
        shade=QColor(color);shade.setAlpha(int(40*amount));p.setPen(QPen(shade,1));p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(cx-w*.22,cy),QPointF(cx+w*.22,cy))
        p.drawLine(QPointF(cx,cy-h*.22),QPointF(cx,cy+h*.22))
        for x,y,sx,sy in ((28,28,1,1),(w-28,28,-1,1),(28,h-28,1,-1),(w-28,h-28,-1,-1)):
            p.drawLine(QPointF(x,y),QPointF(x+12*sx,y));p.drawLine(QPointF(x,y),QPointF(x,y+12*sy))

    def _workspace(self,p,t,clip):
        bands=self._bands or [QRectF(self.rect())]
        for index,band in enumerate(bands):
            q=_out(_unit(t,T.MOTION_CONTENT_START_MS+index*30,T.MOTION_STARTUP_MS-(len(bands)-1-index)*30))
            if not q:continue
            p.save();p.setClipPath(clip);p.setClipRect(QRectF(band.x(),band.y(),band.width(),band.height()*q),Qt.IntersectClip)
            cx,cy=self.width()/2.,self.height()/2.
            p.translate(cx,cy+16*(1-q));p.scale(.985+.015*q,.985+.015*q);p.translate(-cx,-cy)
            p.drawPixmap(QPointF(self.snapshot_origin),self.snapshot);p.restore()

    def _startup(self,p):
        t=self.elapsed;w,h=self.width(),self.height();cx,cy=w/2.,h/2.
        self._registration(p,T.MOTION_CREAM)
        if t<650:
            p.setPen(QPen(QColor(T.MOTION_CREAM),1));p.setBrush(Qt.NoBrush)
            p.drawArc(QRectF(cx-54,cy-54,108,108),90*16,int(-_out(_unit(t,70,320))*275*16))
        circle=_smooth(_unit(t,280,650));radius=6+math.hypot(w,h)*.51*circle
        settle=_out(_unit(t,0,280));point=QPointF(cx-58*(1-settle),cy+18*(1-settle))
        p.setPen(Qt.NoPen);p.setBrush(QColor(T.MOTION_ORANGE));p.drawEllipse(point,radius,radius)
        if t>=390:
            self._registration(p,T.MOTION_INK,.7)
            title_progress=_out(_unit(t,410,660))
            p.save();p.setClipRect(QRectF(36,cy-49,(w-72)*title_progress,106))
            p.setPen(QColor(T.MOTION_INK));p.setFont(self._brand_font)
            p.drawText(QRectF(36,cy-40+18*(1-title_progress),w-72,70),Qt.AlignCenter,self.title);p.restore()
            p.save();p.setClipRect(QRectF(36,cy+40,(w-72)*_out(_unit(t,510,710)),30))
            p.setFont(self._small_font);p.setPen(QColor(T.MOTION_INK))
            p.drawText(QRectF(36,cy+41,w-72,24),Qt.AlignCenter,'ALPHA 1.0  /  '+self.author);p.restore()
            align=_out(_unit(t,390,640))
            p.save();p.translate(cx,cy-86-10*(1-align));p.rotate(12*(1-align))
            p.setOpacity(align)
            if not self._logo.isNull():
                size=64*(.9+.1*align)
                p.drawPixmap(QRectF(-size/2,-size/2,size,size),self._logo,QRectF(self._logo.rect()))
            p.restore()
            p.setPen(QPen(QColor(23,26,21,45),1));p.setBrush(Qt.NoBrush)
            p.drawArc(QRectF(cx-164,cy-164,328,328),int((40+t*.035)*16),100*16)
        if t>=820:
            q=_out(_unit(t,820,1150));card=QRectF(cx*(1-q),cy*(1-q),w*q,h*q)
            aperture=QPainterPath();aperture.addRoundedRect(card,24*(1-q),24*(1-q))
            p.fillPath(aperture,QColor(T.MOTION_CREAM))
            if not self.snapshot.isNull():self._workspace(p,t,aperture)

    def _shutdown(self,p):
        t=self.elapsed;w,h=self.width(),self.height();cx,cy=w/2.,h/2.
        p.fillRect(self.rect(),QColor(T.MOTION_CREAM))
        retreat=_out(_unit(t,0,130));p.save();p.translate(cx,cy+8*retreat);p.scale(1-.015*retreat,1-.015*retreat);p.translate(-cx,-cy)
        p.drawPixmap(QPointF(),self.snapshot);p.restore()
        p.setPen(Qt.NoPen);p.setBrush(QColor(T.MOTION_INK))
        for i in range(6):
            q=_smooth(_unit(t,100+i*35,255+i*35));width=w*q
            p.drawRect(QRectF(0 if i%2==0 else w-width,i*h/6.,width,h/6.+1))
        if t>=430:
            self._registration(p,T.MOTION_CREAM,.6)
            q=_out(_unit(t,430,570));vanish=1-_out(_unit(t,570,615))
            p.save();p.translate(cx,cy);p.rotate(12*(1-q));p.setOpacity(_out(_unit(t,430,490))*vanish)
            if not self._logo.isNull():
                size=(72-14*q)*vanish
                p.drawPixmap(QRectF(-size/2,-size/2,size,size),self._logo,QRectF(self._logo.rect()))
            p.restore()

    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),QColor(T.MOTION_INK));p.setRenderHint(QPainter.Antialiasing);p.setRenderHint(QPainter.SmoothPixmapTransform)
        if self.phase=='shutdown':self._shutdown(p)
        else:self._startup(p)
        p.end()


class MotionController(QObject):
    startup_finished=Signal()
    shutdown_finished=Signal()
    shutdown_failed=Signal(str)

    def __init__(self,window,content=None,*,title='TEXTURE CONVERTER',author='private0516',reduced_motion=None):
        super().__init__(window)
        self.window=window;self.content=content or window.centralWidget()
        if self.content is None:raise ValueError('MotionController requires the main content widget')
        self.title,self.author=title,author
        self.reduced_motion=reduced_motion_requested() if reduced_motion is None else bool(reduced_motion)
        self.state='new';self.closed=False;self.overlay=None;self._animation=None;self._close_callback=None;self._input_blocked=False
        self.window.installEventFilter(self)

    def _block_input(self,blocked):
        self._input_blocked=bool(blocked);app=QApplication.instance()
        if blocked:app.installEventFilter(self)
        else:app.removeEventFilter(self)

    def _suspend_widgets(self,suspended):
        for widget in self.content.findChildren(QWidget):
            method=getattr(widget,'set_motion_suspended',None)
            if method is not None:method(suspended)

    def _stop_animation(self):
        animation,self._animation=self._animation,None
        if animation is not None:animation.stop();animation.deleteLater()

    def _remove_overlay(self):
        overlay,self.overlay=self.overlay,None
        if overlay is not None:overlay.snapshot=QPixmap();overlay.hide();overlay.deleteLater()

    def _animate(self,duration,finished):
        animation=QVariantAnimation(self);animation.setStartValue(0.);animation.setEndValue(float(duration));animation.setDuration(duration)
        animation.setEasingCurve(QEasingCurve.Linear);animation.valueChanged.connect(self._frame);animation.finished.connect(finished)
        self._animation=animation;animation.start()

    def startup(self):
        if self.state!='new':return
        self.state='starting';self.window.setWindowOpacity(1.);self.window.ensurePolished()
        if self.window.layout():self.window.layout().activate()
        if self.reduced_motion:self.window.show();self._finish_startup();return
        self._suspend_widgets(True)
        self.overlay=_StartupOverlay(self.window,self.content,self.title,self.author)
        self._block_input(True);self.overlay.show();self.overlay.raise_();self.overlay.setFocus();self.window.show()
        self.overlay.setGeometry(self.window.rect());self.overlay.raise_()
        # show() resolves nested layouts; the opaque cover prevents a first-frame
        # flash while the now-correct workspace geometry is captured once.
        self.overlay.capture()
        self._animate(T.MOTION_STARTUP_MS,self._finish_startup)

    def _frame(self,value):
        if self.overlay is not None:self.overlay.elapsed=float(value);self.overlay.update()
        if self.state=='closing':self.window.setWindowOpacity(1.-_smooth(_unit(float(value),T.MOTION_SHUTDOWN_MS-80,T.MOTION_SHUTDOWN_MS)))

    def _finish_startup(self):
        if self.state!='starting':return
        self._stop_animation();self._block_input(False);self._remove_overlay();self._suspend_widgets(False)
        self.state='ready';self.content.update();self.startup_finished.emit()

    def shutdown(self,callback):
        if self.state in ('closing','closed','disposed'):return
        self.state='closing';self._close_callback=callback;self._stop_animation();self._block_input(True);self._suspend_widgets(True)
        if self.reduced_motion or not self.window.isVisible():self._finish_shutdown();return
        # Grabbing the window preserves an interrupted intro instead of flashing UI.
        snapshot=self.window.grab();self._remove_overlay()
        self.overlay=_StartupOverlay(self.window,self.content,self.title,self.author,phase='shutdown',snapshot=snapshot)
        self.overlay.show();self.overlay.raise_();self.overlay.setFocus()
        self._animate(T.MOTION_SHUTDOWN_MS,self._finish_shutdown)

    def _finish_shutdown(self):
        if self.state!='closing':return
        self._stop_animation();self._remove_overlay();self.closed=True;self.state='closed';self._block_input(False)
        callback,self._close_callback=self._close_callback,None
        try:
            if callback is not None:callback()
        except Exception as error:
            self.restore_after_close_error();self.shutdown_failed.emit(str(error));return
        self.shutdown_finished.emit()

    def restore_after_close_error(self):
        self._stop_animation();self._remove_overlay();self._close_callback=None;self._block_input(False)
        self.closed=False;self.state='ready';self.window.setWindowOpacity(1.);self._suspend_widgets(False);self.content.update()

    def dispose(self):
        self._stop_animation();self._remove_overlay();self._close_callback=None;self._block_input(False);self._suspend_widgets(True);self.state='disposed'

    def eventFilter(self,obj,event):
        if self._input_blocked and event.type() in (QEvent.MouseButtonPress,QEvent.MouseButtonRelease,QEvent.MouseButtonDblClick,QEvent.Wheel,QEvent.KeyPress,QEvent.KeyRelease,QEvent.Shortcut,QEvent.ShortcutOverride,QEvent.ContextMenu,QEvent.TouchBegin,QEvent.TouchUpdate,QEvent.TouchEnd):
            node=obj
            while node is not None:
                if node is self.window:return True
                node=node.parent()
        if obj is self.window and event.type()==QEvent.Resize and self.overlay is not None:self.overlay.setGeometry(self.window.rect())
        return False
