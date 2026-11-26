#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Desktop window for render.py: pick a draft, a time range and a file, export."""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

FROZEN = getattr(sys, "frozen", False)
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
RENDER_PY = os.path.join(APP_DIR, "render.py")
RENDER_EXE = os.path.join(APP_DIR, "render.exe")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
LOCAL = os.environ.get("LOCALAPPDATA", "")
SETTINGS_PATH = os.path.join(os.environ.get("APPDATA", APP_DIR), "CapCutExport", "settings.json")
FORMATS = ("MP4", "MOV", "MP3", "WAV", "FLAC", "AAC")
FOLLOW = "跟随草稿"


# ---------- helpers ----------

def parse_time(text):
    """'90', '90.5', '1:30', '1:02:03' -> seconds."""
    text = text.strip().replace("：", ":")
    if not text:
        raise ValueError("empty")
    parts = text.split(":")
    if len(parts) > 3 or not all(re.fullmatch(r"\d+(\.\d+)?", p) for p in parts):
        raise ValueError(text)
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def format_time(seconds):
    seconds = round(max(0.0, seconds) * 10) / 10
    whole = int(seconds)
    rest = seconds - whole
    hours, minutes, secs = whole // 3600, whole // 60 % 60, whole % 60
    text = f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
    if rest >= 0.05:
        text += f".{int(round(rest * 10)) % 10}"
    return text


def draft_roots():
    """Default draft folders of CapCut and 剪映, plus a custom one if set."""
    roots = []
    for app in ("CapCut", "JianyingPro"):
        user_data = os.path.join(LOCAL, app, "User Data")
        setting = os.path.join(user_data, "Config", "globalSetting")
        try:
            with open(setting, encoding="utf-8", errors="replace") as file:
                match = re.search(r"^currentCustomDraftPath=(.+)$", file.read(), re.M)
            if match:
                roots.append(match[1].strip().replace("\\\\", "\\"))
        except OSError:
            pass
        roots.append(os.path.join(user_data, "Projects", "com.lveditor.draft"))
    unique = []
    for root in roots:
        if os.path.isdir(root) and os.path.normcase(os.path.abspath(root)) not in map(os.path.normcase, unique):
            unique.append(os.path.abspath(root))
    return unique


def read_draft(folder):
    """(name, duration seconds, modified datetime) or None."""
    content = os.path.join(folder, "draft_content.json")
    if not os.path.isfile(content):
        return None
    name, duration, modified = os.path.basename(folder), None, None
    try:
        with open(os.path.join(folder, "draft_meta_info.json"), encoding="utf-8") as file:
            meta = json.load(file)
        if meta.get("draft_is_invisible") or meta.get("tm_draft_removed"):
            return None
        name = meta.get("draft_name") or name
        if meta.get("tm_duration"):
            duration = meta["tm_duration"] / 1_000_000
    except (OSError, ValueError):
        pass
    if duration is None:
        try:
            with open(content, encoding="utf-8") as file:
                duration = json.load(file).get("duration", 0) / 1_000_000
        except (OSError, ValueError):
            duration = 0.0
    modified = datetime.datetime.fromtimestamp(os.path.getmtime(content))
    return name, duration, modified


def list_drafts():
    drafts = []
    for root in draft_roots():
        for entry in os.listdir(root):
            folder = os.path.join(root, entry)
            info = read_draft(folder) if os.path.isdir(folder) else None
            if info:
                drafts.append((folder, *info))
    drafts.sort(key=lambda item: item[3], reverse=True)
    return drafts


def ffmpeg_found():
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    folders = [os.environ.get("CAPCUT_EXPORT_FFMPEG", ""), os.path.join(APP_DIR, "ffmpeg", "bin"),
               os.path.join(APP_DIR, "ffmpeg"), APP_DIR]
    return any(f and os.path.isfile(os.path.join(f, exe)) for f in folders) or bool(shutil.which("ffmpeg"))


def default_out_dir():
    for folder in (os.path.join(os.path.expanduser("~"), "Videos"),
                   os.path.join(os.path.expanduser("~"), "Desktop")):
        if os.path.isdir(folder):
            return folder
    return os.path.expanduser("~")


