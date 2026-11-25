# -*- coding: utf-8 -*-
"""Offline converter UI: compact workflow, bounded updates, single-window motion."""

from __future__ import annotations

import os
import sys
import traceback
import copy
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QPushButton,
                               QTabWidget, QToolBox, QTextEdit, QVBoxLayout, QWidget)

import config as cfgmod
import i18n
import ui_theme as T
from config import APP_AUTHOR, APP_TITLE, APP_VERSION, MODES, Config
from metal_ui import MetalPanel, t
from converter import Converter
from i18n import tr
from logger import ConversionLog
from material_jobs import scan_jobs
from ui_events import UiEventBuffer
from app_motion import MotionController, ContentReveal
from ui_widgets import (BannerWidget, LiveLogWidget,
                        PillSelector, ProcessPanel,
                        StatusBarWidget, SweepButton)


# ---------------------------------------------------------------------------
# 后台线程
# ---------------------------------------------------------------------------

class ScanWorker(QThread):
    done=Signal(object)
    failed=Signal(str)
    def __init__(self,root,include_textures=True):
        super().__init__();self.root=root;self.include_textures=include_textures
        self.cancel_event=threading.Event();self.events=UiEventBuffer()
    def run(self):
        try:
            jobs=scan_jobs(self.root,include_textures=self.include_textures,cancel_event=self.cancel_event,
                           progress_cb=self.events.progress,inspect_texture_files=self.include_textures)
            if not self.cancel_event.is_set():self.done.emit(jobs)
        except Exception:
            if not self.cancel_event.is_set():self.failed.emit(traceback.format_exc())


class ConvertWorker(QThread):
    done=Signal(object)
    failed=Signal(str)
    def __init__(self,cfg,jobs=None,scan_context=None):
        super().__init__();self.cfg=copy.deepcopy(cfg);self.events=UiEventBuffer()
        self.converter=Converter(self.cfg,jobs=jobs,scan_context=scan_context)
        self.converter.set_progress_cb(self.events.progress)
        self.converter.log.on_entry=self.events.log
    @property
    def warning_count(self):return self.events.warnings
    @property
    def error_count(self):return self.events.errors
    def run(self):
        try:self.done.emit(self.converter.run())
        except Exception:self.failed.emit(traceback.format_exc())


# ---------------------------------------------------------------------------
# 文本结果对话框
# ---------------------------------------------------------------------------

