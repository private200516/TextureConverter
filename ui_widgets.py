# -*- coding: utf-8 -*-
"""
ui_widgets.py — 自定义绘制控件与动画。

- BannerWidget        工具品牌横幅：矢量字标 + 批量统计
- StripedProgressBar  条纹进度条：红色填充 + 流动斜条纹 + 完成脉冲
- StatusBarWidget     底部可视化状态栏：脉冲状态点 + 状态字 + 吞吐/计时/ETA + 条纹流动
- AnimatedNumber      数字滚动计数器（QVariantAnimation）

动效仅在可见且发生变化时驱动；日志批量绘制，闲置控件不保留帧定时器。
"""

from __future__ import annotations

import collections
import itertools
import math
import os
import time

from PySide6.QtCore import (QEasingCurve, QPropertyAnimation, Qt, QTimer,
                            QVariantAnimation, QRectF, QPointF, Signal, QEvent)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QLinearGradient,
                           QPainter, QPainterPath, QPen)
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

import ui_theme as T
from i18n import tr
from app_motion import reduced_motion_requested


def _display_font(px: int, weight=QFont.Black) -> QFont:
    """品牌/状态大字（Stop 复古几何体），中文自动回退鸿蒙黑体。"""
    f = QFont()
    f.setFamilies(["Stop", "Archivo Black", "HarmonyOS Sans SC",
                   "Microsoft YaHei UI"])
    f.setPixelSize(px)
    f.setWeight(weight)
    f.setStyleStrategy(QFont.PreferAntialias)
    return f


def _ui_font(px: int, weight=QFont.Normal) -> QFont:
    """普通 UI 字体：Inter（字重分档）+ 鸿蒙黑体回退。"""
    fam = {QFont.Medium: "Inter Medium",
           QFont.DemiBold: "Inter SemiBold"}.get(weight, "Inter")
    cn = "HarmonyOS Sans SC Medium" if weight in (QFont.Medium,
                                                  QFont.DemiBold) \
        else "HarmonyOS Sans SC"
    f = QFont()
    f.setFamilies([fam, cn, "Microsoft YaHei UI", "Microsoft YaHei"])
    f.setPixelSize(px)
    if weight == QFont.Bold:
        f.setWeight(QFont.Bold)
    f.setStyleStrategy(QFont.PreferAntialias)
    return f


def _mono_font(px: int, weight=QFont.Normal) -> QFont:
    """路径/数字/日志/数据：JetBrains Mono + 鸿蒙黑体回退。"""
    f = QFont()
    f.setFamilies(["JetBrains Mono", "HarmonyOS Sans SC",
                   "Consolas", "Microsoft YaHei"])
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


# ---------------------------------------------------------------------------
# 顶部横幅
# ---------------------------------------------------------------------------

