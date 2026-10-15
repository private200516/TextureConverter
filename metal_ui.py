"""Incremental Qt metal-analysis panel and local source-resolution mask editor."""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
import traceback
import threading
import numpy as np
from PIL import Image
from PySide6.QtCore import Qt, QThread, Signal, QRectF, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QWidget, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QPushButton, QComboBox, QDoubleSpinBox, QSpinBox, QCheckBox, QListWidget,
    QListWidgetItem, QLineEdit, QSplitter, QFileDialog, QMessageBox, QAbstractItemView,
    QDialogButtonBox, QGroupBox, QScrollArea)
import i18n
from mask_model import MaskModel


def t(zh, en): return zh if i18n.LANG == 'zh' else en
def job_value(job, key, default=None):
    return job.get(key, default) if isinstance(job, dict) else getattr(job, key, default)
def job_id(job):
    return str(job_value(job, 'id', job_value(job, 'job_id', job_value(job, 'material_path', job_value(job, 'name', 'job')))))
def job_name(job):
    return str(job_value(job, 'name', job_value(job, 'relative_path', job_value(job, 'material_path', job_id(job)))))


class Work(QThread):
    done = Signal(object)
    failed = Signal(str)
    def __init__(self, fn, parent=None): super().__init__(parent); self.fn = fn
    def run(self):
        try: self.done.emit(self.fn())
        except Exception: self.failed.emit(traceback.format_exc())


def thumbnail(array, side=900):
    if isinstance(array, (str, Path)):
        with Image.open(array) as im: a = im.convert('RGBA').copy()
    else:
        data = np.asarray(array)
        if data.dtype != np.uint8: data = np.rint(np.clip(data, 0, 1)*255).astype(np.uint8)
        a = Image.fromarray(data).convert('RGBA')
    a.thumbnail((side, side), Image.Resampling.BILINEAR)
    data = np.asarray(a).copy()
    return QImage(data.data, data.shape[1], data.shape[0], data.strides[0], QImage.Format_RGBA8888).copy()