class TextDialog(QDialog):
    def __init__(self, title: str, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(680, 540)
        root=QVBoxLayout(self);root.setContentsMargins(0,0,0,0)
        self.content=QWidget(self);root.addWidget(self.content)
        lay = QVBoxLayout(self.content)
        head = QLabel(title)
        head.setStyleSheet(
            f"font-family:{T.FONT_DISPLAY}; font-size:16px; padding:6px 2px;")
        lay.addWidget(head)
        view = QTextEdit(readOnly=True)
        view.setPlainText(text)
        lay.addWidget(view)
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        lay.addWidget(btns)
        self.reveal=ContentReveal(self,kind='dialog')
        self._finishing_done=False;self._closing_result=None

    def showEvent(self,event):
        super().showEvent(event)
        self._finishing_done=False;self._closing_result=None
        self.reveal.reveal(self.content)

    def done(self,result):
        if self._finishing_done or not self.isVisible():
            super().done(result);return
        if self._closing_result is not None:return
        self._closing_result=result
        self.reveal.collapse(self.content,lambda:self._complete_done(result))

    def _complete_done(self,result):
        self._finishing_done=True
        super().done(result)


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, icon_pm=None, icon_key=None):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        available = QApplication.primaryScreen().availableGeometry()
        self.setFixedSize(min(1060, available.width()-32), min(940, available.height()-40))
        # 固定逻辑尺寸，禁用最大化，最大化按钮置灰、双击标题栏不放大
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, False)
        self.setAcceptDrops(True)

        self.cfg = cfgmod.load_config()
        i18n.set_lang(self.cfg.lang)
        self.setStyleSheet(T.app_stylesheet(i18n.LANG))
        self.worker: ConvertWorker | None = None
        self.scanner: ScanWorker | None = None
        self._active_task = None
        self.icon_pm = icon_pm
        self.icon_key = icon_key

        self._prepared_jobs=None;self._scan_context=None
        self._preview=None;self._last_output=None;self._last_material_source=None
        self._last_report_dir = None
        self._preview_refresh_root = None
        self._task_started=None;self._last_done=0;self._last_total=0
        self._show_first_run_about=False
        self._build_ui()
        self._load_into_ui()
        self.in_edit.textChanged.connect(self._invalidate_scan)
        self.materials_input.textChanged.connect(self._invalidate_scan)
        self.mode_pills.changed.connect(self._apply_quality)
        self.cb_report.toggled.connect(self._save_report_preference)
        self._event_timer=QTimer(self,interval=100,timeout=self._drain_worker_events)
        self.motion=MotionController(self,self.centralWidget(),title='TEXTURE CONVERTER',author=APP_AUTHOR)
        self.motion.startup_finished.connect(self._startup_complete)
        self.motion.shutdown_failed.connect(self._shutdown_failed)
        self.navigation_motion=ContentReveal(self,enabled=lambda: not self.motion.reduced_motion and self.motion.state in ('new','ready'))
        self.tabs.currentChanged.connect(self._reveal_tab)
        self.advanced.currentChanged.connect(self._reveal_advanced)
        self._close_pending = False

        # 图标热重载：用户替换 EXE 同目录的 icon.* 或用 ResourceHacker
        # 改了 EXE 内嵌图标后，空闲时约 10 秒内程序内 LOGO 同步更新
        self._icon_timer = QTimer(self, interval=10000,
                                  timeout=self._check_icon_reload)
        self._icon_timer.start()

    def _check_icon_reload(self):
        if not self.isVisible() or self.isMinimized() or (self.worker and self.worker.isRunning()):return
        pm, key = cfgmod.load_app_icon()
        if not key or key == self.icon_key or pm is None:
            return
        self.icon_pm = pm
        self.icon_key = key
        qi = QIcon(pm)
        self.setWindowIcon(qi)
        app = QApplication.instance()
        if app:
            app.setWindowIcon(qi)
        self.banner.reload_icon_feedback(pm)

    # ================= UI 构建 =================

    def _dir_row(self, edit: QLineEdit, title_key: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        edit.setFixedHeight(40)
        row.addWidget(edit, 1)
        btn = QPushButton(tr("btn_browse"))
        btn.setObjectName("ghost")
        btn.setFixedSize(96, 40)
        btn.setToolTip(tr("tip_browse"))
        btn.clicked.connect(lambda: self._pick_dir(edit, title_key))
        self._browse_btns.append(btn)
        row.addWidget(btn)
        return row

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        # 统一外边距：米白底色均匀包裹所有模块
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # 顶部工具标识
        self.banner = BannerWidget(self.icon_pm, APP_AUTHOR)
        root.addWidget(self.banner)

        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(4, 2, 4, 2)
        bl.setSpacing(12)
        root.addWidget(body, 4)

        # ---------- 页签（短快照过渡，不对整棵控件树应用透明特效） ----------
        tabs = QTabWidget()
        self.tabs = tabs
        bl.addWidget(tabs, 1)

        # ==== 基础 ====
        basic = QWidget()
        b = QVBoxLayout(basic)
        b.setSpacing(12)

        self.dir_box = QGroupBox()
        self.dir_box.setMinimumHeight(148)   # QSS padding 不计入 sizeHint，显式保底防裁切
        form = QFormLayout(self.dir_box)
        form.setSpacing(12)
        self.in_edit = QLineEdit()
        self.out_edit = QLineEdit()
        self.lbl_in = QLabel()
        self.lbl_out = QLabel()
        self._browse_btns: list[QPushButton] = []
        form.addRow(self.lbl_in, self._dir_row(self.in_edit, "pick_in"))
        form.addRow(self.lbl_out, self._dir_row(self.out_edit, "pick_out"))
        self.in_edit.setToolTip(tr("tip_in"))
        self.out_edit.setToolTip(tr("tip_out"))
        b.addWidget(self.dir_box)

        self.mode_box = QGroupBox()
        self.mode_box.setMinimumHeight(100)
        ml = QHBoxLayout(self.mode_box)
        self.mode_pills = PillSelector(
            [(k, MODES[k]["label"]) for k in ("balanced", "space")])
        ml.addWidget(self.mode_pills)
        ml.addStretch(1)
        b.addWidget(self.mode_box)
        self.scan_summary=QLabel(t("选择输入与输出，直接开始即可。", "Choose input and output, then start."))
        self.scan_summary.setWordWrap(True);self.scan_summary.setObjectName("hint")
        b.addWidget(self.scan_summary)
        self.run_metrics=QLabel();self.run_metrics.setObjectName("hint")
        self.run_metrics.setWordWrap(True);self.run_metrics.hide();b.addWidget(self.run_metrics)
        self.preview_card=QGroupBox();preview_row=QHBoxLayout(self.preview_card)
        self.preview_hint=QLabel();self.preview_hint.setWordWrap(True)
        preview_row.addWidget(self.preview_hint,1)
        self.btn_preview=QPushButton();self.btn_preview.clicked.connect(self.open_model_preview)
        preview_row.addWidget(self.btn_preview);b.addWidget(self.preview_card)
        b.addStretch(1)
        tabs.addTab(basic, "")

        # ==== 选项（独立页） ====
        opt_tab = QWidget()
        ov = QVBoxLayout(opt_tab)
        ov.setSpacing(12)
        self.quick_box = QGroupBox()
        self.quick_box.setMinimumHeight(150)
        from PySide6.QtWidgets import QGridLayout
        ql = QGridLayout(self.quick_box)
        ql.setHorizontalSpacing(24)
        self.cb_keep = QCheckBox()
        self.cb_mip = QCheckBox()
        self.cb_gens = QCheckBox()
        self.cb_metal = QCheckBox()
        self.cb_glow_l = QCheckBox()
        for i, cb in enumerate((self.cb_keep, self.cb_mip, self.cb_gens,
                                self.cb_metal, self.cb_glow_l)):
            ql.addWidget(cb, i % 3, i // 3)
        ov.addWidget(self.quick_box)
        ov.addStretch(1)
        tabs.addTab(opt_tab, "")

        # ==== 贴图 ====
        tex_tab = QWidget()
        tl = QFormLayout(tex_tab)
        tl.setSpacing(10)
        fmts_color = ["BC7_UNORM", "BC3_UNORM", "BC1_UNORM"]
        fmts_cga = ["BC5_UNORM", "BC3_UNORM", "BC1_UNORM"]
        self.cmb_diffuse = QComboBox(); self.cmb_diffuse.addItems(fmts_color)
        self.cmb_normal = QComboBox(); self.cmb_normal.addItems(["BC5_UNORM"])
        self.cmb_spec = QComboBox(); self.cmb_spec.addItems(fmts_cga)
        self.cmb_glow = QComboBox(); self.cmb_glow.addItems(fmts_color)
        self.cmb_diffuse.setToolTip(tr("tip_fmt_d"))
        self.cmb_normal.setToolTip(tr("tip_fmt_n"))
        self.cmb_spec.setToolTip(tr("tip_fmt_s"))
        self.cmb_glow.setToolTip(tr("tip_fmt_g"))
        self.lbl_diffuse = QLabel()
        self.lbl_normal = QLabel()
        self.lbl_spec = QLabel()
        self.lbl_glow = QLabel()
        tl.addRow(self.lbl_diffuse, self.cmb_diffuse)
        tl.addRow(self.lbl_normal, self.cmb_normal)
        tl.addRow(self.lbl_spec, self.cmb_spec)
        tl.addRow(self.lbl_glow, self.cmb_glow)
        self.note_tex = QLabel()
        self.note_tex.setObjectName("hint"); self.note_tex.setWordWrap(True)
        tl.addRow(self.note_tex)
        tabs.addTab(tex_tab, "")

        # ==== 材质 ====
        mat_tab = QWidget()
        matf = QFormLayout(mat_tab)
        matf.setSpacing(10)
        self.cb_invert = QCheckBox()
        self.cb_ao = QCheckBox()
        self.sp_ao = QDoubleSpinBox(minimum=0.0, maximum=1.0,
                                    singleStep=0.05, value=0.3)
        self.sp_metal = QDoubleSpinBox(minimum=0.0, maximum=1.0,
                                       singleStep=0.05, value=0.5)
        self.lbl_mat_normal = QLabel()
        self.lbl_mat_ao = QLabel()
        self.lbl_ao = QLabel()
        self.lbl_metal = QLabel()
        matf.addRow(self.lbl_mat_normal, self.cb_invert)
        matf.addRow(self.lbl_mat_ao, self.cb_ao)
        matf.addRow(self.lbl_ao, self.sp_ao)
        matf.addRow(self.lbl_metal, self.sp_metal)
        self.note_mat = QLabel()
        self.note_mat.setObjectName("hint"); self.note_mat.setWordWrap(True)
        matf.addRow(self.note_mat)
        tabs.addTab(mat_tab, "")

        # ==== 输出 ====
        out_tab = QWidget()
        ol = QVBoxLayout(out_tab)
        ol.setSpacing(10)
        self.cb_overwrite = QCheckBox()
        self.cb_log = QCheckBox()
        self.cb_report = QCheckBox()
        self.cb_bgsm = QCheckBox()
        for cb in (self.cb_overwrite, self.cb_log, self.cb_bgsm):
            ol.addWidget(cb)
        ol.addStretch(1)
        tabs.addTab(out_tab, "")

        self.metal_panel=MetalPanel(self)
        # Keep existing controls, move specialist settings behind one advanced tab.
        self.advanced=QToolBox()
        for page in (opt_tab,tex_tab,mat_tab,out_tab):
            tabs.removeTab(tabs.indexOf(page))
            self.advanced.addItem(page,"")
        self.preview_host=QWidget();self.preview_layout=QVBoxLayout(self.preview_host)
        self.preview_layout.setContentsMargins(0,0,0,0)
        materials_page = QWidget()
        materials_layout = QVBoxLayout(materials_page)
        self.materials_group = QGroupBox()
        materials_form = QFormLayout(self.materials_group)
        materials_form.setSpacing(12)
        self.materials_input = QLineEdit()
        self.materials_output = QLineEdit()
        self.materials_input_label, self.materials_output_label = QLabel(), QLabel()
        source_row = self._dir_row(self.materials_input, "pick_in")
        self.materials_pick_folder = self._browse_btns.pop()
        self.materials_pick_file = QPushButton()
        self.materials_pick_file.setObjectName("ghost")
        self.materials_pick_file.setFixedSize(96, 40)
        self.materials_pick_file.clicked.connect(self._pick_material_file)
        source_row.insertWidget(1, self.materials_pick_file)
        materials_form.addRow(self.materials_input_label, source_row)
        materials_form.addRow(self.materials_output_label, self._dir_row(self.materials_output, "pick_out"))
        self.materials_output.textChanged.connect(self.out_edit.setText)
        self.out_edit.textChanged.connect(self.materials_output.setText)
        materials_layout.addWidget(self.materials_group)
        self.materials_hint = QLabel()
        self.materials_hint.setObjectName("hint")
        self.materials_hint.setWordWrap(True)
        materials_layout.addWidget(self.materials_hint)
        self.materials_summary = QLabel()
        self._material_summary_text = ("", "")
        self.materials_summary.setWordWrap(True)
        materials_layout.addWidget(self.materials_summary)
        materials_layout.addStretch()
        self.materials_tab_index = tabs.addTab(materials_page, "")
        self.preview_tab_index = tabs.addTab(self.preview_host, "")
        self.metal_tab_index = tabs.addTab(self.metal_panel, "Metal")
        self.advanced_tab_index = tabs.addTab(self.advanced, "")
        for redundant in (self.cb_keep,self.cb_overwrite,self.cb_log):redundant.hide()
        notice=QLabel()
        notice.setWordWrap(True);notice.setObjectName("hint");ol.insertWidget(0,notice)
        self.output_notice=notice

        # ---------- 语言切换胶囊（页签栏右上角） ----------
        self.lang_pills = PillSelector(
            [("zh", tr("lang_zh")), ("en", tr("lang_en"))])
        self.lang_pills.setToolTip(tr("tip_lang"))
        self.lang_pills.set_key(i18n.LANG, animate=False)
        self.lang_pills.changed.connect(self._on_lang)
        tabs.setCornerWidget(self.lang_pills, Qt.TopRightCorner)

        # ---------- 操作按钮（挂载进底部状态栏右侧条纹区） ----------
        self.btn_analyze = SweepButton(tr("btn_dry"))
        self.btn_analyze.setFixedSize(158, 36)
        self.btn_analyze.setToolTip(tr("tip_dry"))
        self.btn_analyze.clicked.connect(self.on_dry_run)
        self.btn_start = SweepButton(tr("btn_start"), primary=True, arrow=True)
        self.btn_start.setFixedSize(186, 36)
        self.btn_start.setToolTip(tr("tip_start"))
        self.btn_start.clicked.connect(self.on_start)

        # ---------- 实时日志控制台 ----------
        self.logview = LiveLogWidget()
        self.logview.setMinimumHeight(110)
        root.addWidget(self.logview, 1)

        # ---------- 详尽进程状态面板（日志栏下方） ----------
        self.proc_panel = ProcessPanel()
        self.proc_panel.setFixedHeight(72)
        root.addWidget(self.proc_panel)

        # ---------- 底部可视化状态栏（含操作按钮） ----------
        self.statusbar = StatusBarWidget()
        self.statusbar.attach_buttons(self.btn_analyze, self.btn_start)
        root.addWidget(self.statusbar)
        from PySide6.QtWidgets import QScrollArea
        # Preserve the original pages while making them fit a fixed-size window.
        for index in range(self.tabs.count()):
            page=self.tabs.widget(index); title=self.tabs.tabText(index)
            self.tabs.removeTab(index)
            scroll=QScrollArea(); scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.NoFrame); scroll.setWidget(page)
            self.tabs.insertTab(index,scroll,title)
        actions=QHBoxLayout()
        self.release_label=QLabel(f"{APP_VERSION}  /  {APP_AUTHOR}")
        self.release_label.setStyleSheet("color:#827e74;font-size:11px;")
        actions.addWidget(self.release_label)
        actions.addWidget(self.cb_report)
        actions.addStretch()
        self.btn_output=QPushButton(t("打开结果","Open results"));self.btn_output.setEnabled(False)
        self.btn_output.clicked.connect(self.open_output);self.btn_output.setObjectName("ghost")
        actions.addWidget(self.btn_output)
        self.btn_log=QPushButton(t("查看日志","View log"));self.btn_log.setEnabled(False)
        self.btn_log.clicked.connect(self.on_view_log);self.btn_log.setObjectName("ghost")
        actions.addWidget(self.btn_log)
        self.btn_about=QPushButton(t("关于与许可", "About & licenses"))
        self.btn_about.setToolTip(t("作者、AI 标注及本地许可说明。", "Author, AI disclosure and local license notices."))
        self.btn_about.clicked.connect(self.show_about); actions.addWidget(self.btn_about)
        self.btn_cancel=QPushButton(t("取消任务", "Cancel task"))
        self.btn_cancel.setToolTip(t("取消当前任务并清理未提交结果，保留已完成文件。", "Cancels the current task, cleans uncommitted output, and retains completed files."))
        self.btn_cancel.setEnabled(False); self.btn_cancel.clicked.connect(self.cancel_work); actions.addWidget(self.btn_cancel)
        root.addLayout(actions)
        self.retranslate()
        self.tabs.setCurrentIndex(0)
        self.tabs.currentChanged.connect(self._tab_changed)

    # ================= 界面语言 =================

    def _on_lang(self, lang: str):
        if lang == i18n.LANG:
            return
        self.lang_pills.blockSignals(True)
        self.lang_pills.set_key(lang, animate=False)
        self.lang_pills.blockSignals(False)
        i18n.set_lang(lang)
        self.cfg.lang = lang
        cfgmod.save_config(self.cfg)
        self.setStyleSheet(T.app_stylesheet(lang))
        self.retranslate()

    def retranslate(self):
        """按当前语言刷新所有界面文案与提示气泡。"""
        self.setWindowTitle(f"Texture Converter · {APP_VERSION}")
        if hasattr(self,"metal_panel"):
            self.tabs.setTabText(self.metal_tab_index,t("金属重建","Metal")); self.metal_panel.retranslate()
        if hasattr(self,"btn_about"):
            self.btn_about.setText(t("关于与许可","About & licenses"))
            self.btn_cancel.setText(t("取消任务","Cancel task"))

        self.tabs.setTabText(0,t("转换","Convert"));self.tabs.setTabText(self.advanced_tab_index,t("高级设置","Advanced"))
        self.tabs.setTabText(self.preview_tab_index,t("模型预览","Model preview"))
        self.tabs.setTabText(self.materials_tab_index,t("材质转换","Materials"))
        self.materials_group.setTitle(t("转换 BGSM / BGEM","Convert BGSM / BGEM"))
        self.materials_input_label.setText(t("源材质","Material source"))
        self.materials_output_label.setText(tr("lbl_out"))
        material_input_tip = t("选择一个 .bgsm、.bgem 文件或要批量转换的文件夹。", "Select a .bgsm or .bgem file, or a folder to convert in bulk.")
        self.materials_input.setPlaceholderText(material_input_tip)
        self.materials_input.setToolTip(material_input_tip)
        self.materials_output.setPlaceholderText(tr("ph_out"))
        self.materials_output.setToolTip(tr("tip_out"))
        self.materials_pick_file.setText(t("选择文件","File…"))
        self.materials_pick_file.setToolTip(t("选择单个 BGSM 或 BGEM 材质。","Choose one BGSM or BGEM material."))
        self.materials_pick_folder.setText(t("选择目录","Folder…"))
        self.materials_pick_folder.setToolTip(t("递归查找目录中的 BGSM 和 BGEM 材质。","Find BGSM and BGEM materials in this folder and its subfolders."))
        self.materials_hint.setText(t("BGSM 和 BGEM 按原目录写入导出根目录下的 materials 文件夹。可与 Mesh Converter 使用同一根目录。", "BGSM and BGEM files keep their source paths under materials in the export root. You can use the same root in Mesh Converter."))
        self.materials_summary.setText(t(*self._material_summary_text))
        self.preview_card.setTitle(t("查看转换结果","Inspect your results"))
        self.preview_hint.setText(t("导入自己的 NIF，在模型上检查转换后的材质。", "Load your NIF to inspect converted materials on the model."))
        self.btn_preview.setText(t("打开模型预览 ↗","Open model preview ↗"))
        self.btn_preview.setToolTip(t("本地读取 NIF 和资源目录；可旋转、缩放并查看缺失引用。", "Read a local NIF and resource folders; orbit, zoom and inspect missing references."))
        if self._preview is not None:self._preview.retranslate()
        for idx,key in enumerate(("tab_options","tab_tex","tab_mat","tab_out")):
            self.advanced.setItemText(idx,tr(key))
        if hasattr(self,"btn_output"):
            self.btn_output.setText(t("打开结果","Open results"));self.btn_log.setText(t("查看日志","View log"))
            self.btn_output.setToolTip(t("打开最近一次任务的导出根目录。", "Open the export root of the last conversion."))
            self.btn_log.setToolTip(t("打开最近一次转换保存的日志。", "Open the log saved for the last conversion."))
        self.output_notice.setText(t("材质和贴图按原路径写入 materials 和 textures。同路径同内容的文件可复用，内容冲突会显示在实时日志中。", "Materials and textures keep their paths under materials and textures. Identical files can be reused. The live log identifies conflicting files."))

        if not getattr(self,"_prepared_jobs",None) and not getattr(self,"_last_output",None):
            self.scan_summary.setText(t("选择输入与输出，直接开始即可。", "Choose input and output, then start."))

        # 分组卡片
        self.dir_box.setTitle(tr("gb_dirs"))
        self.mode_box.setTitle(tr("gb_mode"))
        self.quick_box.setTitle(tr("gb_opts"))

        # 目录
        self.lbl_in.setText(tr("lbl_in"))
        self.lbl_out.setText(tr("lbl_out"))
        self.in_edit.setPlaceholderText(tr("ph_in"))
        self.out_edit.setPlaceholderText(tr("ph_out"))
        self.in_edit.setToolTip(tr("tip_in"))
        self.out_edit.setToolTip(tr("tip_out"))
        for btn in self._browse_btns:
            btn.setText(tr("btn_browse"))
            btn.setToolTip(tr("tip_browse"))

        # 转换模式
        self.mode_pills.set_labels([t("标准 · BC7","Standard · BC7"),t("紧凑 · BC1 / BC3","Compact · BC1 / BC3")])
        self.mode_pills.set_tooltips({
            "balanced": tr("tip_mode_balanced"),
            "space": tr("tip_mode_space"),
        })

        # 常用选项
        self.cb_keep.setText(tr("cb_keep"));   self.cb_keep.setToolTip(tr("tip_keep"))
        self.cb_mip.setText(tr("cb_mip"));     self.cb_mip.setToolTip(tr("tip_mip"))
        self.cb_gens.setText(tr("cb_gens"));   self.cb_gens.setToolTip(tr("tip_gens"))
        self.cb_metal.setText(tr("cb_metal")); self.cb_metal.setToolTip(tr("tip_metal"))
        self.cb_glow_l.setText(tr("cb_glow_l")); self.cb_glow_l.setToolTip(tr("tip_glow_l"))

        # 贴图页
        self.lbl_diffuse.setText(tr("lbl_diffuse"))
        self.lbl_normal.setText(tr("lbl_normal"))
        self.lbl_spec.setText(tr("lbl_spec"))
        self.lbl_glow.setText(tr("lbl_glow"))
        self.cmb_diffuse.setToolTip(tr("tip_fmt_d"))
        self.cmb_normal.setToolTip(tr("tip_fmt_n"))
        self.cmb_spec.setToolTip(tr("tip_fmt_s"))
        self.cmb_glow.setToolTip(tr("tip_fmt_g"))
        self.note_tex.setText(tr("note_tex"))

        # 材质页
        self.lbl_mat_normal.setText("Normal")
        self.lbl_mat_ao.setText("AO")
        self.cb_invert.setText(tr("cb_invert")); self.cb_invert.setToolTip(tr("tip_invert"))
        self.cb_ao.setText(tr("cb_ao"));         self.cb_ao.setToolTip(tr("tip_ao"))
        self.lbl_ao.setText(tr("lbl_ao"))
        self.lbl_metal.setText(tr("lbl_metal"))
        self.sp_ao.setToolTip(tr("tip_ao_str"))
        self.sp_metal.setToolTip(tr("tip_metal_str"))
        self.note_mat.setText(tr("note_mat"))

        # 输出页
        self.cb_overwrite.setText(tr("cb_overwrite")); self.cb_overwrite.setToolTip(tr("tip_overwrite"))
        self.cb_log.setText(tr("cb_log"));       self.cb_log.setToolTip(tr("tip_log"))
        self.cb_report.setText(tr("cb_report")); self.cb_report.setToolTip(tr("tip_report"))
        self.cb_bgsm.setText(tr("cb_bgsm"));     self.cb_bgsm.setToolTip(tr("tip_bgsm"))

        # 操作按钮
        self._set_task_labels()

    def _set_task_labels(self):
        materials = self.tabs.currentIndex() == self.materials_tab_index
        self.btn_analyze.set_text(t("预检材质", "Inspect materials") if materials else t("预检","Inspect"))
        self.btn_analyze.setToolTip(t("扫描源材质并统计待转换文件。","Scan the selected materials and count conversion jobs.") if materials else tr("tip_dry"))
        self.btn_start.set_text(t("转换材质", "Convert materials") if materials else tr("btn_start"))
        self.btn_start.setToolTip(self.materials_hint.text() if materials else tr("tip_start"))

    def _ensure_preview(self):
        if self._preview is None:
            from nif_preview import NifPreviewPanel
            self._preview=NifPreviewPanel(self)
            self.preview_layout.addWidget(self._preview)
            self._preview.busy_changed.connect(self._preview_busy_changed)
        converted = self.out_edit.text().strip()
        roots = (converted, self._last_material_source or self.in_edit.text().strip())
        if not self._preview.is_busy():
            if roots != getattr(self, "_preview_root_hint", None):
                self._preview.set_resource_roots(*roots)
                self._preview_root_hint = roots
            if self._preview_refresh_root and self.tabs.currentIndex() == self.preview_tab_index:
                output = self._preview_refresh_root
                self._preview_refresh_root = None
                self._preview.reload_after_export(output)
        return self._preview

    def _preview_busy_changed(self,*_):
        self.btn_start.setEnabled(not self._busy());self.btn_analyze.setEnabled(not self._busy())
        self.btn_cancel.setEnabled(self._busy())
        if self._close_pending and not self._busy():QTimer.singleShot(0,self.close)

    def _tab_changed(self,index):
        self._set_task_labels()
        preview=index==self.preview_tab_index
        for widget in (self.logview,self.proc_panel,self.statusbar):widget.setVisible(not preview)
        if preview:self._ensure_preview()

    def _reveal_tab(self,index):
        previous=getattr(self,'_motion_tab_index',0);self._motion_tab_index=index
        if index == self.preview_tab_index:
            self.navigation_motion.cancel()
            return
        self.navigation_motion.reveal(self.tabs.currentWidget(),1 if index>=previous else -1)

    def _reveal_advanced(self,index):
        previous=getattr(self,'_motion_advanced_index',0);self._motion_advanced_index=index
        self.navigation_motion.reveal(self.advanced.currentWidget(),1 if index>=previous else -1)

    def open_model_preview(self):
        self.tabs.setCurrentIndex(self.preview_tab_index)

    def _play_intro(self):
        self.motion.startup()

    def _startup_complete(self):
        if self._show_first_run_about and not self._close_pending:
            QTimer.singleShot(80,self.maybe_about)

    def _shutdown_failed(self,message):
        self._close_pending=False
        if self._preview is not None:
            self._preview.restore_viewport()
        self.scan_summary.setText(t('无法关闭窗口，已恢复界面：','Could not close the window; workspace restored: ')+message)

    def _invalidate_scan(self,*_):
        self._last_material_source = None
        self._prepared_jobs=None;self._scan_context=None
        self._material_summary_text = ("", "")
        self.materials_summary.clear()
        self.scan_summary.setText(t("开始转换时会自动扫描。预检为可选操作。", "Starting scans automatically. Inspection is optional."))

    def _apply_quality(self,key):
        preset=MODES.get(key,MODES["balanced"])
        for widget,field in ((self.cmb_diffuse,"diffuse_fmt"),(self.cmb_normal,"normal_fmt"),(self.cmb_spec,"specular_fmt"),(self.cmb_glow,"glow_fmt")):
            widget.setCurrentText(preset[field])

    # ================= 配置 <-> UI =================

    def _load_into_ui(self):
        c = self.cfg
        self.in_edit.setText(c.input_dir)
        self.materials_input.setText(c.materials_input or c.input_dir)
        self.out_edit.setText(c.output_dir)
        self.mode_pills.set_key(c.mode if c.mode in ("balanced","space") else "balanced",
                                animate=False)
        self.metal_panel.load(c)
        self.cb_keep.setChecked(c.keep_structure)
        self.cb_mip.setChecked(c.generate_mipmap)
        self.cb_gens.setChecked(c.generate_s)
        self.cb_metal.setChecked(c.metal_compensation)
        self.cb_glow_l.setChecked(c.glow_from_l_alpha)
        self.cb_invert.setChecked(c.invert_green)
        self.cb_ao.setChecked(c.ao_mode)
        self.sp_ao.setValue(c.ao_strength)
        self.sp_metal.setValue(c.metal_strength)
        self.cb_overwrite.setChecked(c.overwrite)
        self.cb_log.setChecked(c.generate_log)
        self.cb_report.setChecked(c.generate_report)
        self.cb_bgsm.setChecked(c.convert_bgsm)
        self.cmb_diffuse.setCurrentText(c.diffuse_fmt)
        self.cmb_normal.setCurrentText(c.normal_fmt)
        self.cmb_spec.setCurrentText(c.specular_fmt)
        self.cmb_glow.setCurrentText(c.glow_fmt)

    def _collect(self) -> Config:
        c = self.cfg
        c.input_dir = self.in_edit.text().strip()
        c.materials_input = self.materials_input.text().strip()
        c.output_dir = self.out_edit.text().strip()
        c.mode = self.mode_pills.current_key()
        c.lang = i18n.LANG
        c.apply_mode()
        c.diffuse_fmt = self.cmb_diffuse.currentText()
        c.normal_fmt = self.cmb_normal.currentText()
        c.specular_fmt = self.cmb_spec.currentText()
        c.glow_fmt = self.cmb_glow.currentText()
        c.keep_structure = True
        c.generate_mipmap = self.cb_mip.isChecked()
        c.generate_s = self.cb_gens.isChecked()
        c.metal_compensation = self.cb_metal.isChecked()
        c.glow_from_l_alpha = self.cb_glow_l.isChecked()
        c.invert_green = self.cb_invert.isChecked()
        c.ao_mode = self.cb_ao.isChecked()
        c.ao_strength = self.sp_ao.value()
        c.metal_strength = self.sp_metal.value()
        c.overwrite = False
        c.output_layout = 'game'
        c.generate_report = self.cb_report.isChecked()
        c.generate_log = c.generate_report
        c.convert_bgsm = self.cb_bgsm.isChecked()
        self.metal_panel.collect(c)
        cfgmod.save_config(c)
        return c

    def _save_report_preference(self, checked):
        self.cfg.generate_report = checked
        self.cfg.generate_log = checked
        cfgmod.save_config(self.cfg)

    def _export_root(self, path):
        from material_jobs import game_output_root
        try:
            target = game_output_root(path)
            selected = Path(os.path.abspath(path))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, APP_TITLE, str(exc))
            return None
        if selected == target:
            return str(target)
        question = t(f"所选位置是资源子目录。是否将以下位置作为导出根目录，在其中创建 materials 和 textures？\n\n{target}",
                     f"The selected folder is an asset subfolder. Use the following export root for materials and textures?\n\n{target}")
        if QMessageBox.question(self, APP_TITLE, question, QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) == QMessageBox.Yes:
            return str(target)
        return None

    # ================= 目录与拖放 =================

    def _pick_dir(self, edit: QLineEdit, title_key: str):
        start = edit.text()
        if os.path.isfile(start):
            start = os.path.dirname(start)
        d = QFileDialog.getExistingDirectory(self, tr(title_key), start)
        if d and title_key == 'pick_out':
            d = self._export_root(d)
        if d:
            edit.setText(d)

    def _pick_material_file(self):
        path, _ = QFileDialog.getOpenFileName(self, self.materials_pick_file.toolTip(),
                                               self.materials_input.text(), "BGSM / BGEM (*.bgsm *.bgem)")
        if path:
            self.materials_input.setText(path)

    def dragEnterEvent(self, e):
        if not self._busy() and e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        if self._busy():return
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if p.lower().endswith('.nif') and os.path.isfile(p):
                self.open_model_preview();self._ensure_preview().open_nif(p);break
            if self.tabs.currentIndex() == self.materials_tab_index and (os.path.isdir(p) or
                    (Path(p).suffix.lower() in ('.bgsm','.bgem') and os.path.isfile(p))):
                self.materials_input.setText(p)
                break
            if os.path.isdir(p):
                self.in_edit.setText(p)
                # 拖放反馈：状态栏提示
                self.statusbar.set_state("idle")
                self.proc_panel.set_progress(0, 0, f"INPUT ← {p}")
                break

    # ================= 校验 =================

    def _validate(self) -> Config | None:
        c = self._collect()
        if self.tabs.currentIndex() == self.materials_tab_index:
            c = copy.copy(c)
            c.input_dir = c.materials_input
            c.include_textures = False
            c.convert_bgsm = True
            c.metal_mode = 'off'
            source_path = Path(c.input_dir)
            if not c.input_dir or not (source_path.is_dir() or
                    (source_path.suffix.lower() in ('.bgsm','.bgem') and source_path.is_file())):
                QMessageBox.warning(self, APP_TITLE, self.materials_input.toolTip())
                return None
        elif not c.input_dir or not os.path.isdir(c.input_dir):
            QMessageBox.warning(self, APP_TITLE, tr("msg_no_in"))
            return None
        if not c.output_dir:
            QMessageBox.warning(self, APP_TITLE, tr("msg_no_out"))
            return None
        output_dir = self._export_root(c.output_dir)
        if output_dir is None:
            return None
        if c.output_dir != output_dir:
            self.out_edit.setText(output_dir)
            c.output_dir = self.cfg.output_dir = output_dir
            cfgmod.save_config(self.cfg)
        from material_jobs import asset_root
        source = os.path.normcase(str(asset_root(c.input_dir)))
        output = os.path.normcase(os.path.realpath(output_dir))
        try: overlap=os.path.commonpath([source,output]) in (source,output)
        except ValueError: overlap=False
        if overlap:
            QMessageBox.warning(self, APP_TITLE, t("输入与输出不能相同或互相嵌套。", "Input and output cannot be identical or nested."))
            return None
        return c

    # ================= 扫描 / Dry Run =================

    def _busy(self):
        return bool((self.worker and self.worker.isRunning()) or (self.scanner and self.scanner.isRunning()) or (self._preview is not None and self._preview.is_busy()))

    def _begin_task(self,stage):
        self._task_started=time.perf_counter();self._last_done=self._last_total=0
        self.run_metrics.show();self.run_metrics.setText(t("准备任务…","Preparing…"))
        self.tabs.setEnabled(False);self.btn_cancel.setEnabled(True)
        self.btn_start.setEnabled(False);self.btn_analyze.setEnabled(False)
        self.statusbar.set_progress_info(0,0)
        self.proc_panel.reset();self.proc_panel.set_stage(stage)
        self.statusbar.set_state(stage);self.banner.set_count(0,0)
        self.banner.set_running(True);self.logview.set_running(True)
        self._event_timer.start()

    def on_dry_run(self):
        if self._busy():return
        cfg=self._validate()
        if cfg is None:return
        self._begin_task("scanning")
        self.logview.append_system(tr("sys_scan"))
        self.scanner=ScanWorker(cfg.input_dir, getattr(cfg, 'include_textures', True))
        self._active_task=self.scanner
        self.scanner.done.connect(self._scanned)
        self.scanner.failed.connect(self._on_failed)
        self.scanner.finished.connect(self._re_enable)
        self.scanner.start()

    def _scanned(self,jobs):
        self._drain_worker_events(final=True)
        self._prepared_jobs=jobs;self._scan_context=getattr(jobs,"scan_context",None)
        materials=sum(bool(j.get("material_path")) for j in jobs)
        if self.tabs.currentIndex() == self.materials_tab_index:
            issues = {"MATERIAL_PARSE_ERROR", "SOURCE_DIRECTION_MISMATCH"}
            pending = sum(any(item.split(':',1)[0] in issues for item in job.get("diagnostics", [])) for job in jobs)
            self._material_summary_text = (f"已预检 {materials:,} 个材质 · {pending:,} 个有格式或转换方向问题",
                                           f"Inspected {materials:,} materials · {pending:,} with format or direction issues")
            message = t(*self._material_summary_text)
            self.materials_summary.setText(message)
        else:
            pending=sum(any(d.split(':',1)[0]!="READY_FOR_METAL_ANALYSIS" for d in j.get("diagnostics",[])) for j in jobs)
            message = t(f"预检完成：{len(jobs):,} 组任务，{materials:,} 个材质，{pending:,} 组需检查。",f"Inspected: {len(jobs):,} jobs, {materials:,} materials, {pending:,} groups to review.")
            self.scan_summary.setText(message)
        self.statusbar.set_state("idle");self.proc_panel.set_stage("idle")
        self.banner.set_running(False);self.logview.set_running(False)
        self.logview.append_system(message)

    def on_start(self):
        if self._busy():return
        cfg=self._validate()
        if cfg is not None:self._run_convert(cfg)

    def _run_convert(self,cfg=None):
        cfg=cfg or self._validate()
        if cfg is None:return
        self.logview.clear_log();self.banner.set_count(0,0)
        self._begin_task("running");self.logview.append_system(tr("sys_start"))
        self._last_report_dir = None
        self.btn_log.setEnabled(False)
        self.cb_report.setEnabled(False)
        self.worker=ConvertWorker(cfg,jobs=self._prepared_jobs,scan_context=self._scan_context)
        self._active_task=self.worker
        self.worker.done.connect(self._on_convert_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self._re_enable)
        self.worker.start()

    def _drain_worker_events(self,final=False):
        worker=self._active_task
        if worker is not None:
            update=worker.events.drain(512 if final else 80)
            entries=[]
            if update['omitted']:
                entries.append(('INFO',t(f"实时日志已合并 {update['omitted']:,} 条较早的记录。",f"The live log condensed {update['omitted']:,} earlier entries."),''))
            entries.extend(update['logs'])
            if entries:self.logview.append_entries(entries)
            if update['progress']:self._on_progress(*update['progress'])
            if worker is self.worker:self.proc_panel.set_counters(update['warnings'],update['errors'])

    def _on_progress(self, done: int, total: int, name: str):
        self._last_done,self._last_total=done,total
        if self._task_started is not None:
            elapsed=max(.001,time.perf_counter()-self._task_started)
            stats=(t("扫描中", "Scanning") if total==0 else t(f"已处理 {done:,} / {total:,} 组",f"Processed {done:,} / {total:,} groups"))
            rate=(t(f" · {done/elapsed:.1f} 组/秒",f" · {done/elapsed:.1f} groups/s") if total>0 and done>0 else "")
            self.run_metrics.setText(stats+rate+t(f" · 用时 {elapsed:.1f} 秒",f" · {elapsed:.1f}s elapsed"))
        stage="scanning" if total==0 else "running"
        self.statusbar.set_state(stage);self.proc_panel.set_stage(stage)
        self.statusbar.set_progress_info(done, total)
        self.proc_panel.set_progress(done, total, name)
        self.banner.set_count(done, total)
        if self._active_task is self.worker and self.worker is not None:
            self.proc_panel.set_counters(self.worker.warning_count,
                                         self.worker.error_count)

    def _on_convert_done(self,log):
        self._drain_worker_events(final=True)
        cancelled=bool(self.worker and self.worker.converter.cancel_event.is_set())
        phase="idle" if cancelled else "error" if log.errors else "done"
        self.statusbar.set_state(phase);self.proc_panel.set_stage(phase)
        self.proc_panel.set_counters(log.warnings,log.errors)
        self.logview.set_running(False);self.banner.set_running(False)
        self.banner.set_count(log.success_total,0)
        self._last_output = getattr(log, "output_dir", None)
        self._last_report_dir = getattr(log, "report_dir", None)
        if self.worker is not None:
            from material_jobs import asset_root
            self._last_material_source = str(asset_root(self.worker.cfg.input_dir))
        self.btn_output.setEnabled(bool(self._last_output))
        self.btn_log.setEnabled(bool(self._last_report_dir))
        if self._preview is not None:
            if log.success_total:
                self._preview_refresh_root = self._last_output
            self._ensure_preview()
        message=(t("已取消。", "Cancelled. ") if cancelled else t("处理结束。", "Finished. "))+log.summary()
        if self.tabs.currentIndex() == self.materials_tab_index:
            phase_zh, phase_en = ("已取消", "Cancelled") if cancelled else ("已完成", "Finished")
            self._material_summary_text = (
                f"{phase_zh} · 成功 {log.success_total:,} · 警告 {log.warnings:,} · 错误 {log.errors:,}",
                f"{phase_en} · {log.success_total:,} succeeded · {log.warnings:,} warnings · {log.errors:,} errors")
            message = t(*self._material_summary_text)
            self.materials_summary.setText(message)
        else:
            self.scan_summary.setText(message)
        self.logview.append_system(" · ".join(message.splitlines()))
        if self._last_output:
            self.logview.append_system(t("导出根目录　", "Export root  ") + str(self._last_output))
        if self._last_report_dir:
            self.logview.append_system(t("报告目录　", "Reports  ") + str(self._last_report_dir))

    def open_output(self):
        if self._last_output and os.path.isdir(self._last_output):os.startfile(self._last_output)

    def on_view_log(self):
        path = os.path.join(self._last_report_dir, "conversion_log.txt") if self._last_report_dir else None
        if path and os.path.isfile(path):
            # Large reports stay out of the GUI event loop.
            os.startfile(path)
        else:
            QMessageBox.information(self, APP_TITLE, tr("msg_no_log"))

    def _on_failed(self, tb: str):
        self._drain_worker_events(final=True)
        self.statusbar.set_state("error")
        self.proc_panel.set_stage("error")
        self.logview.set_running(False)
        self.logview.append_system(tr("sys_failed"))
        self.banner.set_running(False)
        message = tb.splitlines()[-1] if tb else tr("sys_failed")
        self.logview.append_system(message)
        dialog = QMessageBox(QMessageBox.Critical, APP_TITLE, message, QMessageBox.Ok, self)
        dialog.setTextFormat(Qt.PlainText)
        dialog.setDetailedText(tb)
        dialog.exec()

    def _re_enable(self):
        work_busy=bool((self.worker and self.worker.isRunning()) or (self.scanner and self.scanner.isRunning()))
        busy=self._busy()
        self.btn_start.setEnabled(not busy); self.btn_analyze.setEnabled(not busy)
        self.tabs.setEnabled(not work_busy); self.btn_cancel.setEnabled(busy)
        self.cb_report.setEnabled(not work_busy)
        if not work_busy:
            self._drain_worker_events(final=True);self._event_timer.stop()
            self.logview.set_running(False);self.banner.set_running(False)
            if self._close_pending and not busy:QTimer.singleShot(0,self.close)

    def cancel_work(self):
        if self._preview is not None and self._preview.is_busy():self._preview.cancel()
        if self.scanner and self.scanner.isRunning():
            self.scanner.cancel_event.set()
            self.statusbar.set_state("idle");self.proc_panel.set_stage("idle");self.banner.set_running(False)
            self.logview.append_system(t("扫描已取消。", "Scan cancelled."))
        if self.worker and self.worker.isRunning():
            self.worker.converter.cancel_event.set()
            self.btn_cancel.setEnabled(False)
            self.logview.append_system(t("正在取消：停止当前编解码并清理未提交结果。", "Cancelling the current codec operation and cleaning uncommitted output."))

    def show_about(self):
        text=t(f"作者：{APP_AUTHOR}\n版本：{APP_VERSION}\n项目代码：GPL-3.0\n\n使用 GPT-6 Astra 完成开发，代码与界面包含 AI 生成内容。本工具不代表 OpenAI 或 Bethesda 背书。\n\n本地离线处理。金属预设为实验性质；文件校验通过不等于游戏内视觉验收。\n\n许可与第三方组件说明随程序提供，请查看 LICENSE 和 THIRD_PARTY_NOTICES.txt。字体和游戏资源不属于项目代码许可范围。", f"Author: {APP_AUTHOR}\nVersion: {APP_VERSION}\nProject code: GPL-3.0\n\nDeveloped using GPT-6 Astra. Code and interface include AI-generated content. Not endorsed by OpenAI or Bethesda.\n\nLocal, offline processing. Metal profiles are experimental; file validation does not equal in-game visual acceptance.\n\nSee bundled LICENSE and THIRD_PARTY_NOTICES.txt for project and dependency terms. Project code licensing does not cover fonts or game resources.")
        TextDialog(t("关于与许可", "About & licenses"),text,self).exec()
        self.cfg.about_seen=True; cfgmod.save_config(self.cfg)

    def maybe_about(self):
        if self._show_first_run_about and self.motion.state=='ready' and self.isVisible() and not self.isMinimized() and not self._close_pending and not self.cfg.about_seen:self.show_about()

    # ================= Single-window transition =================
    def closeEvent(self,e):
        if self._busy():
            self._close_pending=True;self.cancel_work();e.ignore();return
        if getattr(self,"_closing",False) or self.motion.closed:
            self.navigation_motion.cancel()
            self._event_timer.stop();self._icon_timer.stop()
            if self._preview is not None:self._preview.shutdown()
            e.accept();return
        self._collect();e.ignore();self._show_first_run_about=False
        self.navigation_motion.cancel()
        if self._preview is not None and self._preview.isVisible() and not self.motion.reduced_motion:
            self._preview.freeze_for_close()
        self.motion.shutdown(lambda: QTimer.singleShot(0,self.close))



# ---------------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    app.setApplicationVersion(APP_VERSION)
    families = cfgmod.register_fonts()
    if families:
        print("fonts:", ", ".join(sorted(set(families))))
    icon_pm, icon_key = cfgmod.load_app_icon()
    if icon_pm is not None:
        app.setWindowIcon(QIcon(icon_pm))
    w = MainWindow(icon_pm, icon_key)
    if icon_pm is not None:
        w.setWindowIcon(QIcon(icon_pm))

    w._show_first_run_about=True
    w.motion.startup()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