class _MotionWidget(QWidget):
    """Wake only for visible, useful animation; no perpetual idle frame clock."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._motion_suspended = False
        self._reduced_motion = reduced_motion_requested()
        self._observed_window = None

    def set_motion_suspended(self, suspended):
        self._motion_suspended = bool(suspended)
        self._sync_motion()

    def set_reduced_motion(self, reduced):
        self._reduced_motion = bool(reduced)
        self.update()

    def _needs_motion(self):
        return bool(getattr(self, '_running', False))

    def _settle_motion(self):
        pass

    def _sync_motion(self):
        timer = getattr(self, '_timer', None)
        if timer is None: return
        if self._reduced_motion: self._settle_motion()
        active = (not self._reduced_motion and not self._motion_suspended
                  and self.isVisible() and not self.window().isMinimized() and self._needs_motion())
        if active and not timer.isActive(): timer.start()
        elif not active: timer.stop()

    def update(self, *args):
        self._sync_motion()
        super().update(*args)

    def showEvent(self, event):
        window = self.window()
        if self._observed_window is not window:
            if self._observed_window is not None: self._observed_window.removeEventFilter(self)
            self._observed_window = window
            window.installEventFilter(self)
        super().showEvent(event)
        self._sync_motion()

    def hideEvent(self, event):
        if getattr(self, '_timer', None) is not None: self._timer.stop()
        super().hideEvent(event)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.WindowStateChange, QEvent.Show): self._sync_motion()
        return False


class BannerWidget(_MotionWidget):
    """工具品牌横幅：自适应矢量标题、作者和批量计数。"""

    def __init__(self, icon_pm=None, author: str = "", parent=None):
        super().__init__(parent)
        self.setMinimumHeight(96)
        self.setMaximumHeight(96)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._phase = 0.0
        self._speed = 0.35          # 条纹漂移速度（周期/秒），转换时加快
        self._pulse = 0.0
        self._running = False
        self._author = author
        self._icon_pm = icon_pm
        self._icon_spin = 0.0       # >0 时图标旋转一周（热重载反馈）
        self._count_disp = 0.0      # 大数字显示值（滚动插值）
        self._count_target = 0
        self._count_from = 0.0
        self._count_started = 0.0
        self._total = 0
        self._timer = QTimer(self, interval=33, timeout=self._tick)

    # -- 图标（支持热重载）------------------------------------------------

    def set_icon(self, pm):
        if pm is not None and not pm.isNull():
            self._icon_pm = pm
            self.update()

    def reload_icon_feedback(self, pm):
        """图标来源变化时替换并旋转一周作为反馈。"""
        if pm is not None and not pm.isNull():
            self._icon_pm = pm
            self._icon_spin = 0.0
            self.update()

    def set_running(self, running: bool):
        self._running = running
        self._speed = 1.4 if running else 0.35
        self.update()

    def _tick(self):
        self._phase = (self._phase + self._speed * 0.033) % 1.0
        self._pulse = (self._pulse + 0.033) % 1.0
        if self._icon_spin > 0:
            self._icon_spin = max(0.0, self._icon_spin - 0.033 / 0.6)
        t = min(1., max(0., (time.monotonic()-self._count_started)/.24))
        self._count_disp = self._count_from + (self._count_target-self._count_from)*(1-(1-t)**3)
        if t >= 1.: self._count_disp = float(self._count_target)
        self.update()

    def set_count(self, done: int, total: int = 0):
        """左侧大数字（已转换数）滚动到目标值。"""
        if int(done) != self._count_target:
            self._count_from = self._count_disp
            self._count_started = time.monotonic()
            self._count_target = int(done)
        self._total = int(total)
        self.update()

    def _needs_motion(self):
        return abs(self._count_target-self._count_disp) > .001

    def _settle_motion(self):
        self._count_disp = float(self._count_target)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()

        p.fillRect(self.rect(), QColor(T.PAPER))
        # 白色圆角卡片（外间距由布局统一控制）
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T.WHITE))
        p.drawRoundedRect(QRectF(0, 0, w, h), 24, 24)

        pad = 26.0
        cy = h / 2

        # ---- 左侧：小标签 + 大号滚动数字 + 作者胶囊 ----
        p.setPen(QColor(T.INK_SOFT))
        p.setFont(_ui_font(10, QFont.Medium))
        p.drawText(QRectF(pad, 14, 240, 16), Qt.AlignLeft,
                   tr("banner_done"))
        num = int(round(self._count_disp))
        p.setPen(QColor(T.RED))
        big_font = _mono_font(34, QFont.Bold)
        p.setFont(big_font)
        p.drawText(QRectF(pad, 31, 260, 44),
                   Qt.AlignVCenter | Qt.AlignLeft, str(num))
        fm_big = QFontMetrics(big_font)
        nx = pad + fm_big.horizontalAdvance(str(num)) + 10
        if self._total > 0:
            p.setPen(QColor(T.INK_SOFT))
            p.setFont(_mono_font(13, QFont.Bold))
            p.drawText(QRectF(nx, 31, 140, 44),
                       Qt.AlignVCenter | Qt.AlignLeft, f"/ {self._total}")
        # Brand is vector text plus three marks; never an upscaled EXE icon.
        title = "TEXTURE CONVERTER"
        slash_w, slash_gap, slash_h = 7.0, 6.0, 16.0
        slashes_w = slash_w*3 + slash_gap*2
        stat_end = nx + (QFontMetrics(_mono_font(13,QFont.Bold)).horizontalAdvance(f"/ {self._total}") if self._total else 0)
        available = max(160., w-pad-max(240.,stat_end+28)-slashes_w-18)
        title_size = 30
        while title_size > 18 and QFontMetrics(_display_font(title_size)).horizontalAdvance(title)>available:
            title_size -= 1
        title_font = _display_font(title_size)
        tw = QFontMetrics(title_font).horizontalAdvance(title)
        group_w = tw + slashes_w + 18
        gx = w-pad-group_w; ty=cy-16
        p.setPen(Qt.NoPen);p.setBrush(QColor(T.RED));p.save()
        p.translate(gx+slashes_w/2,ty);p.rotate(18)
        for i in range(3):
            p.drawRoundedRect(QRectF(-slashes_w/2+i*(slash_w+slash_gap),-slash_h/2,slash_w,slash_h),3,3)
        p.restore()
        p.setFont(title_font);p.setPen(QColor(T.INK))
        p.drawText(QRectF(gx+slashes_w+18,ty-24,tw+8,48),Qt.AlignVCenter|Qt.AlignLeft,title)
        p.setPen(QColor(T.INK_SOFT));p.setFont(_ui_font(10,QFont.Medium))
        subtitle="FO76 → FO4 / BATCH MATERIAL PROCESSING"
        subtitle=QFontMetrics(p.font()).elidedText(subtitle,Qt.ElideMiddle,int(group_w))
        p.drawText(QRectF(gx,ty+28,group_w+4,16),Qt.AlignRight,subtitle)
        p.end()


# ---------------------------------------------------------------------------
# 条纹进度条
# ---------------------------------------------------------------------------

class StripedProgressBar(_MotionWidget):
    """黑框 + 红色填充 + 流动白条纹 + 大号百分比。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(46)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._value = 0.0            # 显示值（动画插值）
        self._target = 0.0
        self._from_value = 0.0
        self._changed_at = 0.0
        self._phase = 0.0
        self._running = False
        self._flash = 0.0            # 完成脉冲
        self._timer = QTimer(self, interval=33, timeout=self._tick)

    def set_progress(self, fraction: float, running: bool = True):
        target = max(0.0, min(1.0, fraction))
        if target != self._target:
            self._from_value = self._value
            self._changed_at = time.monotonic()
            self._target = target
        self._running = running
        if self._target >= 1.0:
            self._flash = 0.0
        self.update()

    def _needs_motion(self):
        return self._running or abs(self._target-self._value)>.00001 or self._flash>0

    def _settle_motion(self):
        self._value=self._target; self._flash=0.0

    def reset(self):
        self._value = self._target = 0.0
        self._running = False
        self._flash = 0.0
        self.update()

    def _tick(self):
        t = min(1., max(0., (time.monotonic()-self._changed_at)/.24))
        self._value = self._from_value + (self._target-self._from_value)*(1-(1-t)**3)
        if t >= 1.: self._value=self._target
        speed = 1.6 if self._running else 0.25
        self._phase = (self._phase + speed * 0.033) % 1.0
        if self._flash > 0:
            self._flash = max(0.0, self._flash - 0.02)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(T.PAPER))
        bar = QRectF(0, 6, w, h - 12)

        # 轨道
        p.fillRect(bar, QColor(T.PAPER_DIM))
        p.setPen(QPen(QColor(T.INK), 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(bar)

        # 填充
        fw = bar.width() * self._value
        if fw > 1:
            fill = QRectF(bar.x() + 1, bar.y() + 1, fw - 2, bar.height() - 2)
            p.fillRect(fill, QColor(T.RED))
            # 流动斜条纹
            p.save()
            p.setClipRect(fill)
            pen = QPen(QColor(255, 255, 255, 90))
            pen.setWidthF(5.0)
            p.setPen(pen)
            period = 22.0
            offset = self._phase * period
            x = fill.x() - fill.height() - period + offset
            while x < fill.x() + fill.width():
                p.drawLine(QPointF(x, fill.bottom() + 4),
                           QPointF(x + fill.height() + 8, fill.top() - 4))
                x += period
            p.restore()

        # 完成脉冲白闪
        if self._flash > 0:
            overlay = QColor(255, 255, 255, int(160 * self._flash))
            p.fillRect(bar, overlay)

        # 百分比（数字 → JetBrains Mono）
        pct = f"{int(round(self._value * 100)):3d}%"
        p.setFont(_mono_font(int(h * 0.42), QFont.Bold))
        color = QColor(T.WHITE) if self._value > 0.5 else QColor(T.INK)
        p.setPen(color)
        p.drawText(bar.adjusted(10, 0, -10, 0),
                   Qt.AlignVCenter | Qt.AlignRight, pct)
        p.end()


# ---------------------------------------------------------------------------
# 底部可视化状态栏
# ---------------------------------------------------------------------------

class StatusBarWidget(_MotionWidget):
    """
    黑色状态栏：脉冲状态点 + 状态字 + 统计（吞吐/用时/ETA）+ 右端流动条纹。
    set_state('idle' | 'scanning' | 'running' | 'done' | 'error')
    """

    STATE_TEXT = {
        "idle": ("STANDBY", QColor(T.LINE)),
        "scanning": ("SCANNING", QColor(T.AMBER)),
        "running": ("CONVERTING", QColor(T.RED)),
        "done": ("COMPLETE", QColor(T.GREEN_OK)),
        "error": ("ERROR", QColor(T.RED_DARK)),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(54)
        self.setMaximumHeight(54)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._state = "idle"
        self._phase = 0.0
        self._pulse = 0.0
        self._running = False
        self._stats = ""
        self._t0 = None
        self._done = 0
        self._btns: list = []
        self._dock_left: float | None = None
        self._timer = QTimer(self, interval=33, timeout=self._tick)

    # ---- 右侧按钮挂载（覆盖在条纹装饰区上） ----

    def attach_buttons(self, *btns):
        self._btns = list(btns)
        for b in self._btns:
            b.setParent(self)
            b.show()
        self._layout_buttons()

    def resizeEvent(self, e):
        self._layout_buttons()
        super().resizeEvent(e)

    def _layout_buttons(self):
        if not self._btns:
            return
        x = self.width() - 18          # 右缘呼吸位，按钮不再贴边
        bh = 36
        cy = (self.height() - bh) // 2
        for b in reversed(self._btns):
            bw = max(b.sizeHint().width(), b.minimumWidth(), 120)
            b.setGeometry(int(x - bw), int(cy), int(bw), bh)
            x -= bw + 12
        self._dock_left = x + 4          # 黑底板左缘（含 6px 内边距）
        self.update()

    def set_state(self, state: str):
        if state == self._state: return
        self._state = state
        self._running = state in ("scanning", "running")
        if state == "running":
            self._t0 = time.time()
            self._done = 0
        elif state in ("idle", "done", "error"):
            self._t0 = None
        self.update()

    def set_progress_info(self, done: int, total: int):
        self._done = done
        self._total = total
        self.update()

    def _tick(self):
        speed = 2.2 if self._running else 0.4
        self._phase = (self._phase + speed * 0.033) % 1.0
        self._pulse = (self._pulse + 0.033) % 1.0
        if self._running or self._state in ("done",):
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # 薰衣草底 + 白色胶囊条（外间距由布局统一控制）
        p.fillRect(0, 0, w, h, QColor(T.PAPER))
        bar = QRectF(0, 0, w, h)
        radius = h / 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T.WHITE))
        p.drawRoundedRect(bar, radius, radius)

        # 顶部发丝进度线（红色，随转换进度填充）
        clip = QPainterPath()
        clip.addRoundedRect(bar, radius, radius)
        p.save()
        p.setClipPath(clip)
        p.fillRect(QRectF(0, 0, w, 3), QColor(T.PAPER_DIM))
        frac = 0.0
        if getattr(self, "_total", 0):
            frac = min(1.0, self._done / max(1, self._total))
        if self._state == "done":
            frac = 1.0
        if frac > 0.001:
            p.fillRect(QRectF(0, 0, w * frac, 3), QColor(T.RED))
        p.restore()

        # 左端脉冲状态点
        _, color = self.STATE_TEXT.get(self._state, self.STATE_TEXT["idle"])
        cy = h / 2
        pulse = 0.5 + 0.5 * math.sin(self._pulse * 2 * math.pi)
        base_r = 6.0
        halo_r = base_r + 5 * pulse
        halo = QColor(color)
        halo.setAlpha(int(70 * (1 - pulse)) if self._running else 40)
        p.setPen(Qt.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QPointF(30, cy), halo_r, halo_r)
        p.setBrush(color)
        p.drawEllipse(QPointF(30, cy), base_r, base_r)

        # 品牌小字
        p.setPen(QColor(T.INK_SOFT))
        p.setFont(_mono_font(10, QFont.Bold))
        p.drawText(QRectF(48, 0, 300, h), Qt.AlignVCenter | Qt.AlignLeft,
                   "FO76 → FO4 · TEXTURE CONVERTER")
        p.end()


# ---------------------------------------------------------------------------
# 详尽进程状态面板（主页）
# ---------------------------------------------------------------------------

class ProcessPanel(QWidget):
    """Compact 72px conversion status: state, file, count, warnings and errors."""
    STATE_TEXT = {'idle':'STANDBY','scanning':'SCANNING','running':'CONVERTING','done':'COMPLETE','error':'ERROR'}
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(72)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._state='idle'; self._done=0; self._total=0; self._warn=0; self._err=0; self._file=''

    def set_stage(self, state):
        if state == self._state: return
        self._state=state
        if state=='idle': self._done=self._total=self._warn=self._err=0; self._file=''
        elif state=='running': self._done=0
        self.update()

    def set_progress(self, done, total, name=''):
        self._done,self._total=max(0,int(done)),max(0,int(total))
        if name: self._file=name
        self.update()

    def set_counters(self, warn, err):
        self._warn,self._err=max(0,int(warn)),max(0,int(err)); self.update()

    def paintEvent(self, event):
        p=QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(),QColor(T.PAPER))
        rect=QRectF(0,0,self.width(),self.height())
        path=QPainterPath(); path.addRoundedRect(rect,18,18)
        p.fillPath(path,QColor(T.WHITE)); p.setClipPath(path)
        p.setPen(QColor(T.RED)); p.setFont(_display_font(24))
        label=self.STATE_TEXT.get(self._state,'STANDBY')
        p.drawText(QRectF(22,5,360,35),Qt.AlignLeft|Qt.AlignVCenter,label)
        p.setPen(QColor(T.INK_SOFT)); p.setFont(_mono_font(11))
        stats=f'{self._done} / {self._total}     WARN {self._warn}     ERR {self._err}'
        p.drawText(QRectF(22,42,self.width()-44,19),Qt.AlignLeft|Qt.AlignVCenter,stats)
        p.setFont(_mono_font(11))
        width=max(30,self.width()-430)
        filename=QFontMetrics(p.font()).elidedText(self._file or '—',Qt.ElideMiddle,width)
        p.drawText(QRectF(400,10,width,25),Qt.AlignRight|Qt.AlignVCenter,filename)
        fraction=min(1.,self._done/max(1,self._total)) if self._total else 0.
        if self._state=='done' and self._total: fraction=1.
        track=QColor(T.PAPER_DIM)
        p.fillRect(QRectF(0,self.height()-3,self.width(),3),track)
        p.fillRect(QRectF(0,self.height()-3,self.width()*fraction,3),QColor(T.RED))
        p.end()