class MaskCanvas(QWidget):
    changed = Signal()
    error = Signal(str)
    def __init__(self):
        super().__init__(); self.setMinimumSize(350, 320)
        self.setMouseTracking(True); self.model = None; self.image = None
        self.layer = 'edit'; self.radius = 12; self.erase = False; self.paint_enabled = False
        self.dragging = False; self.last = None; self.rect = QRectF()
        self.setToolTip(t('预览可缩小；画笔半径始终按源纹理像素计算。绿色为编辑区，红色为保护区。',
            'Preview can be scaled; brush radius uses source pixels. Green edits, red protects.'))

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.contentsRect(), QColor('#252521'))
        if self.image is None:
            p.setPen(QColor('#d4d1c8')); p.drawText(self.contentsRect(), Qt.AlignCenter,
                t('选择材质并生成本地预览', 'Select a material and build a local preview')); return
        size = self.image.size(); size.scale(self.width()-20, self.height()-20, Qt.KeepAspectRatio)
        self.rect = QRectF((self.width()-size.width())/2, (self.height()-size.height())/2, size.width(), size.height())
        p.drawImage(self.rect, self.image)
        if self.paint_enabled and self.model:
            h,w = self.model.shape; stride = max(1, int(max(h,w)/900))
            edits = self.model.layers['edit'][::stride,::stride]
            protect = self.model.layers['protect'][::stride,::stride]
            overlay = np.zeros((*edits.shape,4), np.uint8)
            overlay[...,1] = 220; overlay[...,0] = np.where(protect>0, 255, 0)
            overlay[...,1][protect>0] = 55
            overlay[...,3] = np.maximum(edits//3, protect//2)
            im = QImage(overlay.data, overlay.shape[1],overlay.shape[0],overlay.strides[0],QImage.Format_RGBA8888)
            p.drawImage(self.rect, im)
        p.end()

    def _source(self, pos):
        if not self.model or not self.rect.contains(pos): return None
        h,w = self.model.shape
        return ((pos.x()-self.rect.x())/self.rect.width()*w, (pos.y()-self.rect.y())/self.rect.height()*h)

    def mousePressEvent(self, e):
        if e.button()!=Qt.LeftButton or not self.paint_enabled or not self.model: return
        point=self._source(e.position())
        if point is None: return
        try: self.model.checkpoint(self.layer)
        except ValueError as ex: self.error.emit(str(ex)); return
        self.dragging=True; self.last=point; self._stroke(point)

    def _stroke(self, point):
        start=self.last or point
        distance=np.hypot(point[0]-start[0],point[1]-start[1])
        for f in np.linspace(0,1,max(2,int(distance/max(1,self.radius/2))+1)):
            self.model.brush(self.layer,start[0]+f*(point[0]-start[0]),start[1]+f*(point[1]-start[1]),self.radius,self.erase)
        self.last=point; self.update(); self.changed.emit()

    def mouseMoveEvent(self,e):
        if self.dragging:
            point=self._source(e.position())
            if point is not None: self._stroke(point)

    def mouseReleaseEvent(self,e): self.dragging=False; self.last=None


class MetalPanel(QWidget):
    def __init__(self, owner):
        super().__init__(); self.owner=owner; self.jobs=[]; self.overrides={}; self.defaults={}
        lay=QVBoxLayout(self); lay.setSpacing(12)
        title=QLabel('FO76 METAL RECONSTRUCTION'); title.setStyleSheet('color:#ff481e;font-weight:700;font-size:18px;')
        lay.addWidget(title)
        self.mode=QComboBox(); self.mode.addItems(['关闭 / Off','自动保守 / Conservative','手动蒙版 / Manual masks'])
        self.mode.setToolTip(t('关闭时保留普通转换。实验预设需在材质检查窗口中逐项确认。',
            'Off preserves ordinary conversion. Experimental profiles require explicit per-job confirmation.'))
        lay.addWidget(self.mode)
        self.note=QLabel(); self.note.setWordWrap(True); lay.addWidget(self.note)
        self.open_btn=QPushButton(); self.open_btn.clicked.connect(self.open_dialog); lay.addWidget(self.open_btn)
        self.count=QLabel(); self.count.setWordWrap(True); lay.addWidget(self.count)
        lay.addStretch(); self.retranslate()

    def retranslate(self):
        self.note.setText(t('实验预设 · 尚未经过 FO4 游戏内验收\n仅在受支持的区域重建颜色、反射与光滑度。保护蒙版优先；不会添加细节。',
            'EXPERIMENTAL · FO4 in-game validation pending\nRebuilds supported local color, specular and smoothness only. Protection masks always win; no invented details.'))
        self.open_btn.setText(t('检查材质 · 对照预览 · 编辑蒙版','Inspect materials · Compare · Edit masks'))
        self.open_btn.setToolTip(t('后台分析 BGSM 贴图引用，检查候选，编辑或导入独立蒙版，再为所选任务保存设置。',
            'Analyze BGSM texture references in the background, inspect candidates, edit/import masks, and save selected-job settings.'))
        self.count.setText(t(f'已保存 {len(self.overrides)} 个任务的独立设置。', f'{len(self.overrides)} job-specific settings saved.'))

    def load(self,cfg):
        self.mode.setCurrentIndex({'off':0,'auto':1,'manual':2}.get(getattr(cfg,'metal_mode','off'),0))
        self.defaults=copy.deepcopy(getattr(cfg,'metal_options',{})); self.overrides=copy.deepcopy(getattr(cfg,'job_overrides',{})); self.retranslate()

    def collect(self,cfg):
        cfg.metal_mode=['off','auto','manual'][self.mode.currentIndex()]
        cfg.metal_options=copy.deepcopy(self.defaults); cfg.job_overrides=copy.deepcopy(self.overrides)

    def open_dialog(self):
        root=self.owner.in_edit.text().strip()
        if not os.path.isdir(root): QMessageBox.warning(self,'Metal',t('请先选择输入目录。','Select an input directory first.')); return
        dlg=MetalDialog(self,root); dlg.exec(); self.retranslate()


class MetalDialog(QDialog):
    def __init__(self,panel,root):
        super().__init__(panel); self.panel=panel; self.root=root; self.jobs=[]; self.current=None
        self._search_rows=[]; self.cancel_event=threading.Event()
        self.views={}; self.model=None; self.work=None; self.closed_pending=False; self.dirty=False; self.preview_key=None; self.reviews={}
        self.setWindowTitle(t('FO76 金属重建 · 材质检查','FO76 Metal Reconstruction · Material inspection'))
        self.resize(1220,850)
        outer=QVBoxLayout(self)
        self.status=QLabel(t('准备分析材质引用…','Preparing material reference analysis…')); self.status.setWordWrap(True); outer.addWidget(self.status)
        split=QSplitter(); outer.addWidget(split,1)
        left=QWidget(); ll=QVBoxLayout(left); ll.setContentsMargins(0,0,6,0)
        self.filter=QLineEdit(); self.filter.setPlaceholderText(t('筛选路径（最多显示 500 项）','Filter path (shows up to 500 matches)'))
        self.filter.setToolTip(t('只过滤列表，不修改源文件。','Filters the list without modifying sources.'))
        self._filter_timer=QTimer(self,interval=150,singleShot=True)
        self._filter_timer.timeout.connect(self.fill_list)
        self.filter.textChanged.connect(lambda:self._filter_timer.start()); ll.addWidget(self.filter)
        self.list=QListWidget(); self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.currentItemChanged.connect(self.select_job); self.list.setToolTip(t('Ctrl / Shift 多选；双击生成预览。','Ctrl / Shift selects multiple jobs; double-click to preview.'))
        self.list.itemDoubleClicked.connect(lambda _:self.preview()); ll.addWidget(self.list)
        self.count=QLabel(); ll.addWidget(self.count)
        split.addWidget(left)
        right=QWidget(); rr=QVBoxLayout(right); rr.setContentsMargins(6,0,0,0)
        top=QHBoxLayout(); self.view=QComboBox(); self.view.currentIndexChanged.connect(self.show_view); top.addWidget(self.view,1)
        self.preview_button=QPushButton(t('生成 / 刷新预览','Build / refresh preview')); self.preview_button.clicked.connect(self.preview)
        self.preview_button.setToolTip(t('使用当前设置重新计算；不会生成游戏材质。','Recomputes with current settings without writing deployable game materials.'))
        top.addWidget(self.preview_button); rr.addLayout(top)
        self.canvas=MaskCanvas(); self.canvas.error.connect(self.error); self.canvas.changed.connect(self.mask_changed); rr.addWidget(self.canvas,1)
        self.legend=QLabel(t('2D 对照；不能替代游戏内验收。','2D comparison; does not replace in-game validation.')); rr.addWidget(self.legend)
        controls=QHBoxLayout(); self.layer=QComboBox(); self.layer.addItems([t('编辑蒙版','Edit mask'),t('保护蒙版（优先）','Protection mask (priority)')]); self.layer.currentIndexChanged.connect(self.brush_controls)
        self.layer.setToolTip(t('保护区不允许被手动增加覆盖。','Manual additions cannot override protected pixels.'))
        controls.addWidget(self.layer); self.paint=QCheckBox(t('画笔','Brush')); self.paint.toggled.connect(self.brush_controls); controls.addWidget(self.paint)
        self.erase=QCheckBox(t('擦除','Erase')); self.erase.toggled.connect(self.brush_controls); controls.addWidget(self.erase)
        self.radius=QSpinBox(); self.radius.setRange(1,512); self.radius.setValue(12); self.radius.setSuffix(' px'); self.radius.valueChanged.connect(self.brush_controls)
        self.radius.setToolTip(t('源贴图像素半径，不随窗口缩放。','Radius in source pixels, independent of preview scale.')); controls.addWidget(self.radius)
        rr.addLayout(controls)
        actions=QHBoxLayout()
        for zh,en,fn in [('导入','Import',self.import_mask),('导出','Export',self.export_mask),('反选','Invert',lambda:self.mask_action('invert')),('清空','Clear',lambda:self.mask_action('clear')),('撤销','Undo',self.undo),('恢复候选','Restore',lambda:self.mask_action('restore'))]:
            b=QPushButton(t(zh,en)); b.clicked.connect(fn); b.setToolTip(t(zh+'当前层；操作保留源分辨率。',en+' current layer at original resolution.')); actions.addWidget(b)
        rr.addLayout(actions); split.addWidget(right); split.setSizes([320,860])
        settings=QGroupBox(t('局部重建设置','Local reconstruction settings')); form=QHBoxLayout(settings); outer.addWidget(settings)
        self.preset=QComboBox(); self.preset.addItem('Neutral steel · experimental','neutral_steel'); self.preset.setToolTip(t('尚未经过 FO4 游戏验收，不是通用银色预设。','Not game-validated; not a universal silver preset.'))
        form.addWidget(self.preset)
        self.spins={}
        for key,zh,en,default in [('reconstruction_strength','颜色','Color',.65),('tint_retention','保色','Tint',.25),('specular_strength','反射','Specular',.35),('smoothness_strength','光滑度','Smoothness',.25)]:
            col=QVBoxLayout(); col.addWidget(QLabel(t(zh,en))); s=QDoubleSpinBox(); s.setRange(0,1); s.setSingleStep(.05); s.setValue(default); s.setMaximumWidth(95)
            s.setToolTip(t('仅在最终蒙版内混合；零表示保持普通转换结果。','Blends only inside the final mask; zero keeps the ordinary baseline.'))
            col.addWidget(s); form.addLayout(col); self.spins[key]=s
        self.boundary=QComboBox(); self.boundary.addItem(t('不扩边','No expansion'),'none'); self.boundary.addItem(t('向内收缩 1 px','Erode inward 1 px'),'erode'); self.boundary.addItem(t('向内羽化 2 px','Feather inward 2 px'),'feather')
        self.boundary.setToolTip(t('边界仅向内处理，不扩大编辑区。','Boundary treatment never expands the edit region.')); form.addWidget(self.boundary)
        advanced=QHBoxLayout(); outer.addLayout(advanced)
        self.transfer=QComboBox(); self.transfer.addItem(t('颜色合同：自动检查','Color contract: inspect'),'auto'); self.transfer.addItem('D sRGB / R linear (manual override)','srgb_linear')
        self.transfer.addItem('D linear / R linear (manual override)','linear_linear'); self.transfer.setToolTip(t('明确覆盖将写入报告；不确定时保持自动检查。','An explicit override is recorded in the report. Keep inspection when uncertain.')); advanced.addWidget(self.transfer)
        self.approve=QCheckBox(t('我已检查，允许所选任务使用实验重建','I reviewed these jobs; allow experimental reconstruction'))
        self.approve.setToolTip(t('仅确认当前/选中任务，不会把预设标记为已通过游戏验收。','Applies only to saved selected jobs, never marks the profile as game-approved.')); advanced.addWidget(self.approve)
        bottom=QHBoxLayout(); outer.addLayout(bottom)
        for zh,en,fn in [('应用到当前任务','Apply to current',lambda:self.apply(False)),('应用到选中任务','Apply to selection',lambda:self.apply(True)),('恢复设置默认值','Reset settings',self.reset)]:
            b=QPushButton(t(zh,en)); b.clicked.connect(fn); b.setToolTip(t('保存独立任务设置；正式结果由主窗口的开始转换生成。','Saves job settings; use Start conversion in the main window for formal output.')); bottom.addWidget(b)
        bottom.addStretch(); close=QPushButton(t('完成','Done')); close.clicked.connect(self.accept); bottom.addWidget(close)
        self._busy_controls=[self.filter,self.list,self.canvas,self.preview_button,settings,self.transfer,self.approve]
        self._busy_controls.extend(right.findChildren(QPushButton))
        self._busy_controls.extend(right.findChildren(QCheckBox))
        self._busy_controls.extend(right.findChildren(QSpinBox))
        self._busy_controls.extend([self.layer]+[bottom.itemAt(i).widget() for i in range(3)])
        self._start(lambda:self._scan(),self.scanned)

    def _scan(self):
        from texture_api import scan_jobs
        return scan_jobs(self.root,direction='76-to-4',include_textures=True,cancel_event=self.cancel_event)

    def _start(self,fn,done):
        if self.work and self.work.isRunning(): return False
        self._filter_timer.stop()
        for widget in self._busy_controls:widget.setEnabled(False)
        self.work=Work(fn,self); self.work.done.connect(done); self.work.failed.connect(self.error); self.work.finished.connect(self._work_finished); self.work.start(); return True

    def _work_finished(self):
        previous=self.work
        self.work=None
        if previous is not None: previous.deleteLater()
        for widget in self._busy_controls:widget.setEnabled(True)
        if self.closed_pending: super().reject()

    def scanned(self,jobs):
        self.jobs=list(jobs); self._search_rows=[(job_name(j).casefold(),j) for j in self.jobs]; self.panel.jobs=self.jobs; self.fill_list(); self.status.setText(t('按引用组织任务。双击材质生成预览。','Jobs organized by references. Double-click a material to preview.'))

    def fill_list(self):
        selected=job_id(self.current) if self.current else None
        text=self.filter.text().casefold(); matches=0
        # Rebuilding filtered rows must not clear the current preview or painted masks.
        self.list.blockSignals(True); self.list.setUpdatesEnabled(False)
        try:
            self.list.clear()
            for name,job in self._search_rows:
                if text not in name:continue
                matches+=1
                if matches>500:continue
                item=QListWidgetItem(job_name(job));item.setData(Qt.UserRole,job)
                item.setToolTip(job_name(job));self.list.addItem(item)
                if job_id(job)==selected:self.list.setCurrentItem(item)
        finally:
            self.list.blockSignals(False);self.list.setUpdatesEnabled(True)
        self.count.setText(t(f'{matches} 个匹配 / {len(self.jobs)} 个任务',f'{matches} matches / {len(self.jobs)} jobs'))

    def _discard_edits(self):
        if not self.dirty:return True
        answer=QMessageBox.question(self,t('未应用的蒙版','Unapplied mask edits'),
            t('当前蒙版尚未应用。放弃这些修改？','The current mask has not been applied. Discard these edits?'),
            QMessageBox.Discard | QMessageBox.Cancel,QMessageBox.Cancel)
        return answer==QMessageBox.Discard

    def select_job(self,item,previous=None):
        next_job=item.data(Qt.UserRole) if item else None
        if self.current is not None and next_job is not None and job_id(self.current)==job_id(next_job):return
        if not self._discard_edits():
            self.list.blockSignals(True);self.list.setCurrentItem(previous);self.list.blockSignals(False);return
        self.current=next_job; self.model=None; self.canvas.model=None; self.views={}; self.canvas.image=None; self.canvas.update(); self.preview_key=None
        options=self.panel.overrides.get(job_id(self.current),self.panel.defaults) if self.current else {}
        self.load_options(options); self.dirty=False

    def load_options(self,o):
        for key,s in self.spins.items(): s.setValue(o.get(key,{'reconstruction_strength':.65,'tint_retention':.25,'specular_strength':.35,'smoothness_strength':.25}[key]))
        self.approve.setChecked(bool(o.get('allow_experimental',False))); self.transfer.setCurrentIndex(max(0,self.transfer.findData(o.get('color_contract_override','auto'))))
        self.boundary.setCurrentIndex(max(0,self.boundary.findData(o.get('boundary','none'))))

    def options(self):
        result={key:s.value() for key,s in self.spins.items()}
        result.update(preset=self.preset.currentData(),mode=['off','auto','manual'][self.panel.mode.currentIndex()],allow_experimental=self.approve.isChecked(),boundary=self.boundary.currentData(),color_contract_override=self.transfer.currentData())
        if self.current:
            saved=self.panel.overrides.get(job_id(self.current),{})
            for k in ('edit_mask_path','protect_mask_path'):
                if saved.get(k): result[k]=saved[k]
        return result

    def preview(self):
        if not self.current or (self.work and self.work.isRunning()): return
        job=copy.deepcopy(self.current); opts=self.options()
        if self.model:
            opts['manual_mask']=self.model.layers['edit'].copy()/255.; opts['protect_mask']=self.model.layers['protect'].copy()/255.
        self.status.setText(t('后台计算预览；输入文件保持只读…','Computing preview in background; inputs remain read-only…'))
        def work():
            from texture_api import preview_job
            result=preview_job(job,opts)
            result['thumbnails']={k:thumbnail(v) for k,v in result.get('views',{}).items()}
            result.pop('views',None)
            result['_preview_job_id']=job_id(job)
            return result
        self._start(work,self.preview_ready)

    def preview_ready(self,result):
        if result.get('_preview_job_id',job_id(self.current))!=job_id(self.current):return
        self.views=result.get('thumbnails',{}); shape=result.get('source_shape')
        if shape and (self.model is None or self.model.shape!=tuple(shape[:2])):
            self.model=MaskModel(shape,result.get('candidate'))
            for layer,key in [('edit','edit_mask_path'),('protect','protect_mask_path')]:
                path=self.options().get(key)
                if path:
                    try:self.model.load(layer,path)
                    except (OSError,ValueError) as ex:self.error(str(ex))
            self.canvas.model=self.model
        labels={'source_d':('源 _d · 颜色显示','Source _d · Color display'),'baseline_d':('普通结果 · 颜色显示','Ordinary baseline · Color display'),'reconstructed_d':('重建结果 · 颜色显示','Reconstruction · Color display'),'candidate':('候选蒙版 · 数值通道','Candidate mask · Numeric channel'),'protect':('保护蒙版 · 数值通道','Protection mask · Numeric channel'),'final_mask':('最终蒙版 · 数值通道','Final mask · Numeric channel'),'specular':('_s.R 反射 · 数值通道','_s.R Specular · Numeric channel'),'smoothness':('_s.G 光滑度 · 数值通道','_s.G Smoothness · Numeric channel'),'difference':('差异 · 数值显示','Difference · Numeric display'),'alpha':('实际 Alpha · 数值通道','Actual alpha · Numeric channel')}
        self.view.clear()
        for key in self.views: self.view.addItem(t(*labels.get(key,(key,key))),key)
        self.preview_key=job_id(self.current)
        self.reviews[self.preview_key]=copy.deepcopy(result.get('metadata',{}).get('hashes',{}))
        self.show_view(); self.brush_controls()
        diag=result.get('diagnostics',[]); self.status.setText('\n'.join(str(x) for x in diag) or t('分析完成；请检查候选和保护区。','Analysis complete; inspect candidate and protection masks.'))
        # Recomputing a preview does not persist edited masks. Keep dirty until Apply.

    def show_view(self,*_):
        self.canvas.image=self.views.get(self.view.currentData()); self.canvas.update()
        self.legend.setText(t('2D 对照 · ','2D comparison · ')+self.view.currentText()+t(' · 游戏内效果待验证',' · In-game validation pending'))

    def brush_controls(self,*_):
        self.canvas.layer='protect' if self.layer.currentIndex() else 'edit'; self.canvas.radius=self.radius.value(); self.canvas.erase=self.erase.isChecked(); self.canvas.paint_enabled=self.paint.isChecked(); self.canvas.update()

    def mask_changed(self): self.dirty=True
    def mask_action(self,action):
        if self.model:
            try:self.model.action(self.canvas.layer,action); self.mask_changed(); self.canvas.update()
            except ValueError as ex:self.error(str(ex))
    def undo(self):
        if self.model:self.model.undo(); self.mask_changed(); self.canvas.update()
    def import_mask(self):
        if not self.model:return
        path,_=QFileDialog.getOpenFileName(self,t('导入原尺寸灰度蒙版','Import exact-size grayscale mask'),'','PNG (*.png)')
        if path:
            try:self.model.load(self.canvas.layer,path); self.mask_changed(); self.canvas.update()
            except (ValueError,OSError) as ex:self.error(str(ex))
    def export_mask(self):
        if not self.model:return
        path,_=QFileDialog.getSaveFileName(self,t('导出蒙版','Export mask'),'mask.png','PNG (*.png)')
        if path:
            try:self.model.save(self.canvas.layer,path)
            except OSError as ex:self.error(str(ex))

    def apply(self,selection):
        jobs=[i.data(Qt.UserRole) for i in self.list.selectedItems()] if selection else ([self.current] if self.current else [])
        if not jobs:return
        if self.model and (self.dirty or self.panel.mode.currentIndex()==2) and len(jobs)>1:
            QMessageBox.warning(self,'Metal',t('手工蒙版只应用到当前材质。不同材质必须分别核对尺寸和保护区。','Manual masks apply only to the current material; review dimensions and protection separately.')); return
        opts=self.options()
        if opts.get('allow_experimental') and any(not self.reviews.get(job_id(j)) for j in jobs):
            self.error(t('请先逐项生成对照预览，或取消实验确认后仅保存参数。', 'Preview each selected job first, or uncheck experimental confirmation to save parameters only.')); return
        if len(jobs)>1:
            opts.pop('edit_mask_path',None); opts.pop('protect_mask_path',None)
        if self.model and len(jobs)==1 and (self.dirty or self.panel.mode.currentIndex()==2):
            if job_id(jobs[0])!=self.preview_key:self.error(t('请先生成该任务的预览。','Build a preview of this job first.')); return
            folder=Path(os.environ.get('LOCALAPPDATA',str(Path.home()))) / 'private0516' / 'TextureConverter' / 'masks'
            try:
                folder.mkdir(parents=True,exist_ok=True)
                for layer,key in [('edit','edit_mask_path'),('protect','protect_mask_path')]:
                    data=self.model.layers[layer]
                    digest=hashlib.sha256(str(data.shape).encode()+data.tobytes()).hexdigest()
                    path=folder/(digest+'.png')
                    if not path.exists():self.model.save(layer,path)
                    opts[key]=str(path)
            except OSError as ex:self.error(str(ex));return
        for job in jobs:
            per_job=copy.deepcopy(opts)
            if per_job.get('allow_experimental'):per_job['reviewed_source_hashes']=copy.deepcopy(self.reviews[job_id(job)])
            self.panel.overrides[job_id(job)]=per_job
        self.dirty=False; self.status.setText(t(f'已保存 {len(jobs)} 个任务的设置；回到主窗口开始转换。',f'Saved settings for {len(jobs)} jobs; start conversion in the main window.'))

    def reset(self):
        self.load_options({})
        if self.current:self.panel.overrides.pop(job_id(self.current),None)
    def error(self,message):
        self.status.setText(str(message).splitlines()[-1]); QMessageBox.warning(self,'Metal',str(message))
    def done(self,result):
        if not self._discard_edits():return
        self.dirty=False
        if self.work and self.work.isRunning():
            self.cancel_event.set()
            self.closed_pending=True; self.status.setText(t('等待当前预览完成后关闭…','Closing after the current preview completes…')); return
        super().done(result)