def safe_name(name):
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "导出"


def open_path(path):
    if os.path.exists(path):
        os.startfile(path)


# ---------- window ----------

class ExportApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("CapCut 草稿导出器")
        self.geometry("820x700")
        self.minsize(720, 600)
        try:
            ttk.Style(self).theme_use("vista")
        except tk.TclError:
            pass

        self.settings = self._load_settings()
        self.drafts = []
        self.by_iid = {}
        self.current = None          # (folder, name, duration, modified)
        self.process = None
        self.temp_dir = None
        self.cancelled = False
        self.warnings = []
        self.last_output = None

        self.range_mode = tk.StringVar(value="all")
        self.start_var = tk.StringVar(value="0:00")
        self.end_var = tk.StringVar()
        self.range_info = tk.StringVar()
        self.format_var = tk.StringVar(value=self.settings.get("format", "MP4"))
        self.out_var = tk.StringVar()
        self.codec_var = tk.StringVar(value=self.settings.get("codec", "自动"))
        self.resolution_var = tk.StringVar(value=self.settings.get("resolution", FOLLOW))
        self.fps_var = tk.StringVar(value=self.settings.get("fps", FOLLOW))
        self.bitrate_var = tk.StringVar()
        self.music_var = tk.StringVar()
        self.no_text_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="选择一个草稿，然后点「开始导出」。")
        self.show_log = tk.BooleanVar(value=False)

        self._build()
        for variable in (self.start_var, self.end_var, self.range_mode):
            variable.trace_add("write", lambda *_: self._update_range_info())
        self._on_format()
        self.refresh_drafts()
        if not ffmpeg_found():
            self.status_var.set("缺少 FFmpeg：请把 ffmpeg.exe 和 ffprobe.exe 放进程序目录的 ffmpeg 文件夹。")
            self.export_btn.configure(state=tk.DISABLED)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----- layout -----
    def _build(self):
        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(0, weight=1)

        # 1. draft
        box = ttk.LabelFrame(root, text=" ① 选择草稿 ", padding=8)
        box.grid(row=0, column=0, sticky="nsew")
        root.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(box, columns=("duration", "modified"), height=6, selectmode="browse")
        self.tree.heading("#0", text="草稿名称")
        self.tree.heading("duration", text="时长")
        self.tree.heading("modified", text="最后修改")
        self.tree.column("#0", width=380)
        self.tree.column("duration", width=90, anchor="center")
        self.tree.column("modified", width=150, anchor="center")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(box, orient=tk.VERTICAL, command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select())
        buttons = ttk.Frame(box)
        buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(buttons, text="刷新", command=self.refresh_drafts).pack(side=tk.LEFT)
        ttk.Button(buttons, text="打开其他草稿文件夹…", command=self._pick_draft).pack(side=tk.LEFT, padx=6)
        self.empty_hint = ttk.Label(buttons, foreground="#888")
        self.empty_hint.pack(side=tk.LEFT, padx=6)

        # 2. range
        box = ttk.LabelFrame(root, text=" ② 导出范围 ", padding=8)
        box.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.all_radio = ttk.Radiobutton(box, text="整个草稿", value="all", variable=self.range_mode)
        self.all_radio.grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(box, text="自定义：从", value="custom", variable=self.range_mode).grid(
            row=1, column=0, sticky="w", pady=(6, 0))
        self.start_entry = ttk.Entry(box, textvariable=self.start_var, width=9, justify="center")
        self.start_entry.grid(row=1, column=1, padx=4, pady=(6, 0))
        ttk.Label(box, text="到").grid(row=1, column=2, pady=(6, 0))
        self.end_entry = ttk.Entry(box, textvariable=self.end_var, width=9, justify="center")
        self.end_entry.grid(row=1, column=3, padx=4, pady=(6, 0))
        quick = ttk.Frame(box)
        quick.grid(row=1, column=4, sticky="w", padx=(12, 0), pady=(6, 0))
        for label, seconds in (("前 10 秒", 10), ("前 30 秒", 30), ("前 1 分钟", 60)):
            ttk.Button(quick, text=label, width=9,
                       command=lambda s=seconds: self._quick_range(s)).pack(side=tk.LEFT, padx=2)
        for entry in (self.start_entry, self.end_entry):
            entry.bind("<FocusIn>", lambda _e: self.range_mode.set("custom"))
        ttk.Label(box, text="时间可以写秒数（90）或 分:秒（1:30）", foreground="#888").grid(
            row=2, column=1, columnspan=4, sticky="w", padx=4, pady=(4, 0))
        self.range_label = ttk.Label(box, textvariable=self.range_info, foreground="#1a5fb4")
        self.range_label.grid(row=3, column=0, columnspan=5, sticky="w", pady=(6, 0))

        # 3. output
        box = ttk.LabelFrame(root, text=" ③ 保存为 ", padding=8)
        box.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        box.columnconfigure(1, weight=1)
        fmt = ttk.Combobox(box, textvariable=self.format_var, values=FORMATS, state="readonly", width=7)
        fmt.grid(row=0, column=0, padx=(0, 6))
        fmt.bind("<<ComboboxSelected>>", lambda _e: self._on_format())
        ttk.Entry(box, textvariable=self.out_var).grid(row=0, column=1, sticky="ew")
        ttk.Button(box, text="另存为…", command=self._pick_out).grid(row=0, column=2, padx=(6, 0))
        ttk.Label(box, text="MP3 / WAV / FLAC / AAC 只导出声音", foreground="#888").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))

        # advanced
        self.adv_open = tk.BooleanVar(value=False)
        adv_toggle = ttk.Checkbutton(root, text="高级设置（画质、帧率、编码、背景音乐）",
                                     variable=self.adv_open, command=self._toggle_advanced)
        adv_toggle.grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.adv = ttk.Frame(root, padding=(18, 4, 0, 0))
        self.video_fields = []
        for row, col, label, variable, values in (
            (0, 0, "分辨率", self.resolution_var, (FOLLOW, "480p", "720p", "1080p", "1440p", "2160p")),
            (0, 2, "帧率", self.fps_var, (FOLLOW, "24", "25", "30", "48", "50", "60")),
            (1, 0, "编码", self.codec_var, ("自动", "H.264", "H.265")),
            (1, 2, "码率 kbps", self.bitrate_var, None),
        ):
            ttk.Label(self.adv, text=label).grid(row=row, column=col, sticky="w", pady=3)
            field = (ttk.Combobox(self.adv, textvariable=variable, values=values, state="readonly", width=12)
                     if values else ttk.Entry(self.adv, textvariable=variable, width=14))
            field.grid(row=row, column=col + 1, sticky="w", padx=(6, 24), pady=3)
            self.video_fields.append((field, "readonly" if values else "normal"))
        ttk.Label(self.adv, text="码率留空 = 自动", foreground="#888").grid(row=1, column=4, sticky="w")
        ttk.Label(self.adv, text="加一首本地音乐").grid(row=2, column=0, sticky="w", pady=3)
        music = ttk.Frame(self.adv)
        music.grid(row=2, column=1, columnspan=4, sticky="ew", padx=(6, 0))
        ttk.Entry(music, textvariable=self.music_var, width=46).pack(side=tk.LEFT)
        ttk.Button(music, text="选择…", command=self._pick_music).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(self.adv, text="不渲染字幕", variable=self.no_text_var).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=3)

        # progress + actions
        bottom = ttk.Frame(root)
        bottom.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        bottom.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(bottom, maximum=100)
        self.progress.grid(row=0, column=0, columnspan=5, sticky="ew")
        ttk.Label(bottom, textvariable=self.status_var, wraplength=760).grid(
            row=1, column=0, columnspan=5, sticky="w", pady=(4, 6))
        ttk.Checkbutton(bottom, text="显示日志", variable=self.show_log,
                        command=self._toggle_log).grid(row=2, column=0, sticky="w")
        self.open_file_btn = ttk.Button(bottom, text="播放", command=lambda: open_path(self.last_output or ""),
                                        state=tk.DISABLED)
        self.open_file_btn.grid(row=2, column=1, padx=3)
        ttk.Button(bottom, text="打开文件夹", command=self._open_folder).grid(row=2, column=2, padx=3)
        self.cancel_btn = ttk.Button(bottom, text="取消", command=self._cancel, state=tk.DISABLED)
        self.cancel_btn.grid(row=2, column=3, padx=3)
        self.export_btn = ttk.Button(bottom, text="开始导出", command=self._start)
        self.export_btn.grid(row=2, column=4, padx=(3, 0))
        self.log = scrolledtext.ScrolledText(root, height=10, state=tk.DISABLED, font=("Consolas", 9))
        root.rowconfigure(6, weight=1)

    def _toggle_advanced(self):
        if self.adv_open.get():
            self.adv.grid(row=4, column=0, sticky="ew")
        else:
            self.adv.grid_remove()

    def _toggle_log(self):
        if self.show_log.get():
            self.log.grid(row=6, column=0, sticky="nsew", pady=(8, 0))
        else:
            self.log.grid_remove()

    # ----- drafts -----
    def refresh_drafts(self):
        selected = self.current[0] if self.current else None
        self.drafts = list_drafts()
        self.tree.delete(*self.tree.get_children())
        self.by_iid = {}
        for index, draft in enumerate(self.drafts):
            self._insert(str(index), draft, tk.END)
        self.empty_hint.configure(text="" if self.drafts else
                                  "没有找到 CapCut / 剪映草稿，请用「打开其他草稿文件夹」。")
        index = next((i for i, d in enumerate(self.drafts) if d[0] == selected), 0 if self.drafts else None)
        if index is not None:
            self.tree.selection_set(str(index))
            self.tree.see(str(index))

    def _on_select(self):
        selection = self.tree.selection()
        if selection and selection[0] in self.by_iid:
            self._use_draft(self.by_iid[selection[0]])

    def _insert(self, iid, draft, position):
        _folder, name, duration, modified = draft
        self.by_iid[iid] = draft
        self.tree.insert("", position, iid=iid, text=name,
                         values=(format_time(duration), modified.strftime("%Y-%m-%d %H:%M")))

    def _pick_draft(self):
        folder = filedialog.askdirectory(title="选择草稿文件夹（里面有 draft_content.json）")
        if not folder:
            return
        info = read_draft(folder)
        if not info:
            messagebox.showerror("不是草稿文件夹", "这个文件夹里没有 draft_content.json。")
            return
        draft = (os.path.abspath(folder), *info)
        self.drafts.insert(0, draft)
        iid = f"picked{len(self.by_iid)}"
        self._insert(iid, draft, 0)
        self.tree.selection_set(iid)
        self.tree.see(iid)

    def _use_draft(self, draft):
        self.current = draft
        self.all_radio.configure(text=f"整个草稿（{format_time(draft[2])}）")
        self.end_var.set(format_time(draft[2]))
        self.start_var.set("0:00")
        self.range_mode.set("all")
        self._suggest_output()
        self._update_range_info()

    # ----- range -----
    def _quick_range(self, seconds):
        if not self.current:
            return
        self.range_mode.set("custom")
        self.start_var.set("0:00")
        self.end_var.set(format_time(min(seconds, self.current[2])))

    def _range(self):
        """(start, end) in seconds, or raise ValueError with a message."""
        if not self.current:
            raise ValueError("请先选择一个草稿。")
        total = self.current[2]
        if self.range_mode.get() == "all":
            return 0.0, total
        try:
            start = parse_time(self.start_var.get() or "0")
        except ValueError:
            raise ValueError("起点时间看不懂，请写成 90 或 1:30。")
        try:
            end = parse_time(self.end_var.get())
        except ValueError:
            raise ValueError("终点时间看不懂，请写成 90 或 1:30。")
        if end > total + 0.1:  # the shown length is rounded to 0.1 s
            raise ValueError(f"终点超过了草稿时长 {format_time(total)}。")
        if start >= end:
            raise ValueError("起点要早于终点。")
        return start, min(end, total)

    def _update_range_info(self):
        try:
            start, end = self._range()
            self.range_info.set(f"将导出 {format_time(start)} – {format_time(end)}，共 {format_time(end - start)}")
            self.range_label.configure(foreground="#1a5fb4")
        except ValueError as error:
            self.range_info.set(str(error))
            self.range_label.configure(foreground="#c01c28")

    # ----- output -----
    def _extension(self):
        return "." + self.format_var.get().lower()

    def _suggest_output(self):
        if not self.current:
            return
        folder = self.settings.get("out_dir") or default_out_dir()
        if not os.path.isdir(folder):
            folder = default_out_dir()
        self.out_var.set(os.path.join(folder, safe_name(self.current[1]) + self._extension()))

    def _on_format(self):
        out = self.out_var.get().strip()
        if out:
            self.out_var.set(os.path.splitext(out)[0] + self._extension())
        video = self.format_var.get() in ("MP4", "MOV")
        for field, state in self.video_fields:
            field.configure(state=state if video else tk.DISABLED)

    def _pick_out(self):
        label, extension = self.format_var.get(), self._extension()
        current = self.out_var.get().strip()
        path = filedialog.asksaveasfilename(
            title="保存导出文件", defaultextension=extension, filetypes=[(label, "*" + extension)],
            initialdir=os.path.dirname(current) if current else default_out_dir(),
            initialfile=os.path.basename(current) if current else "")
        if path:
            self.out_var.set(os.path.normpath(path))

    def _pick_music(self):
        path = filedialog.askopenfilename(
            title="选择背景音乐", filetypes=[("音频", "*.mp3 *.wav *.m4a *.aac *.flac"), ("所有文件", "*.*")])
        if path:
            self.music_var.set(os.path.normpath(path))

    def _open_folder(self):
        target = self.last_output or self.out_var.get().strip()
        if target and os.path.isfile(target):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(target)])
        elif target:
            open_path(os.path.dirname(target))

    # ----- export -----
    def _command(self, out, start, end):
        if FROZEN and os.path.isfile(RENDER_EXE):
            cmd = [RENDER_EXE]
        else:
            cmd = [sys.executable, "-X", "utf8", "-u", RENDER_PY]
        cmd += [self.current[0], "-o", out]
        if self.range_mode.get() == "custom":
            cmd += ["--start", f"{start:.3f}", "--end", f"{end:.6f}"]
        if self.format_var.get() in ("MP4", "MOV"):
            if self.resolution_var.get() != FOLLOW:
                cmd += ["--resolution", self.resolution_var.get().removesuffix("p")]
            if self.fps_var.get() != FOLLOW:
                cmd += ["--fps", self.fps_var.get()]
            if self.codec_var.get() != "自动":
                cmd += ["--codec", "hevc" if self.codec_var.get() == "H.265" else "h264"]
            if self.bitrate_var.get().strip():
                cmd += ["--bitrate", self.bitrate_var.get().strip()]
        if self.music_var.get().strip():
            cmd += ["--music", self.music_var.get().strip()]
        if self.no_text_var.get():
            cmd.append("--no-text")
        return cmd

    def _start(self):
        try:
            start, end = self._range()
        except ValueError as error:
            messagebox.showerror("导出范围有误", str(error))
            return
        out = self.out_var.get().strip()
        if not out:
            messagebox.showerror("缺少保存位置", "请先选择保存位置。")
            return
        if os.path.splitext(out)[1].lower() != self._extension():
            out = os.path.splitext(out)[0] + self._extension()
            self.out_var.set(out)
        out = os.path.abspath(out)
        bitrate = self.bitrate_var.get().strip()
        if bitrate and not bitrate.isdigit():
            messagebox.showerror("码率有误", "码率请填正整数（单位 kbps），或者留空。")
            return
        if not os.path.isdir(os.path.dirname(out)):
            messagebox.showerror("文件夹不存在", f"找不到保存文件夹：\n{os.path.dirname(out)}")
            return
        if os.path.exists(out) and not messagebox.askyesno("文件已存在", f"{os.path.basename(out)} 已存在，要覆盖吗？"):
            return

        self.settings.update(out_dir=os.path.dirname(out), format=self.format_var.get(),
                             codec=self.codec_var.get(), resolution=self.resolution_var.get(),
                             fps=self.fps_var.get())
        self._save_settings()
        self.cancelled = False
        self.warnings = []
        self.temp_dir = None
        self.last_output = None
        self.open_file_btn.configure(state=tk.DISABLED)
        self.export_btn.configure(state=tk.DISABLED)
        self.cancel_btn.configure(state=tk.NORMAL)
        self.progress["value"] = 0
        self.status_var.set(f"正在导出 {os.path.basename(out)} …（{format_time(end - start)}）")
        self._log(f"\n=== {datetime.datetime.now():%H:%M:%S} 开始导出 {out} ===\n")
        cmd = self._command(out, start, end)
        threading.Thread(target=self._worker, args=(cmd, out), daemon=True).start()

    def _worker(self, cmd, out):
        errors = []
        try:
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", creationflags=NO_WINDOW, env=env)
            for line in self.process.stdout:
                text = line.rstrip()
                if text.startswith("[progress]"):
                    value = int(text.split()[1])
                    self.after(0, lambda v=value: self.progress.configure(value=v))
                    continue
                if text.startswith("[temp]"):
                    self.temp_dir = text[6:].strip()
                elif text.startswith("[warn]"):
                    self.warnings.append(text[6:].strip())
                elif text.startswith("[fatal]") or "error:" in text:
                    errors.append(text.split("error:", 1)[-1].replace("[fatal]", "").strip())
                self.after(0, self._log, line)
            code = self.process.wait()
        except Exception as exc:  # noqa: BLE001 - shown to the user
            code, errors = -1, [str(exc)]
        self.after(0, self._finished, code, out, errors)

    def _finished(self, code, out, errors):
        self.process = None
        self.export_btn.configure(state=tk.NORMAL)
        self.cancel_btn.configure(state=tk.DISABLED)
        if self.cancelled:
            for leftover in (out, self.temp_dir):
                if leftover and os.path.isfile(leftover):
                    os.remove(leftover)
                elif leftover and os.path.isdir(leftover):
                    shutil.rmtree(leftover, ignore_errors=True)
            self.progress["value"] = 0
            self.status_var.set("已取消导出。")
            return
        if code == 0 and os.path.isfile(out):
            self.progress["value"] = 100
            self.last_output = out
            self.open_file_btn.configure(state=tk.NORMAL)
            note = ""
            if self.warnings:
                note = f"  有 {len(self.warnings)} 条提示（部分效果只做了近似或被跳过），勾选「显示日志」可查看。"
            self.status_var.set(f"导出完成：{out}{note}")
            self._log("[完成]\n")
        else:
            reason = errors[-1] if errors else f"退出码 {code}"
            self.status_var.set(f"导出失败：{reason}")
            self.show_log.set(True)
            self._toggle_log()
            messagebox.showerror("导出失败", f"{reason}\n\n详细信息见下方日志。")

    def _cancel(self):
        if self.process and self.process.poll() is None:
            self.cancelled = True
            self.status_var.set("正在取消…")
            # Stop render and the FFmpeg it started.
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.process.pid)],
                           capture_output=True, creationflags=NO_WINDOW)

    def _on_close(self):
        if self.process and self.process.poll() is None:
            if not messagebox.askyesno("正在导出", "导出还没完成，要取消并退出吗？"):
                return
            self._cancel()
            if self.temp_dir:
                shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.destroy()

    # ----- misc -----
    def _log(self, text):
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, text)
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _load_settings(self):
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as file:
                return json.load(file)
        except (OSError, ValueError):
            return {}

    def _save_settings(self):
        try:
            os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
            with open(SETTINGS_PATH, "w", encoding="utf-8") as file:
                json.dump(self.settings, file, ensure_ascii=False, indent=1)
        except OSError:
            pass


if __name__ == "__main__":
    ExportApp().mainloop()