class LiveLogWidget(_MotionWidget):
    """Bounded live queue, batch append, and user-controlled bottom following."""
    MAX_ENTRIES=2000
    LINE_H=22
    ARRIVAL_SECONDS=.18
    LEVEL_COLORS={'OK':QColor(T.GREEN_OK),'GENERATED':QColor(T.RED),'COPIED':QColor(T.PAPER_DIM),
                  'WARNING':QColor(T.AMBER),'ERROR':QColor(T.RED_DARK),'SKIPPED':QColor(T.INK_SOFT),'INFO':QColor(T.PAPER_DIM)}

    def __init__(self,parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Expanding)
        self.setMinimumHeight(140)
        self._entries=collections.deque(maxlen=self.MAX_ENTRIES)
        self._scroll=0; self._running=False; self._total_entries=0
        self._timer=QTimer(self,interval=33,timeout=self._tick)

    def append_entries(self, entries):
        now=time.monotonic(); count=0
        for entry in entries:
            level,path,*rest=entry
            message=rest[0] if rest else ''
            text=f'{path}   —   {message}' if message else str(path)
            self._entries.append((str(level),text,now)); count+=1
        if not count: return
        self._total_entries+=count
        if self._scroll:
            self._scroll=min(max(0,len(self._entries)-self._visible_count()),self._scroll+count)
        self.update()

    def append_entry(self,level,rel_path,message=''):
        self.append_entries([(level,rel_path,message)])

    def append_system(self,text): self.append_entries([('INFO',text,'')])

    def clear_log(self):
        self._entries.clear(); self._scroll=0; self._total_entries=0; self.update()

    def set_running(self,running): self._running=bool(running); self.update()

    def _needs_motion(self):
        return bool(self._entries and self._scroll==0 and time.monotonic()-self._entries[-1][2]<self.ARRIVAL_SECONDS)

    def _tick(self): self.update()

    def _visible_count(self): return max(1,(self.height()-46)//self.LINE_H)

    def wheelEvent(self,event):
        steps=event.angleDelta().y()/120
        limit=max(0,len(self._entries)-self._visible_count())
        self._scroll=max(0,min(limit,self._scroll+int(steps*3)))
        self.update(); event.accept()

    def paintEvent(self,event):
        p=QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        w,h=self.width(),self.height(); p.fillRect(self.rect(),QColor(T.PAPER))
        path=QPainterPath(); path.addRoundedRect(QRectF(0,0,w,h),18,18)
        p.fillPath(path,QColor(T.INK)); p.setClipPath(path)
        now=time.monotonic(); visible=self._visible_count()
        start=max(0,len(self._entries)-visible-self._scroll)
        rows=itertools.islice(self._entries,start,start+visible)
        text_font=_mono_font(11); tag_font=_mono_font(10,QFont.Bold)
        fm=QFontMetrics(text_font); fm_tag=QFontMetrics(tag_font)
        for i,(level,text,appeared) in enumerate(rows):
            age=max(0.,now-appeared)
            alpha=1. if self._reduced_motion or self._scroll else min(1.,age/self.ARRIVAL_SECONDS)
            ease=1.-(1.-alpha)**3; y=36+i*self.LINE_H+(1-ease)*6
            p.setOpacity(ease)
            color=self.LEVEL_COLORS.get(level,QColor(T.PAPER_DIM))
            tag=f'[{level}]'; tw=fm_tag.horizontalAdvance(tag)+14
            chip=QRectF(14,y+2,tw,17); bg=QColor(color); bg.setAlpha(35)
            p.setPen(Qt.NoPen); p.setBrush(bg); p.drawRoundedRect(chip,7,7)
            p.setPen(color); p.setFont(tag_font); p.drawText(chip,Qt.AlignCenter,tag)
            x=chip.right()+12; width=max(0,w-x-16)
            p.setFont(text_font); p.setPen(QColor(T.PAPER_DIM if level not in ('ERROR','WARNING') else T.WHITE))
            p.drawText(QRectF(x,y,width,self.LINE_H),Qt.AlignVCenter|Qt.AlignLeft,fm.elidedText(text,Qt.ElideMiddle,int(width)))
        p.setOpacity(1.)
        if not self._entries:
            p.setFont(text_font); p.setPen(QColor(T.INK_SOFT))
            p.drawText(QRectF(0,34,w,h-34),Qt.AlignCenter,tr('log_empty'))
        p.fillRect(QRectF(0,0,w,30),QColor(T.INK))
        p.setPen(Qt.NoPen); p.setBrush(QColor(T.RED if self._running else T.INK_SOFT)); p.drawEllipse(QPointF(19,17),3,3)
        p.setFont(tag_font); p.setPen(QColor(T.PAPER_DIM))
        p.drawText(QRectF(31,7,220,20),Qt.AlignVCenter|Qt.AlignLeft,'LOG / LIVE')
        label=tr('log_scrollback',n=self._scroll) if self._scroll else tr('log_lines',n=self._total_entries)
        p.drawText(QRectF(230,7,max(0,w-246),20),Qt.AlignVCenter|Qt.AlignRight,label)
        p.end()


class PillSelector(QWidget):
    """
    胶囊单选器（对应参考图的 S/M/L 药丸）：
    红色选中胶囊在选项间滑动（260ms OutCubic），点击切换。
    """
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self._keys = [k for k, _ in options]
        self._labels = [lb for _, lb in options]
        self._index = 0
        self._pos = 0.0                 # 胶囊动画位置（浮点索引）
        self._tooltips: dict[str, str] = {}
        self.setMinimumHeight(40)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    # ---- 外部接口 ----

    def current_key(self) -> str:
        return self._keys[self._index]

    def set_key(self, key: str, animate: bool = True):
        if key not in self._keys:
            return
        idx = self._keys.index(key)
        if idx == self._index:
            return
        self._index = idx
        if animate and self.isVisible() and not reduced_motion_requested():
            self._anim.stop()
            self._anim.setStartValue(self._pos)
            self._anim.setEndValue(float(idx))
            self._anim.start()
        else:
            self._anim.stop()
            self._pos = float(idx)
            self.update()
        self.changed.emit(key)

    def set_tooltips(self, tips: dict[str, str]):
        self._tooltips = tips

    def set_labels(self, labels: list[str]):
        """切换语言时更新胶囊文字（键不变，选中态不变）。"""
        if len(labels) != len(self._keys):
            return
        self._labels = list(labels)
        self.updateGeometry()
        self.update()

    def _on_anim(self, v):
        self._pos = float(v)
        self.update()

    # ---- 几何 ----

    def _pill_widths(self) -> list[float]:
        fm = QFontMetrics(_ui_font(12, QFont.Bold))
        return [fm.horizontalAdvance(lb) + 36 for lb in self._labels]

    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(int(sum(self._pill_widths()) + 8), 40)

    def minimumSizeHint(self):
        return self.sizeHint()

    def _pill_rect(self, i: float) -> QRectF:
        """第 i 个（支持浮点）胶囊的外框。"""
        widths = self._pill_widths()
        i0 = int(i)
        frac = i - i0
        x0 = 4 + sum(widths[:i0])
        w0 = widths[i0]
        if frac > 0.001 and i0 + 1 < len(widths):
            x1 = 4 + sum(widths[:i0 + 1])
            w1 = widths[i0 + 1]
            x = x0 + (x1 - x0) * frac
            ww = w0 + (w1 - w0) * frac
        else:
            x, ww = x0, w0
        return QRectF(x, 4, ww, self.height() - 8)

    # ---- 交互 ----

    def mousePressEvent(self, e):
        x = e.position().x()
        widths = self._pill_widths()
        acc = 4.0
        for i, pw in enumerate(widths):
            if acc <= x <= acc + pw:
                self.set_key(self._keys[i])
                tip = self._tooltips.get(self._keys[i])
                if tip:
                    self.setToolTip(tip)
                break
            acc += pw
        super().mousePressEvent(e)

    # ---- 绘制 ----

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        h = self.height()

        p.fillRect(self.rect(), QColor(T.PAPER))
        # 红色选中胶囊（滑动）
        pill = self._pill_rect(self._pos)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T.RED))
        p.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)

        # 标签（未选中 Inter SemiBold / 选中 Inter Bold）
        for i, lb in enumerate(self._labels):
            r = self._pill_rect(float(i))
            # 与选中胶囊的重合度决定文字颜色
            inter = r.intersected(pill)
            cov = (inter.width() / r.width()) if r.width() > 0 else 0
            active = cov > 0.5
            p.setFont(_ui_font(12, QFont.Bold if active else QFont.DemiBold))
            p.setPen(QColor(T.WHITE) if active else QColor(T.INK))
            p.drawText(r, Qt.AlignCenter, lb)
        p.end()


