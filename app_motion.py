"""Single native window transitions shared by the converter toolkit.

Call startup() before showing the window. startup_finished is the point at
which a first-run license dialog may open. shutdown(callback) completes once;
the host accepts its next closeEvent when controller.closed is True.
"""
from __future__ import annotations
import os
import sys
from PySide6.QtCore import QObject, QEvent, QPoint, QRectF, Qt, Signal, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QWidget, QApplication
import ui_theme as T


def reduced_motion_requested() -> bool:
    """Explicit test/user setting wins, then Windows client-area animation preference."""
    value = os.environ.get('CONVERTER_REDUCED_MOTION', os.environ.get('TEXTURE_CONVERTER_REDUCED_MOTION'))
    if value is not None:
        return value.strip().lower() not in ('', '0', 'false', 'no', 'off')
    if sys.platform == 'win32':
        try:
            import ctypes
            enabled = ctypes.c_int(1)
            if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
                return not bool(enabled.value)
        except (AttributeError, OSError):
            pass
    return False


class _StartupOverlay(QWidget):
    """Fully repaints every client pixel; no translucent/transparent backing store."""
    def __init__(self, window, content, title, author):
        super().__init__(window)
        self.content = content
        self.title = title
        self.author = author
        self.elapsed = 0.0
        self.snapshot = QPixmap()
        self.snapshot_origin = QPoint()
        self.setObjectName('converterStartupOverlay')
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WA_NoSystemBackground, False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setGeometry(window.rect())

    def capture(self):
        # The overlay is a sibling of content, never part of this grab.
        self.snapshot_origin = self.content.mapTo(self.parentWidget(), QPoint())
        self.snapshot = self.content.grab()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(T.MOTION_CREAM))
        p.setRenderHint(QPainter.Antialiasing)
        t = self.elapsed
        if t >= T.MOTION_CONTENT_START_MS and not self.snapshot.isNull():
            alpha = min(1., (t - T.MOTION_CONTENT_START_MS) / T.MOTION_CONTENT_FADE_MS)
            p.setOpacity(1. - (1. - alpha) ** 3)
            p.drawPixmap(self.snapshot_origin, self.snapshot)
            p.setOpacity(1.)
        title_alpha = max(0., min(1., (T.MOTION_CONTENT_START_MS - t) / (T.MOTION_CONTENT_START_MS - T.MOTION_PROGRESS_MS)))
        if title_alpha:
            p.setOpacity(title_alpha)
            w, h = self.width(), self.height()
            font = QFont(); font.setFamilies(['Stop', 'Archivo Black', 'Inter', 'Microsoft YaHei UI'])
            font.setPixelSize(max(20, min(34, int(w / 25))))
            p.setFont(font); p.setPen(QColor(T.MOTION_ORANGE))
            p.drawText(QRectF(24, h/2-48, w-48, 44), Qt.AlignCenter, self.title)
            font.setFamilies(['Inter', 'HarmonyOS Sans SC', 'Microsoft YaHei UI']); font.setPixelSize(11)
            p.setFont(font); p.setPen(QColor(T.INK_SOFT))
            p.drawText(QRectF(24, h/2+5, w-48, 22), Qt.AlignCenter, 'ALPHA 1.0  /  '+self.author)
            width = min(220., w*.36); x=(w-width)/2; y=h/2+50
            p.fillRect(QRectF(x,y,width,2), QColor(T.PAPER_DIM))
            progress = max(0.,min(1., t/T.MOTION_PROGRESS_MS))
            p.fillRect(QRectF(x,y,width*(1-(1-progress)**3),2), QColor(T.MOTION_ORANGE))
        p.end()


