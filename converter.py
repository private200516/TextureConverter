# -*- coding: utf-8 -*-
"""
converter.py — 转换编排：扫描 → 逐材质集处理 → 输出 FO4 目录结构。

- 绝不修改输入文件
- 单个文件失败不终止任务
- 保留目录结构（可关）
- texconv 从程序内部资源提取到 %TEMP%\\FO76_to_FO4 后调用
- 支持取消（threading.Event）
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

import dds
import material
from config import Config, resource_path
from i18n import tr
from logger import ConversionLog
from scanner import MaterialSet, ScanReport, classify_filename, scan_directory


def scanner_classify(path: str) -> tuple[str | None, str | None]:
    """按文件名识别贴图角色，返回 (stem, suffix)。"""
    return classify_filename(os.path.basename(path))

# ---------------------------------------------------------------------------
# texconv 部署
# ---------------------------------------------------------------------------

_texconv_lock = threading.Lock()
_extracted_texconv: str | None = None


def get_texconv() -> str | None:
    """Use the bundled tool in place; no shared temporary executable or global cleanup."""
    path = resource_path("tools", "texconv.exe")
    return path if os.path.isfile(path) else None


# ---------------------------------------------------------------------------
# 转换器
# ---------------------------------------------------------------------------

class Converter:
    def __init__(self, cfg: Config, log: ConversionLog | None = None, *, jobs=None, scan_context=None):
        self.cfg = cfg
        self.prepared_jobs = jobs
        self.scan_context = scan_context if scan_context is not None else getattr(jobs,"scan_context",None)
        self.scan_reused = False
        self.log = log or ConversionLog()
        self.cancel_event = threading.Event()
        self.texconv = get_texconv()
        self._progress_cb = None
        self._done = 0
        self._total = 0
        self._plock = threading.Lock()

    # -- 进度回调 ---------------------------------------------------------

    def set_progress_cb(self, cb) -> None:
        """cb(done:int, total:int, current_file:str)"""
        self._progress_cb = cb

    def _tick(self, name: str) -> None:
        with self._plock:
            self._done += 1
            done, total = self._done, self._total
        if self._progress_cb:
            self._progress_cb(done, total, name)

    # -- 路径 -------------------------------------------------------------

    def _out_path(self, rel_dir: str, filename: str) -> str:
        if self.cfg.keep_structure and rel_dir:
            return os.path.join(self.cfg.output_dir, rel_dir, filename)
        return os.path.join(self.cfg.output_dir, filename)

    # -- 单张贴图处理 ------------------------------------------------------

    def _convert_map(self, src: str, out: str, fmt: str, rel: str,
                     transform=None, force_no_bc1_if_alpha: bool = False):
        """
        解码 src → 可选 transform(rgba, ctx) → 编码为 fmt 输出。
        返回 None（成功）或抛出异常。
        """
        dds._check_cancel(self.cancel_event)
        if os.path.exists(out) and not self.cfg.overwrite:
            self.log.add("SKIPPED", rel, tr("nt_skip_exists"))
            return
        info = dds.parse_dds_header(src)
        rgba = dds.decode_dds_pixels(src, info, cancel_event=self.cancel_event)
        src_fmt = info.fmt

        ctx = {}
        if transform is not None:
            rgba, tnotes = transform(rgba, ctx)
            dds._check_cancel(self.cancel_event)
        else:
            tnotes = []

        actual_fmt = fmt
        if force_no_bc1_if_alpha and fmt == "BC1_UNORM" and material.has_alpha(rgba):
            actual_fmt = "BC3_UNORM"  # Diffuse 含 Alpha 时不得强制 BC1
            tnotes.append(tr("nt_alpha_bc3"))

        used_fmt, warn = dds.encode_dds(rgba, actual_fmt, out,
                                        mipmaps=self.cfg.generate_mipmap,
                                        texconv_path=self.texconv,
                                        cancel_event=self.cancel_event)
        msg = f"{src_fmt} → {used_fmt}"
        if tnotes:
            msg += " | " + "; ".join(tnotes)
        self.log.add("OK", rel, msg)
        if warn:
            self.log.add("WARNING", rel, warn)

    # -- 材质集处理 --------------------------------------------------------

    def _process_set(self, ms: MaterialSet):
        cfg = self.cfg
        dds._check_cancel(self.cancel_event)
        stem = ms.stem
        maps = ms.maps
        name_of = lambda suf: f"{stem}_{suf}.dds"

        # 预载 _l / _r（可能多处用到）
        l_img = r_img = None
        if any(k in maps for k in ("l",)) and (cfg.generate_s or cfg.ao_mode or cfg.glow_from_l_alpha):
            l_img = dds.decode_dds_pixels(maps["l"], cancel_event=self.cancel_event) if "l" in maps else None
        if "r" in maps and (cfg.generate_s or cfg.metal_compensation):
            r_img = dds.decode_dds_pixels(maps["r"], cancel_event=self.cancel_event)

        # --- Diffuse ---
        if "d" in maps:
            def d_transform(rgba, _ctx):
                notes = []
                if cfg.ao_mode and l_img is not None:
                    rgba = material.apply_ao_to_diffuse(rgba, l_img, cfg.ao_strength)
                    notes.append(f"AO×{cfg.ao_strength:.2f}")
                if cfg.metal_compensation and r_img is not None:
                    rgba = material.apply_metal_compensation(rgba, r_img, cfg.metal_strength)
                    notes.append(f"Metal×{cfg.metal_strength:.2f}")
                return rgba, notes

            rel = os.path.join(ms.rel_dir, name_of("d"))
            self._convert_map(maps["d"], self._out_path(ms.rel_dir, name_of("d")),
                              cfg.diffuse_fmt, rel, transform=d_transform,
                              force_no_bc1_if_alpha=True)
            self._tick(rel)

        # --- Normal（SNORM→UNORM 在解码时已处理，绝不只改 Header）---
        if "n" in maps:
            def n_transform(rgba, _ctx):
                notes = []
                if cfg.invert_green:
                    rgba = material.invert_green_channel(rgba)
                    notes.append("Invert Green")
                return rgba, notes

            rel = os.path.join(ms.rel_dir, name_of("n"))
            self._convert_map(maps["n"], self._out_path(ms.rel_dir, name_of("n")),
                              cfg.normal_fmt, rel, transform=n_transform)
            self._tick(rel)

        # --- Glow ---
        if "g" in maps:
            rel = os.path.join(ms.rel_dir, name_of("g"))
            self._convert_map(maps["g"], self._out_path(ms.rel_dir, name_of("g")),
                              cfg.glow_fmt, rel)
            self._tick(rel)
        elif cfg.glow_from_l_alpha and l_img is not None:
            try:
                glow = material.build_glow_from_l_alpha(l_img)
                out = self._out_path(ms.rel_dir, name_of("g"))
                used, warn = dds.encode_dds(glow, cfg.glow_fmt, out,
                                            mipmaps=cfg.generate_mipmap,
                                            texconv_path=self.texconv,
                                            cancel_event=self.cancel_event)
                self.log.add("GENERATED", os.path.join(ms.rel_dir, name_of("g")),
                             tr("nt_glow", fmt=used))
                if warn:
                    self.log.add("WARNING", os.path.join(ms.rel_dir, name_of("g")), warn)
            except dds.CancellationRequested:
                raise
            except Exception as e:  # noqa: BLE001
                self.log.add("ERROR", os.path.join(ms.rel_dir, name_of("g")), str(e))

        # --- _l + _r → _s ---
        if cfg.generate_s:
            if "l" in maps and "r" in maps:
                try:
                    s = material.build_specular(l_img, r_img)
                    out = self._out_path(ms.rel_dir, name_of("s"))
                    used, warn = dds.encode_dds(s, cfg.specular_fmt, out,
                                                mipmaps=cfg.generate_mipmap,
                                                texconv_path=self.texconv,
                                                cancel_event=self.cancel_event)
                    self.log.add("GENERATED", os.path.join(ms.rel_dir, name_of("s")),
                                 f"R = Reflectance(Rec.709), G = Smoothness → {used}")
                    if warn:
                        self.log.add("WARNING", os.path.join(ms.rel_dir, name_of("s")), warn)
                except dds.CancellationRequested:
                    raise
                except Exception as e:  # noqa: BLE001
                    self.log.add("ERROR", os.path.join(ms.rel_dir, name_of("s")), str(e))
            elif "l" in maps:
                self.log.add("WARNING", os.path.join(ms.rel_dir, stem + "_r.dds") + " missing",
                             tr("nt_missing_r"))

        # --- 输入已有 _s：直接格式转换 ---
        if "s" in maps:
            rel = os.path.join(ms.rel_dir, name_of("s"))
            self._convert_map(maps["s"], self._out_path(ms.rel_dir, name_of("s")),
                              cfg.specular_fmt, rel)
            self._tick(rel)

        # --- _l / _r 本身不输出（已并入 _s），计数推进 ---
        for suf in ("l", "r"):
            if suf in maps:
                self._tick(os.path.join(ms.rel_dir, name_of(suf)))

    # -- BGSM / BGEM --------------------------------------------------------

    def _convert_bgsm(self, src: str, report_dir: str):
        from material_converter import convert_file
        dest = self._out_path(report_dir, os.path.basename(src))
        info = convert_file(src, dest)
        rel = os.path.relpath(src, self.cfg.input_dir)
        self.log.add("OK", rel, "Field-level BGSM/BGEM conversion")
        for warning in info["warnings"]:
            self.log.add("WARNING", rel, warning)

    # -- 主流程 -------------------------------------------------------------

    def run(self) -> ConversionLog:
        """All modes use isolated runs; off retains the ordinary pixel algorithms."""
        from texture_api import scan_jobs, run_jobs
        from material_jobs import scan_context_matches
        direction=getattr(self.cfg,"direction","76-to-4")
        include_textures=getattr(self.cfg,"include_textures",True)
        if self.prepared_jobs is not None and scan_context_matches(self.scan_context,self.cfg.input_dir,direction,include_textures,self.cancel_event):
            jobs=self.prepared_jobs;self.scan_reused=True
        else:
            jobs=scan_jobs(self.cfg.input_dir,direction=direction,include_textures=include_textures,cancel_event=self.cancel_event,progress_cb=self._progress_cb)
        self.prepared_jobs=None;self.scan_context=None
        if not self.cfg.convert_bgsm:
            jobs = [dict(job, material_path=None, kind="texture_group")
                    for job in jobs if job["texture_paths"]]
        # The shared pipeline selects its fixed bundled codec. Configuration
        # and per-job processing data cannot supply an executable path.
        self.log = run_jobs(jobs, self.cfg, self._progress_cb, self.log.on_entry, self.cancel_event)
        return self.log

    def _run_legacy(self) -> ConversionLog:
        cfg = self.cfg
        t0 = time.time()
        report: ScanReport = scan_directory(cfg.input_dir)

        # 总工作量 = 分类 DDS 数（l/r 计入所属 set 的 tick）
        self._total = report.total_dds + len(report.bgsm_files)
        self._done = 0

        # 未分类 DDS：原样复制
        for src in report.unclassified:
            if self.cancel_event.is_set():
                break
            rel = os.path.relpath(src, cfg.input_dir)
            try:
                out = self._out_path(os.path.dirname(rel), os.path.basename(src))
                if os.path.exists(out) and not cfg.overwrite:
                    self.log.add("SKIPPED", rel, tr("nt_skip_short"))
                else:
                    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
                    shutil.copy2(src, out)
                    self.log.add("COPIED", rel, tr("nt_copy_suffix"))
            except Exception as e:  # noqa: BLE001
                self.log.add("ERROR", rel, str(e))
            self._tick(rel)

        # 材质集（小线程池并行；单文件失败不影响整体）
        sets = list(report.sets.values())

        def work(ms: MaterialSet):
            if self.cancel_event.is_set():
                return
            try:
                self._process_set(ms)
            except dds.CancellationRequested:
                return
            except Exception as e:  # noqa: BLE001
                self.log.add("ERROR", os.path.join(ms.rel_dir, ms.stem), tr("nt_set_fail", e=e))
                # 补齐该 set 的进度，避免进度条卡住
                for _ in ms.maps:
                    self._tick(ms.stem)

        workers = min(4, (os.cpu_count() or 4))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(work, sets))

        # BGSM / BGEM
        if cfg.convert_bgsm:
            for src in report.bgsm_files:
                if self.cancel_event.is_set():
                    break
                rel_dir = os.path.dirname(os.path.relpath(src, cfg.input_dir))
                try:
                    self._convert_bgsm(src, rel_dir)
                except Exception as e:  # noqa: BLE001
                    self.log.add("ERROR", os.path.relpath(src, cfg.input_dir), str(e))
                self._tick(os.path.relpath(src, cfg.input_dir))

        # 日志与报告
        if cfg.generate_log:
            try:
                self.log.write(os.path.join(cfg.output_dir, "conversion_log.txt"))
            except OSError as e:
                self.log.add("ERROR", "conversion_log.txt", tr("nt_log_fail", e=e))
        if cfg.generate_report:
            try:
                from scanner import format_report
                with open(os.path.join(cfg.output_dir, "conversion_report.txt"),
                          "w", encoding="utf-8") as f:
                    f.write(tr("rep_file_title") + "\n" + "=" * 60 + "\n\n")
                    f.write(format_report(report))
                    f.write("\n\n" + "=" * 60 + "\n")
                    f.write(tr("rep_elapsed", s=time.time() - t0) + "\n")
                    f.write(self.log.summary() + "\n")
            except OSError:
                pass
        return self.log