# ---------------------------------------------------------------------------
# 动画数字
# ---------------------------------------------------------------------------

class AnimatedNumber(QLabel):
    """数值变化时滚动计数（0.45s OutCubic）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0
        self.setText("0")
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(450)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_val)

    def set_value(self, v: int, animate: bool = True):
        v = int(v)
        if not animate or not self.isVisible() or reduced_motion_requested() or v == self._value:
            self._anim.stop()
            self._value = v
            self.setText(str(v))
            return
        self._anim.stop()
        self._anim.setStartValue(self._value)
        self._anim.setEndValue(v)
        self._value = v
        self._anim.start()

    def _on_val(self, x):
        self.setText(str(int(x)))


# ---------------------------------------------------------------------------
# 动力学跑马灯带
# ---------------------------------------------------------------------------

class MarqueeWidget(_MotionWidget):
    """无缝滚动的动力学文字带（kinetic marquee），转换时加速。"""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._text = text
        self._phase = 0.0
        self._running = False
        self.setMinimumHeight(30)
        self.setMaximumHeight(30)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._timer = QTimer(self, interval=33, timeout=self._tick)

    def set_running(self, running: bool):
        self._running = running
        self.update()

    def _tick(self):
        speed = 1.8 if self._running else 0.5
        self._phase = (self._phase + speed * 0.033 * 60) % 100000
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(T.INK))
        p.setFont(_mono_font(12, QFont.Bold))
        fm = QFontMetrics(p.font())
        unit = self._text + "   "
        uw = max(1, fm.horizontalAdvance(unit))
        x = -(self._phase % uw)
        i = 0
        while x < w:
            # 交替白 / 红，增强节奏感
            p.setPen(QColor(T.RED) if i % 2 else QColor(T.PAPER))
            p.drawText(int(x), 0, uw, h, Qt.AlignVCenter | Qt.AlignLeft, unit)
            x += uw
            i += 1
        p.end()


# ---------------------------------------------------------------------------
# 悬停扫掠按钮
# ---------------------------------------------------------------------------

class SweepButton(QWidget):
    """
    自绘按钮：悬停时红色斜切扫掠填充 + 文字反色 + 箭头滑动。
    primary=True 时底色为红，扫掠为黑。
    """
    clicked = Signal()

    def __init__(self, text: str, primary: bool = False, arrow: bool = False,
                 parent=None):
        super().__init__(parent)
        self._text = text
        self._primary = primary
        self._arrow = arrow
        self._sweep = 0.0
        self._hover = False
        self._enabled = True
        self.setMinimumHeight(40)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_sweep)

    def sizeHint(self):
        font = _ui_font(13, QFont.Bold) if self._primary else _ui_font(12, QFont.Bold)
        fm = QFontMetrics(font)
        label = ("▶  " + self._text) if self._arrow else self._text
        from PySide6.QtCore import QSize
        return QSize(fm.horizontalAdvance(label) + 48, 40)

    def minimumSizeHint(self):
        return self.sizeHint()

    def setEnabled(self, on: bool):  # noqa: N802
        self._enabled = bool(on)
        super().setEnabled(on)
        self.update()

    def set_text(self, text: str):
        """切换语言时更新按钮文字。"""
        self._text = text
        self.updateGeometry()
        self.update()

    def _on_sweep(self, v):
        self._sweep = float(v)
        self.update()

    def _animate_to(self, target: float):
        self._anim.stop()
        if reduced_motion_requested() or not self.isVisible():
            self._sweep=target; self.update(); return
        self._anim.setStartValue(self._sweep)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, e):
        if self._enabled:
            self._hover = True
            self._animate_to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self._animate_to(0.0)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if self._enabled and e.button() == Qt.LeftButton:
            self._sweep = 1.0
            self.update()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if self._enabled and e.button() == Qt.LeftButton and self.rect().contains(e.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(T.PAPER))
        rect = QRectF(1, 1, w - 2, h - 2)
        radius = (h - 2) / 2

        if not self._enabled:
            base, fg, border = QColor(T.PAPER_DIM), QColor(T.INK_SOFT), QColor(T.PAPER_DIM)
        elif self._primary:
            base, fg, border = QColor(T.RED), QColor(T.WHITE), QColor(T.RED_DARK)
        else:
            base, fg, border = QColor(T.WHITE), QColor(T.INK), QColor(T.LINE)

        # 胶囊底 + 细描边
        p.setPen(Qt.NoPen)
        p.setBrush(base)
        p.drawRoundedRect(rect, radius, radius)
        p.setPen(QPen(border, 2))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect, radius, radius)

        # 斜切扫掠层（裁切到胶囊形）
        if self._sweep > 0.001 and self._enabled:
            sweep_color = QColor(T.INK) if self._primary else QColor(T.RED)
            sw = (w + 40) * self._sweep
            path = QPainterPath()
            path.moveTo(0, 0)
            path.lineTo(sw, 0)
            path.lineTo(sw - 24, h)
            path.lineTo(0, h)
            path.closeSubpath()
            clip = QPainterPath()
            clip.addRoundedRect(rect, radius, radius)
            p.save()
            p.setClipPath(clip)
            p.fillPath(path, sweep_color)
            p.restore()

        # 文字（扫掠覆盖处反色）
        font = _ui_font(13, QFont.Bold) if self._primary else _ui_font(12, QFont.Bold)
        p.setFont(font)
        label = ("▶  " + self._text) if self._arrow else self._text
        # 箭头随悬停右移
        dx = int(4 * self._sweep) if self._arrow else 0
        tw = QFontMetrics(font).horizontalAdvance(label)
        tx = (w - tw) / 2 + dx
        # 被扫掠层覆盖的部分用底色
        split_x = (w + 40) * self._sweep - 24
        if self._sweep > 0.001 and self._enabled:
            p.save()
            p.setClipRect(QRectF(0, 0, split_x, h))
            p.setPen(QColor(T.WHITE) if not self._primary else QColor(T.WHITE))
            p.drawText(QRectF(tx, 0, tw + 20, h), Qt.AlignVCenter, label)
            p.restore()
            p.save()
            p.setClipRect(QRectF(split_x, 0, w - split_x, h))
            p.setPen(fg)
            p.drawText(QRectF(tx, 0, tw + 20, h), Qt.AlignVCenter, label)
            p.restore()
        else:
            p.setPen(fg)
            p.drawText(QRectF(tx, 0, tw + 20, h), Qt.AlignVCenter, label)
        p.end()


# ---------------------------------------------------------------------------
# 启动画面
# ---------------------------------------------------------------------------

