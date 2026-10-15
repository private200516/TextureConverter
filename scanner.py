# -*- coding: utf-8 -*-
"""
scanner.py — 递归扫描输入目录，识别 FO76 贴图并分组为 Material Set。

识别后缀（大小写不敏感）：
    _d  Diffuse
    _n  Normal
    _l  FO76 PBR Lighting 贴图 (R=Smoothness, G=AO, B=SSS, A=Glow)
    _r  Reflectance
    _g  Glow
    _s  Specular（已存在则直接转换）
其余后缀的 DDS 作为「未分类」原样复制。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from i18n import tr

KNOWN_SUFFIXES = ("d", "n", "l", "r", "g", "s")
_SUFFIX_RE = re.compile(r"^(?P<stem>.+?)_(?P<suffix>d|n|l|r|g|s)$", re.IGNORECASE)


@dataclass
class MaterialSet:
    """同一 stem + 同一相对目录下的一组贴图。"""
    stem: str
    rel_dir: str
    maps: dict[str, str] = field(default_factory=dict)  # suffix -> abs path

    @property
    def has_diffuse(self) -> bool:
        return "d" in self.maps

    @property
    def has_normal(self) -> bool:
        return "n" in self.maps

    @property
    def can_generate_s(self) -> bool:
        return "l" in self.maps and "r" in self.maps


@dataclass
class ScanReport:
    total_dds: int = 0
    counts: dict[str, int] = field(default_factory=dict)  # suffix -> count
    unclassified: list[str] = field(default_factory=list)
    sets: dict[tuple[str, str], MaterialSet] = field(default_factory=dict)
    incomplete_sets: list[tuple[str, str, list[str]]] = field(default_factory=list)
    bgsm_files: list[str] = field(default_factory=list)

    @property
    def will_generate_s(self) -> int:
        return sum(1 for s in self.sets.values() if s.can_generate_s)


def classify_filename(name: str) -> tuple[str | None, str | None]:
    """返回 (stem, suffix)；未识别返回 (None, None)。"""
    base = os.path.splitext(name)[0]
    m = _SUFFIX_RE.match(base)
    if not m:
        return None, None
    return m.group("stem"), m.group("suffix").lower()


def scan_directory(root: str, cancel_event=None) -> ScanReport:
    report = ScanReport()
    for dirpath, _dirnames, filenames in os.walk(root):
        if cancel_event is not None and cancel_event.is_set():return report
        _dirnames[:]=[n for n in _dirnames if not os.path.islink(os.path.join(dirpath,n)) and not os.path.isjunction(os.path.join(dirpath,n))]
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == ".":
            rel_dir = ""
        for fn in filenames:
            if cancel_event is not None and cancel_event.is_set():return report
            low = fn.lower()
            full = os.path.join(dirpath, fn)
            if low.endswith((".bgsm", ".bgem")):
                report.bgsm_files.append(full)
                continue
            if not low.endswith(".dds"):
                continue
            report.total_dds += 1
            stem, suffix = classify_filename(fn)
            if suffix is None:
                report.unclassified.append(full)
                continue
            report.counts[suffix] = report.counts.get(suffix, 0) + 1
            key = (rel_dir, stem.lower())
            ms = report.sets.get(key)
            if ms is None:
                ms = MaterialSet(stem=stem, rel_dir=rel_dir)
                report.sets[key] = ms
            if suffix in ms.maps:
                # 同名重复（大小写不同等）——保留先到的，忽略后者
                continue
            ms.maps[suffix] = full

    # 完整性检查：有 _d 但缺 _n，或有 _l 缺 _r 等
    for (rel_dir, _), ms in sorted(report.sets.items()):
        missing = []
        if ms.has_diffuse and not ms.has_normal:
            missing.append("_n")
        if "l" in ms.maps and "r" not in ms.maps:
            missing.append("_r")
        if "r" in ms.maps and "l" not in ms.maps:
            missing.append("_l")
        if missing:
            label = os.path.join(rel_dir, ms.stem) if rel_dir else ms.stem
            report.incomplete_sets.append((label, rel_dir, missing))
    return report


def format_report(report: ScanReport) -> str:
    """生成预扫描/仅分析结果文本。"""
    c = report.counts
    lines = [
        tr("rep_found", n=report.total_dds),
        "",
        f"Diffuse (_d)：{c.get('d', 0)}",
        f"Normal  (_n)：{c.get('n', 0)}",
        f"_l：{c.get('l', 0)}",
        f"_r：{c.get('r', 0)}",
        f"Glow (_g)：{c.get('g', 0)}",
        tr("rep_s_exist", n=c.get('s', 0)),
        tr("rep_unclassified", n=len(report.unclassified)),
        f"BGSM / BGEM：{len(report.bgsm_files)}",
        "",
        tr("rep_sets", n=len(report.sets)),
        tr("rep_incomplete", n=len(report.incomplete_sets)),
        "",
        tr("rep_will_s", n=report.will_generate_s),
    ]
    if report.incomplete_sets:
        lines.append("")
        lines.append(tr("rep_missing_hdr"))
        for label, _rel, missing in report.incomplete_sets[:50]:
            lines.append(tr("rep_missing_item", label=label,
                            miss=", ".join(missing)))
        if len(report.incomplete_sets) > 50:
            lines.append(tr("rep_more", n=len(report.incomplete_sets) - 50))
    if report.unclassified:
        lines.append("")
        lines.append(tr("rep_uncl_hdr"))
        for p in report.unclassified[:30]:
            lines.append(f"  {p}")
        if len(report.unclassified) > 30:
            lines.append(tr("rep_more", n=len(report.unclassified) - 30))
    return "\n".join(lines)
