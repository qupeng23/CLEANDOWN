#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLEANDOWN 1.2.0 - one-click cache cleaner with optional auto shutdown.

Reconstructed from the shipped 使用说明.txt. Standard library only.
Run with: python cleandown.py
"""

from __future__ import annotations

import configparser
import ctypes
import datetime as dt
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

APP_NAME = "CLEANDOWN"
VERSION = "1.2.0"

APPDATA = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", str(APPDATA.parent / "Local")))
CONFIG_DIR = APPDATA / APP_NAME
CONFIG_PATH = CONFIG_DIR / f"{APP_NAME}.ini"
LOG_DIR = CONFIG_DIR / "logs"

DEFAULTS = {
    "shutdown_enabled": "1",
    "shutdown_seconds": "10",
    "dry_run": "0",
    "force_shutdown": "0",
    "clean_gpu_cache": "0",
    "clean_dev_cache": "0",
}


@dataclass
class Settings:
    shutdown_enabled: bool
    shutdown_seconds: int
    dry_run: bool
    force_shutdown: bool
    clean_gpu_cache: bool
    clean_dev_cache: bool


@dataclass
class Stats:
    files: int = 0
    dirs: int = 0
    bytes_: int = 0


def load_settings() -> Settings:
    parser = configparser.ConfigParser()
    parser[APP_NAME] = dict(DEFAULTS)
    try:
        if CONFIG_PATH.exists():
            parser.read(CONFIG_PATH, encoding="utf-8")
    except (OSError, configparser.Error):
        pass

    section = parser[APP_NAME] if parser.has_section(APP_NAME) else parser[configparser.DEFAULTSECT]

    def flag(name: str) -> bool:
        try:
            return section.getboolean(name, fallback=DEFAULTS[name] == "1")
        except ValueError:
            return DEFAULTS[name] == "1"

    def number(name: str) -> int:
        try:
            value = section.getint(name, fallback=int(DEFAULTS[name]))
            return max(0, value)
        except ValueError:
            return int(DEFAULTS[name])

    return Settings(
        shutdown_enabled=flag("shutdown_enabled"),
        shutdown_seconds=number("shutdown_seconds"),
        dry_run=flag("dry_run"),
        force_shutdown=flag("force_shutdown"),
        clean_gpu_cache=flag("clean_gpu_cache"),
        clean_dev_cache=flag("clean_dev_cache"),
    )


class Logger:
    def __init__(self) -> None:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            self.path: Path | None = LOG_DIR / f"{dt.date.today():%Y%m%d}.log"
        except OSError:
            self.path = None

    def log(self, message: str, emit=print) -> None:
        line = f"[{dt.datetime.now():%H:%M:%S}] {message}"
        emit(line)
        if self.path is not None:
            try:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError:
                pass


def _is_symlink(path: Path) -> bool:
    try:
        return path.is_symlink()
    except OSError:
        return True


def _safe_stat_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def purge_target(path: Path, logger: Logger, dry_run: bool, stats: Stats, label: str) -> None:
    """Delete a file or a whole subtree, but never follow symlinks/junctions."""
    if not path.exists() or _is_symlink(path):
        return

    if path.is_file():
        size = _safe_stat_size(path)
        if not dry_run:
            try:
                path.unlink()
            except OSError as exc:
                logger.log(f"  跳过（占用/权限）{path}: {exc}")
                return
        stats.files += 1
        stats.bytes_ += size
        logger.log(f"  {label}: 文件 {path} ({size / 1048576:.2f} MB)")
        return

    for child in list(path.iterdir()):
        try:
            purge_target(child, logger, dry_run, stats, label)
        except OSError as exc:
            logger.log(f"  跳过 {child}: {exc}")

    if not dry_run:
        try:
            path.rmdir()
            stats.dirs += 1
        except OSError:
            pass


def clean_directory_contents(directory: Path, logger: Logger, dry_run: bool, stats: Stats, label: str) -> None:
    if not directory.exists() or _is_symlink(directory):
        return
    logger.log(f"清理 {label}: {directory}")
    for child in list(directory.iterdir()):
        purge_target(child, logger, dry_run, stats, label)


def browser_cache_dirs() -> Iterable[tuple[Path, str]]:
    roots = (
        (LOCALAPPDATA / "Microsoft" / "Edge" / "User Data", "Edge"),
        (LOCALAPPDATA / "Google" / "Chrome" / "User Data", "Chrome"),
        (LOCALAPPDATA / "Quark" / "User Data", "夸克"),
        (LOCALAPPDATA / "Doubao" / "User Data", "豆包"),
        (LOCALAPPDATA / "Chromium" / "User Data", "Chromium"),
    )
    for root, name in roots:
        if not root.exists():
            continue
        for profile in root.iterdir():
            try:
                if not profile.is_dir() or _is_symlink(profile):
                    continue
                if profile.name != "Default" and not profile.name.startswith("Profile "):
                    continue
                for sub in ("Cache", "Code Cache", "GPUCache"):
                    target = profile / sub
                    if target.exists():
                        yield target, f"{name} {sub}"
            except OSError:
                continue


def cleanup(settings: Settings, logger: Logger, emit=print) -> Stats:
    stats = Stats()
    dry = settings.dry_run
    logger.log("=== 开始扫描 ===" if not dry else "=== 试运行（不删除、不关机）===", emit)

    for target, label in browser_cache_dirs():
        clean_directory_contents(target, logger, dry, stats, label)

    generic = (
        (Path(os.environ.get("TEMP", str(LOCALAPPDATA / "Temp"))), "系统临时文件"),
        (Path("C:/Windows/Temp"), "Windows 临时文件"),
        (LOCALAPPDATA / "CrashDumps", "崩溃转储"),
        (LOCALAPPDATA / "Microsoft" / "Windows" / "WER" / "ReportQueue", "WER 报告队列"),
        (LOCALAPPDATA / "Microsoft" / "Windows" / "WER" / "ReportArchive", "WER 报告存档"),
        (Path("C:/Windows/SoftwareDistribution/Download"), "Windows 更新下载缓存"),
        (
            Path("C:/Windows/ServiceProfiles/NetworkService/AppData/Local/Microsoft/Windows/DeliveryOptimization/Cache"),
            "传递优化缓存",
        ),
    )
    for target, label in generic:
        clean_directory_contents(target, logger, dry, stats, label)

    if settings.clean_gpu_cache:
        for target, label in (
            (LOCALAPPDATA / "NVIDIA" / "DXCache", "NVIDIA DXCache"),
            (LOCALAPPDATA / "D3DSCache", "D3DSCache"),
            (LOCALAPPDATA / "AMD" / "DxCache", "AMD DxCache"),
        ):
            clean_directory_contents(target, logger, dry, stats, label)
    else:
        logger.log("已跳过：显卡着色器缓存（clean_gpu_cache=0）", emit)

    if settings.clean_dev_cache:
        for target, label in (
            (LOCALAPPDATA / "pip" / "Cache", "pip 缓存"),
            (Path.home() / ".cache" / "pip", "pip 缓存(home)"),
            (APPDATA / "uv" / "cache", "uv 缓存"),
        ):
            clean_directory_contents(target, logger, dry, stats, label)
    else:
        logger.log("已跳过：开发包缓存（clean_dev_cache=0）", emit)

    scan_invalid_uninstall_entries(logger, emit)

    if not dry:
        try:
            # SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
            result = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x7)
            logger.log(f"回收站清理返回码: {result}", emit)
        except (AttributeError, OSError) as exc:
            logger.log(f"回收站清理跳过: {exc}", emit)

    logger.log(
        f"=== 扫描完成：{stats.files} 个文件，{stats.dirs} 个空目录，"
        f"{stats.bytes_ / 1048576:.2f} MB ===",
        emit,
    )
    return stats


def scan_invalid_uninstall_entries(logger: Logger, emit=print) -> None:
    """Log broken uninstall entries. Never delete registry keys automatically."""
    if os.name != "nt":
        return
    try:
        import winreg
    except ImportError:
        return

    locations = (
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    for hive, path in locations:
        try:
            key = winreg.OpenKey(hive, path)
        except OSError:
            continue
        index = 0
        try:
            while True:
                try:
                    sub_name = winreg.EnumKey(key, index)
                    index += 1
                except OSError:
                    break
                try:
                    sub = winreg.OpenKey(key, sub_name)
                    display, _ = winreg.QueryValueEx(sub, "DisplayName")
                    candidate = ""
                    for value in ("InstallLocation", "UninstallString", "DisplayIcon"):
                        try:
                            raw, _ = winreg.QueryValueEx(sub, value)
                            if raw and isinstance(raw, str):
                                candidate = raw.strip('"').split(" -", 1)[0]
                                break
                        except OSError:
                            continue
                    winreg.CloseKey(sub)
                    if candidate and not Path(candidate).exists():
                        logger.log(f"无效卸载残留（仅记录，不删除）: {display} -> {candidate}", emit)
                except OSError:
                    continue
        finally:
            winreg.CloseKey(key)


def ensure_admin() -> bool:
    if os.name != "nt":
        return True
    try:
        if ctypes.windll.shell32.IsUserAnAdmin():
            return True
        args = " ".join(f'"{item}"' for item in sys.argv)
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, args, None, 1)
        return False
    except (AttributeError, OSError):
        return True


def issue_shutdown(seconds: int, force: bool, logger: Logger, emit=print) -> bool:
    command = ["shutdown", "/s", "/t", str(max(1, seconds))]
    if force:
        command.append("/f")
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        logger.log(f"关机命令: {' '.join(command)} (code={completed.returncode})", emit)
        return completed.returncode == 0
    except OSError as exc:
        logger.log(f"关机命令失败: {exc}", emit)
        return False


def abort_shutdown(logger: Logger, emit=print) -> None:
    try:
        completed = subprocess.run(["shutdown", "/a"], capture_output=True, text=True, check=False)
        logger.log(f"已执行 shutdown /a (code={completed.returncode})", emit)
    except OSError as exc:
        logger.log(f"取消关机失败: {exc}", emit)


class CleanDownApp:
    def __init__(self, root: tk.Tk, settings: Settings, logger: Logger) -> None:
        self.root = root
        self.settings = settings
        self.logger = logger
        self.remaining = settings.shutdown_seconds
        self.countdown_job: str | None = None
        self.shutdown_issued = False

        root.title(f"CLEANDOWN {VERSION}")
        root.geometry("560x420")
        root.resizable(False, False)

        tk.Label(root, text=f"CLEANDOWN {VERSION}", font=("Microsoft YaHei", 16, "bold")).pack(
            anchor="w", padx=16, pady=(14, 4)
        )
        tk.Label(
            root,
            text="一键清理缓存垃圾，可选清理后自动关机。",
            font=("Microsoft YaHei", 10),
        ).pack(anchor="w", padx=16)

        self.status = tk.StringVar(value="准备就绪")
        tk.Label(root, textvariable=self.status, font=("Microsoft YaHei", 11)).pack(anchor="w", padx=16, pady=8)

        frame = tk.Frame(root)
        frame.pack(fill="both", expand=True, padx=16, pady=4)
        self.output = tk.Text(frame, wrap="word", height=14)
        scroll = tk.Scrollbar(frame, command=self.output.yview)
        self.output.configure(yscrollcommand=scroll.set)
        self.output.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        buttons = tk.Frame(root)
        buttons.pack(fill="x", padx=16, pady=12)
        self.cancel_button = tk.Button(
            buttons, text="取消关机", state="disabled", command=self.cancel_shutdown, width=14
        )
        self.cancel_button.pack(side="right", padx=(8, 0))
        tk.Button(buttons, text="退出", command=root.destroy, width=10).pack(side="right")

        root.after(200, self.start)

    def emit(self, message: str) -> None:
        def update() -> None:
            self.output.insert("end", message + "\n")
            self.output.see("end")

        self.root.after(0, update)

    def start(self) -> None:
        threading.Thread(target=self.worker, daemon=True).start()

    def worker(self) -> None:
        self.status.set("正在清理…")
        cleanup(self.settings, self.logger, emit=self.emit)
        if self.settings.dry_run or not self.settings.shutdown_enabled:
            self.status.set("清理完成（不会关机）")
            return
        self.remaining = max(1, self.settings.shutdown_seconds)
        self.shutdown_issued = issue_shutdown(self.remaining, self.settings.force_shutdown, self.logger, self.emit)
        if not self.shutdown_issued:
            self.status.set("关机命令未能发出")
            return
        self.root.after(0, lambda: self.cancel_button.configure(state="normal"))
        self.tick()

    def tick(self) -> None:
        if self.remaining <= 0:
            self.status.set("即将关机")
            return
        self.status.set(f"清理完成，{self.remaining} 秒后关机（可点右侧取消）")
        self.remaining -= 1
        self.countdown_job = self.root.after(1000, self.tick)

    def cancel_shutdown(self) -> None:
        if self.countdown_job is not None:
            try:
                self.root.after_cancel(self.countdown_job)
            except tk.TclError:
                pass
        abort_shutdown(self.logger, self.emit)
        self.status.set("已取消关机")
        self.cancel_button.configure(state="disabled")


def main() -> int:
    if not ensure_admin():
        return 0
    settings = load_settings()
    logger = Logger()
    root = tk.Tk()
    CleanDownApp(root, settings, logger)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
