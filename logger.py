# -*- coding: utf-8 -*-
"""
logger.py — 转换日志（conversion_log.txt）与计数统计。
"""

from __future__ import annotations

import os
import threading
import time
import shutil
from collections import deque
from dataclasses import dataclass, field

from i18n import tr


@dataclass
class LogEntry:
    level: str          # OK / GENERATED / COPIED / WARNING / ERROR / SKIPPED
    rel_path: str
    message: str


@dataclass
class ConversionLog:
    entries: list[LogEntry] = field(default_factory=list)
    ok: int = 0
    generated: int = 0
    copied: int = 0
    warnings: int = 0
    errors: int = 0
    skipped: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # 可选回调：callable(level, rel_path, message)，GUI 实时日志用
    on_entry: object = None
    max_entries: int = 5000
    _stream: object = field(default=None, repr=False)
    _stream_path: str | None = field(default=None, repr=False)
    total_entries: int = 0

    def __post_init__(self):
        self.entries = deque(self.entries, maxlen=self.max_entries)

    def start_stream(self, path: str) -> None:
        """Keep the full batch log on disk while retaining only a recent RAM window."""
        with self._lock:
            if self._stream is not None:raise RuntimeError("Log stream already open")
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            self._stream_path=os.path.abspath(path)
            self._stream=open(path,"w",encoding="utf-8",buffering=65536)
            self._stream.write(tr("log_file_title")+"\n")
            self._stream.write(f"{tr('log_file_time')}: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            self._stream.write("="*60+"\n\n")
            for entry in self.entries:self._write_entry(self._stream,entry)

    @staticmethod
    def _write_entry(stream, entry):
        stream.write(f"[{entry.level}]\n{entry.rel_path}\n{entry.message}\n\n")

    def add(self, level: str, rel_path: str, message: str) -> None:
        with self._lock:
            entry=LogEntry(level, rel_path, message)
            self.entries.append(entry)
            self.total_entries+=1
            if self._stream is not None:self._write_entry(self._stream,entry)
            if level == "OK":
                self.ok += 1
            elif level == "GENERATED":
                self.generated += 1
            elif level == "COPIED":
                self.copied += 1
            elif level == "WARNING":
                self.warnings += 1
            elif level == "ERROR":
                self.errors += 1
            elif level == "SKIPPED":
                self.skipped += 1
            cb = self.on_entry
        if cb:
            try:
                cb(level, rel_path, message)
            except Exception:  # noqa: BLE001 — 回调绝不影响转换
                pass

    @property
    def success_total(self) -> int:
        return self.ok + self.generated + self.copied

    def summary(self) -> str:
        return tr("log_summary", total=self.success_total, ok=self.ok,
                  gen=self.generated, cp=self.copied,
                  warn=self.warnings, err=self.errors)

    def write(self, path: str) -> None:
        with self._lock:
            if self._stream is not None:
                self._stream.write("="*60+"\n"+self.summary()+"\n")
                self._stream.close();self._stream=None
            if self._stream_path:
                if os.path.abspath(path)!=self._stream_path:shutil.copyfile(self._stream_path,path)
                return
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path,"w",encoding="utf-8") as stream:
                stream.write(tr("log_file_title")+"\n")
                if self.total_entries>len(self.entries):stream.write("Recent log window only; enable start_stream for a complete persistent log.\n")
                for entry in self.entries:self._write_entry(stream,entry)
                stream.write("="*60+"\n"+self.summary()+"\n")