class MotionController(QObject):
    startup_finished = Signal()
    shutdown_finished = Signal()

    def __init__(self, window, content=None, *, title='TEXTURE CONVERTER', author='private0516', reduced_motion=None):
        super().__init__(window)
        self.window = window
        self.content = content or window.centralWidget()
        if self.content is None:
            raise ValueError('MotionController requires the main content widget')
        self.title, self.author = title, author
        self.reduced_motion = reduced_motion_requested() if reduced_motion is None else bool(reduced_motion)
        self.state = 'new'
        self.closed = False
        self.overlay = None
        self._animation = None
        self._close_callback = None
        self._input_blocked = False
        self.window.installEventFilter(self)

    def _block_input(self, blocked):
        self._input_blocked = bool(blocked)
        app = QApplication.instance()
        if blocked: app.installEventFilter(self)
        else: app.removeEventFilter(self)

    def _suspend_widgets(self, suspended):
        for widget in self.content.findChildren(QWidget):
            method = getattr(widget, 'set_motion_suspended', None)
            if method is not None:
                method(suspended)

    def _stop_animation(self):
        animation, self._animation = self._animation, None
        if animation is not None:
            animation.stop()
            animation.deleteLater()

    def _remove_overlay(self):
        overlay, self.overlay = self.overlay, None
        if overlay is not None:
            overlay.snapshot = QPixmap()
            overlay.hide()
            overlay.deleteLater()

    def startup(self):
        if self.state != 'new':
            return
        self.state = 'starting'
        self.window.setWindowOpacity(1.)
        self.window.ensurePolished()
        if self.window.layout(): self.window.layout().activate()
        if self.reduced_motion:
            self.window.show()
            self._finish_startup()
            return
        self._suspend_widgets(True)
        self.overlay = _StartupOverlay(self.window, self.content, self.title, self.author)
        self.overlay.capture()
        self._block_input(True)
        self.overlay.show(); self.overlay.raise_(); self.overlay.setFocus()
        self.window.show()
        self.overlay.setGeometry(self.window.rect())
        self.overlay.raise_()
        animation = QVariantAnimation(self)
        animation.setStartValue(0.); animation.setEndValue(float(T.MOTION_STARTUP_MS))
        animation.setDuration(T.MOTION_STARTUP_MS)
        animation.setEasingCurve(QEasingCurve.Linear)
        animation.valueChanged.connect(self._frame)
        animation.finished.connect(self._finish_startup)
        self._animation = animation
        animation.start()

    def _frame(self, value):
        if self.overlay is not None:
            self.overlay.elapsed = float(value)
            self.overlay.update()

    def _finish_startup(self):
        if self.state != 'starting': return
        self._stop_animation()
        self._block_input(False)
        self._remove_overlay()
        self._suspend_widgets(False)
        self.state = 'ready'
        self.content.update()
        self.startup_finished.emit()

    def shutdown(self, callback):
        if self.state in ('closing','closed','disposed'): return
        self.state = 'closing'
        self._close_callback = callback
        self._stop_animation()
        # Freeze an in-progress startup cover; exposing content here would flash.
        self._block_input(True)
        self._suspend_widgets(True)
        if self.reduced_motion or not self.window.isVisible():
            self._finish_shutdown()
            return
        animation = QVariantAnimation(self)
        animation.setStartValue(self.window.windowOpacity()); animation.setEndValue(0.)
        animation.setDuration(T.MOTION_SHUTDOWN_MS)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.valueChanged.connect(self.window.setWindowOpacity)
        animation.finished.connect(self._finish_shutdown)
        self._animation = animation
        animation.start()

    def _finish_shutdown(self):
        if self.state != 'closing': return
        self._stop_animation()
        self._remove_overlay()
        self.closed = True
        self.state = 'closed'
        self._block_input(False)
        callback, self._close_callback = self._close_callback, None
        self.shutdown_finished.emit()
        if callback is not None: callback()

    def dispose(self):
        """Cancel pending animations without invoking host callbacks."""
        self._stop_animation(); self._remove_overlay()
        self._close_callback = None
        self._block_input(False)
        self._suspend_widgets(True)
        self.state = 'disposed'

    def eventFilter(self, obj, event):
        if self._input_blocked and event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick, QEvent.Wheel, QEvent.KeyPress, QEvent.KeyRelease, QEvent.Shortcut, QEvent.ShortcutOverride, QEvent.ContextMenu, QEvent.TouchBegin, QEvent.TouchUpdate, QEvent.TouchEnd):
            node = obj
            while node is not None:
                if node is self.window: return True
                node = node.parent()
        if obj is self.window and event.type() == QEvent.Resize and self.overlay is not None:
            self.overlay.setGeometry(self.window.rect())
        return False
