
"""
批量 7Z 压缩工具 v2.3 - Part 2: GUI 主程序
文件名: compress7z_gui.py  ← 运行此文件
"""
import os, sys, re, time, threading, queue, locale
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from concurrent.futures import ThreadPoolExecutor, as_completed

from compress7z_engine import (
    CompressEngine, COMPRESS_LEVELS, DEFAULT_COMPRESS_LEVEL_KEY,
    DEFAULT_EXCLUDES, SMART_PARALLEL_THRESHOLD_GB_DEFAULT,
    BYTES_PER_GB, DARK_COLORS, LIGHT_COLORS,
    get_application_path, get_runtime_dir,
    get_system_encoding, get_subprocess_flags,
    get_folder_size, format_size, format_eta,
    sort_key_by_name, match_folder_filter,
    is_windows_dark_mode,
    load_config, save_config,
    load_progress, save_progress, clear_progress,
)

WINDOW_TITLE = "批量 7Z 压缩工具 v2.3"
WINDOW_SIZE = "800x750"
MIN_WINDOW_SIZE = (720, 650)
QUEUE_POLL_MS = 80


# ============================================================
#  文件夹预览对话框 (FIX4: 滚轮仅 canvas 内生效)
# ============================================================
class FolderPreviewDialog:
    def __init__(self, parent, folders_with_size, colors, is_dark):
        self.result = None
        self.check_vars = {}
        c = colors

        self.dlg = tk.Toplevel(parent)
        self.dlg.title("待压缩文件夹预览")
        self.dlg.geometry("600x500")
        self.dlg.minsize(500, 350)
        self.dlg.transient(parent)
        self.dlg.grab_set()
        self.dlg.configure(bg=c["bg"])

        # 顶部信息
        top = ttk.Frame(self.dlg, padding="10")
        top.pack(fill=tk.X)
        total_sz = sum(s for _, s in folders_with_size)
        ttk.Label(top, text=(
            f"共 {len(folders_with_size)} 个文件夹，"
            f"总大小: {format_size(total_sz)}")
        ).pack(side=tk.LEFT)

        # 按钮行
        br = ttk.Frame(self.dlg, padding="10 0")
        br.pack(fill=tk.X)
        ttk.Button(br, text="全选",
                   command=self._sel_all).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(br, text="全不选",
                   command=self._sel_none).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(br, text="反选",
                   command=self._sel_inv).pack(side=tk.LEFT)

        # 列表区域
        lf = ttk.Frame(self.dlg, padding="10 5")
        lf.pack(fill=tk.BOTH, expand=True)
        self._canvas = tk.Canvas(lf, bg=c["tree_bg"],
                                 highlightthickness=0)
        sb = ttk.Scrollbar(lf, orient=tk.VERTICAL,
                           command=self._canvas.yview)
        inner = ttk.Frame(self._canvas)
        inner.bind("<Configure>", lambda e:
            self._canvas.configure(
                scrollregion=self._canvas.bbox("all")))
        self._canvas.create_window((0, 0), window=inner, anchor="nw")
        self._canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # FIX4: 仅鼠标在 canvas 内时绑定滚轮
        self._canvas.bind("<Enter>", lambda e:
            self._canvas.bind("<MouseWheel>", self._mw))
        self._canvas.bind("<Leave>", lambda e:
            self._canvas.unbind("<MouseWheel>"))

        # 表头
        hdr = ttk.Frame(inner)
        hdr.pack(fill=tk.X, padx=5, pady=(0, 5))
        ttk.Label(hdr, text="文件夹名称", width=40,
                  font=("", 9, "bold")).pack(side=tk.LEFT)
        ttk.Label(hdr, text="大小", width=15,
                  font=("", 9, "bold")).pack(side=tk.RIGHT)

        # 文件夹列表
        for name, size in folders_with_size:
            var = tk.BooleanVar(value=True)
            self.check_vars[name] = var
            row = ttk.Frame(inner)
            row.pack(fill=tk.X, padx=5, pady=1)
            ttk.Checkbutton(row, text=name, variable=var,
                            width=45).pack(side=tk.LEFT)
            ttk.Label(row, text=format_size(size), width=15,
                      anchor=tk.E, foreground="gray").pack(side=tk.RIGHT)

        # 底部
        bot = ttk.Frame(self.dlg, padding="10")
        bot.pack(fill=tk.X)
        self.lbl_sel = ttk.Label(bot, text="")
        self.lbl_sel.pack(side=tk.LEFT)
        self._upd()
        ttk.Button(bot, text="取消",
                   command=self._cancel).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(bot, text="✅ 确认开始",
                   command=self._confirm).pack(side=tk.RIGHT)

        for v in self.check_vars.values():
            v.trace_add("write", lambda *_: self._upd())
        self.dlg.protocol("WM_DELETE_WINDOW", self._cancel)

    def _mw(self, e):
        self._canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

    def _upd(self):
        n = sum(1 for v in self.check_vars.values() if v.get())
        self.lbl_sel.config(text=f"已选: {n}/{len(self.check_vars)}")

    def _sel_all(self):
        for v in self.check_vars.values(): v.set(True)

    def _sel_none(self):
        for v in self.check_vars.values(): v.set(False)

    def _sel_inv(self):
        for v in self.check_vars.values(): v.set(not v.get())

    def _confirm(self):
        sel = [n for n, v in self.check_vars.items() if v.get()]
        if not sel:
            messagebox.showwarning("提示", "请至少选择一个文件夹！",
                                   parent=self.dlg)
            return
        self.result = sel
        self.dlg.destroy()

    def _cancel(self):
        self.result = None
        self.dlg.destroy()

    def show(self):
        self.dlg.wait_window()
        return self.result


# ============================================================
#  GUI 主界面
# ============================================================
class CompressApp7Z:

    def __init__(self, root):
        self.root = root
        self.root.title(WINDOW_TITLE)
        self.root.geometry(WINDOW_SIZE)
        self.root.minsize(*MIN_WINDOW_SIZE)

        self.msg_queue = queue.Queue()
        self.is_running = False
        self.config = load_config()

        # FIX2: 每个文件夹独立 skip event
        self._skip_events = {}
        self._skip_lock = threading.Lock()
        self._current_item = ""
        self._pause_event = threading.Event()

        self.console_encoding = get_system_encoding()
        self.sp_flags = get_subprocess_flags()
        self.seven_zip_path = CompressEngine.find_7z()
        self.engine = CompressEngine(
            self.seven_zip_path,
            self.console_encoding, self.sp_flags)
        self.cpu_count = os.cpu_count() or 4

        try:
            locale.setlocale(locale.LC_COLLATE, '')
        except Exception:
            pass

        dm = self.config.get("dark_mode", "auto")
        if dm == "auto":
            self.is_dark = is_windows_dark_mode()
        elif dm == "dark":
            self.is_dark = True
        else:
            self.is_dark = False
        self.colors = DARK_COLORS if self.is_dark else LIGHT_COLORS

        self._setup_theme()
        self._setup_ui()
        self._load_cfg()
        self._setup_dnd()
        self._restore_pos()
        self._check_recovery()
        self._poll()

    # -------------------- 主题 --------------------
    def _setup_theme(self):
        c = self.colors
        self.root.configure(bg=c["bg"])
        s = ttk.Style()
        s.theme_use('clam')
        s.configure(".", background=c["bg"], foreground=c["fg"],
                    fieldbackground=c["entry_bg"],
                    insertcolor=c["fg"], borderwidth=1)
        for w in ["TFrame", "TLabel", "TLabelframe",
                   "TLabelframe.Label"]:
            s.configure(w, background=c["bg"], foreground=c["fg"])
        s.configure("TEntry", fieldbackground=c["entry_bg"],
                    foreground=c["entry_fg"])
        s.configure("TButton", background=c["btn_bg"],
                    foreground=c["fg"])
        s.map("TButton", background=[('active', c["select_bg"])])
        s.configure("TCheckbutton", background=c["bg"],
                    foreground=c["fg"])
        s.map("TCheckbutton", background=[('active', c["bg"])])
        s.configure("TCombobox", fieldbackground=c["entry_bg"],
                    foreground=c["entry_fg"])
        s.configure("TSpinbox", fieldbackground=c["entry_bg"],
                    foreground=c["entry_fg"])
        tc = "#333333" if self.is_dark else "#e0e0e0"
        pc = "#0078d4" if self.is_dark else "#06b025"
        s.configure("green.Horizontal.TProgressbar",
                    troughcolor=tc, background=pc)

    # -------------------- UI --------------------
    def _setup_ui(self):
        c = self.colors
        main = ttk.Frame(self.root, padding="12")
        main.pack(fill=tk.BOTH, expand=True)

        # 引擎状态
        ef = ttk.LabelFrame(main, text="引擎状态", padding="5")
        ef.pack(fill=tk.X, pady=(0, 6))
        if self.seven_zip_path:
            if getattr(sys, 'frozen', False) and \
                    os.path.dirname(self.seven_zip_path) == get_runtime_dir():
                txt = "✅ 已加载内嵌 7-Zip 引擎"
            else:
                txt = f"✅ 已找到 7-Zip: {self.seven_zip_path}"
            ttk.Label(ef, text=txt, foreground="#4ec94e").pack(anchor=tk.W)
        else:
            ttk.Label(ef, foreground="#ff6b6b",
                      text="❌ 未找到 7-Zip 引擎！请安装或放 7z.exe 到同目录"
                      ).pack(anchor=tk.W)

        # 目录设置
        pf = ttk.LabelFrame(main, text="目录设置", padding="8")
        pf.pack(fill=tk.X, pady=(0, 6))
        pf.columnconfigure(1, weight=1)

        ttk.Label(pf, text="总文件夹:").grid(row=0, column=0, sticky=tk.W)
        self.entry_src = ttk.Entry(pf)
        self.entry_src.grid(row=0, column=1, sticky=tk.EW, padx=5)
        ttk.Button(pf, text="浏览…", width=7,
                   command=self._browse_src).grid(row=0, column=2)

        ttk.Label(pf, text="输出目录:").grid(
            row=1, column=0, sticky=tk.W, pady=5)
        self.entry_out = ttk.Entry(pf)
        self.entry_out.grid(row=1, column=1, sticky=tk.EW, padx=5, pady=5)
        self.btn_bout = ttk.Button(pf, text="浏览…", width=7,
                                   command=self._browse_out)
        self.btn_bout.grid(row=1, column=2, pady=5)

        self.var_osame = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            pf, text="输出到总文件夹（取消勾选可自定义）",
            variable=self.var_osame,
            command=self._tog_out
        ).grid(row=2, column=1, sticky=tk.W, padx=5)
        self.entry_out.config(state=tk.DISABLED)
        self.btn_bout.config(state=tk.DISABLED)

        # 功能选项
        of = ttk.LabelFrame(main, text="功能选项", padding="8")
        of.pack(fill=tk.X, pady=(0, 6))

        r0 = ttk.Frame(of); r0.pack(fill=tk.X)
        self.var_pwd = tk.BooleanVar()
        self.var_del = tk.BooleanVar()
        self.var_skip = tk.BooleanVar(value=True)
        self.var_enh = tk.BooleanVar()
        ttk.Checkbutton(r0, text="加密压缩包",
                        variable=self.var_pwd).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Checkbutton(r0, text="压缩后删除源文件夹",
                        variable=self.var_del).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Checkbutton(r0, text="跳过已存在",
                        variable=self.var_skip).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Checkbutton(r0, text="增强校验",
                        variable=self.var_enh).pack(side=tk.LEFT)

        r1 = ttk.Frame(of); r1.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(r1, text="压缩级别:").pack(side=tk.LEFT)
        self.cmb_lv = ttk.Combobox(
            r1, values=list(COMPRESS_LEVELS.keys()),
            state="readonly", width=22)
        self.cmb_lv.set(DEFAULT_COMPRESS_LEVEL_KEY)
        self.cmb_lv.pack(side=tk.LEFT, padx=(5, 20))
        ttk.Label(r1, text="并行数:").pack(side=tk.LEFT)
        self.spn_wk = ttk.Spinbox(r1, from_=1, to=self.cpu_count,
                                   width=4, state="readonly")
        self.spn_wk.set(1)
        self.spn_wk.pack(side=tk.LEFT, padx=5)
        ttk.Label(r1, text=f"(最大{self.cpu_count})",
                  foreground="gray").pack(side=tk.LEFT)

        r2 = ttk.Frame(of); r2.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(r2, text="排除规则:").pack(side=tk.LEFT)
        self.entry_exc = ttk.Entry(r2)
        self.entry_exc.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Label(r2, text="分号分隔", foreground="gray").pack(side=tk.LEFT)

        r3 = ttk.Frame(of); r3.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(r3, text="文件夹过滤:").pack(side=tk.LEFT)
        self.entry_flt = ttk.Entry(r3)
        self.entry_flt.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Label(r3, text="通配符;分隔(留空=全部)",
                  foreground="gray").pack(side=tk.LEFT)

        # FIX7: 阈值上限 9999
        r4 = ttk.Frame(of); r4.pack(fill=tk.X, pady=(6, 0))
        self.var_smart = tk.BooleanVar()
        ttk.Checkbutton(r4, text="智能并行",
                        variable=self.var_smart).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(r4, text="大文件夹阈值:").pack(side=tk.LEFT)
        self.spn_thr = ttk.Spinbox(r4, from_=1, to=9999,
                                    width=5, state="readonly")
        self.spn_thr.set(SMART_PARALLEL_THRESHOLD_GB_DEFAULT)
        self.spn_thr.pack(side=tk.LEFT, padx=5)
        ttk.Label(r4, text="GB").pack(side=tk.LEFT)
        ttk.Label(r4, text="(小文件夹并行,大文件夹串行)",
                  foreground="gray").pack(side=tk.LEFT, padx=10)

        # 进度
        pgf = ttk.LabelFrame(main, text="压缩进度", padding="8")
        pgf.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        tr = ttk.Frame(pgf); tr.pack(fill=tk.X)
        self.lbl_st = ttk.Label(tr, text="状态: 等待开始…")
        self.lbl_st.pack(side=tk.LEFT)
        self.lbl_eta = ttk.Label(tr, text="", foreground="gray")
        self.lbl_eta.pack(side=tk.RIGHT, padx=(0, 10))
        self.lbl_tp = ttk.Label(tr, text="0%", width=6, anchor=tk.E,
                                font=("Consolas", 10, "bold"))
        self.lbl_tp.pack(side=tk.RIGHT)

        self.prog_t = ttk.Progressbar(
            pgf, orient=tk.HORIZONTAL, mode='determinate',
            style="green.Horizontal.TProgressbar")
        self.prog_t.pack(fill=tk.X, pady=(4, 0))

        fr = ttk.Frame(pgf); fr.pack(fill=tk.X, pady=(8, 0))
        self.lbl_fn = ttk.Label(fr, text="当前文件: -")
        self.lbl_fn.pack(side=tk.LEFT)
        self.lbl_fp = ttk.Label(fr, text="0%", width=6, anchor=tk.E,
                                font=("Consolas", 10, "bold"))
        self.lbl_fp.pack(side=tk.RIGHT)

        self.prog_f = ttk.Progressbar(
            pgf, orient=tk.HORIZONTAL, mode='determinate',
            style="green.Horizontal.TProgressbar")
        self.prog_f.pack(fill=tk.X, pady=(2, 0))

        # 日志
        log_frm = ttk.Frame(pgf)
        log_frm.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.txt_log = tk.Text(
            log_frm, height=8, state=tk.DISABLED,
            wrap=tk.WORD, font=("Consolas", 9),
            bg=c["log_bg"], fg=c["log_fg"],
            insertbackground=c["log_fg"],
            selectbackground=c["select_bg"])
        sb = ttk.Scrollbar(log_frm, command=self.txt_log.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.txt_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.txt_log.config(yscrollcommand=sb.set)
        self.txt_log.tag_config("SUCCESS", foreground="#4ec94e")
        self.txt_log.tag_config("WARNING", foreground="#e8a838")
        self.txt_log.tag_config("ERROR", foreground="#ff6b6b")
        self.txt_log.tag_config("INFO", foreground=c["log_fg"])

        self._lmenu = tk.Menu(self.txt_log, tearoff=0,
                              bg=c["bg"], fg=c["fg"])
        self._lmenu.add_command(label="复制全部", command=self._copy_log)
        self._lmenu.add_command(label="清空日志", command=self._clear_log)
        self.txt_log.bind("<Button-3>",
                          lambda e: self._lmenu.tk_popup(e.x_root, e.y_root))

        # 按钮
        bf = ttk.Frame(main); bf.pack(fill=tk.X)
        self.btn_go = ttk.Button(bf, text="🚀 开始压缩",
                                 command=self._start)
        self.btn_go.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_pa = ttk.Button(bf, text="⏸ 暂停",
                                 command=self._toggle_pause,
                                 state=tk.DISABLED)
        self.btn_pa.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_sk = ttk.Button(bf, text="⏭ 跳过当前",
                                 command=self._skip_current,
                                 state=tk.DISABLED)
        self.btn_sk.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_sp = ttk.Button(bf, text="⏹ 停止",
                                 command=self._stop,
                                 state=tk.DISABLED)
        self.btn_sp.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(bf, text="📄 导出日志",
                   command=self._export_log).pack(side=tk.LEFT)

    # -------------------- 控件联动 --------------------
    def _tog_out(self):
        st = tk.DISABLED if self.var_osame.get() else tk.NORMAL
        self.entry_out.config(state=st)
        self.btn_bout.config(state=st)

    def _restore_pos(self):
        x = self.config.get("window_x")
        y = self.config.get("window_y")
        if x is not None and y is not None:
            self.root.geometry(f"+{x}+{y}")

    def _save_pos(self):
        try:
            m = re.search(r'\+(\d+)\+(\d+)', self.root.geometry())
            if m:
                self.config["window_x"] = int(m.group(1))
                self.config["window_y"] = int(m.group(2))
        except Exception:
            pass

    # -------------------- 崩溃恢复 (FIX8) --------------------
    def _check_recovery(self):
        prog = load_progress()
        if not prog:
            return
        src = prog.get("source_dir", "")
        comp = prog.get("completed", [])
        tot = prog.get("total", 0)
        if not src or not comp:
            clear_progress()
            return
        if messagebox.askyesno("崩溃恢复",
                f"检测到上次未完成的任务：\n\n"
                f"  总文件夹: {src}\n"
                f"  已完成: {len(comp)}/{tot}\n\n"
                f"是否从断点继续？"):
            self.entry_src.delete(0, tk.END)
            self.entry_src.insert(0, src)
            out = prog.get("output_dir", "")
            if out and out != src:
                self.var_osame.set(False)
                self.entry_out.config(state=tk.NORMAL)
                self.btn_bout.config(state=tk.NORMAL)
                self.entry_out.delete(0, tk.END)
                self.entry_out.insert(0, out)
            self._log(f"📂 从断点恢复，已跳过 {len(comp)} 个已完成的文件夹",
                      "INFO")
            self._recovery_done = set(comp)
        else:
            clear_progress()
            self._recovery_done = set()

    # -------------------- 拖拽 --------------------
    def _setup_dnd(self):
        try:
            from tkinterdnd2 import DND_FILES
            self.entry_src.drop_target_register(DND_FILES)
            self.entry_src.dnd_bind('<<Drop>>',
                                     lambda e: self._drop(self.entry_src, e))
            self.entry_out.drop_target_register(DND_FILES)
            self.entry_out.dnd_bind('<<Drop>>', self._drop_out)
        except Exception:
            pass

    def _drop(self, entry, event):
        p = event.data.strip('{}')
        if os.path.isdir(p):
            entry.delete(0, tk.END)
            entry.insert(0, p)

    def _drop_out(self, event):
        p = event.data.strip('{}')
        if os.path.isdir(p):
            self.entry_out.config(state=tk.NORMAL)
            self.entry_out.delete(0, tk.END)
            self.entry_out.insert(0, p)
            self.var_osame.set(False)
            self.btn_bout.config(state=tk.NORMAL)

    # -------------------- 配置 --------------------
    def _load_cfg(self):
        c = self.config
        self.entry_src.insert(0, c.get("source_dir", ""))
        if not c.get("output_same_as_source", True):
            self.var_osame.set(False)
            self.entry_out.config(state=tk.NORMAL)
            self.btn_bout.config(state=tk.NORMAL)
            self.entry_out.insert(0, c.get("output_dir", ""))
        self.var_pwd.set(c.get("use_password", False))
        self.var_del.set(c.get("delete_source", False))
        self.var_skip.set(c.get("skip_existing", True))
        self.var_enh.set(c.get("enhanced_verify", False))
        lk = c.get("compress_level", DEFAULT_COMPRESS_LEVEL_KEY)
        if lk in COMPRESS_LEVELS:
            self.cmb_lv.set(lk)
        self.entry_exc.insert(0, c.get("excludes", DEFAULT_EXCLUDES))
        self.entry_flt.insert(0, c.get("folder_filter", ""))
        self.spn_wk.set(min(c.get("max_workers", 1), self.cpu_count))
        self.var_smart.set(c.get("smart_parallel", False))
        self.spn_thr.set(c.get("smart_threshold_gb",
                               SMART_PARALLEL_THRESHOLD_GB_DEFAULT))
        self._recovery_done = set()

    def _save_cfg(self):
        self._save_pos()
        save_config({
            "source_dir": self.entry_src.get().strip(),
            "output_same_as_source": self.var_osame.get(),
            "output_dir": self.entry_out.get().strip(),
            "use_password": self.var_pwd.get(),
            "delete_source": self.var_del.get(),
            "skip_existing": self.var_skip.get(),
            "enhanced_verify": self.var_enh.get(),
            "compress_level": self.cmb_lv.get(),
            "excludes": self.entry_exc.get().strip(),
            "folder_filter": self.entry_flt.get().strip(),
            "max_workers": int(self.spn_wk.get()),
            "smart_parallel": self.var_smart.get(),
            "smart_threshold_gb": int(self.spn_thr.get()),
            "dark_mode": self.config.get("dark_mode", "auto"),
            "window_x": self.config.get("window_x"),
            "window_y": self.config.get("window_y"),
        })

    # -------------------- 日志 --------------------
    def _log(self, msg, level="INFO"):
        self.txt_log.config(state=tk.NORMAL)
        self.txt_log.insert(tk.END, msg + "\n", (level,))
        self.txt_log.see(tk.END)
        self.txt_log.config(state=tk.DISABLED)

    def _copy_log(self):
        t = self.txt_log.get("1.0", tk.END).strip()
        if t:
            self.root.clipboard_clear()
            self.root.clipboard_append(t)

    def _clear_log(self):
        self.txt_log.config(state=tk.NORMAL)
        self.txt_log.delete("1.0", tk.END)
        self.txt_log.config(state=tk.DISABLED)

    def _export_log(self):
        t = self.txt_log.get("1.0", tk.END).strip()
        if not t:
            messagebox.showinfo("提示", "日志为空。")
            return
        p = filedialog.asksaveasfilename(
            title="导出日志", defaultextension=".txt",
            filetypes=[("文本文件", "*.txt")])
        if p:
            try:
                with open(p, 'w', encoding='utf-8') as f:
                    f.write(t)
                messagebox.showinfo("成功", f"日志已导出至:\n{p}")
            except Exception as e:
                messagebox.showerror("错误", f"导出失败: {e}")

    # -------------------- 浏览 --------------------
    def _browse_src(self):
        p = filedialog.askdirectory(title="选择总文件夹")
        if p:
            self.entry_src.delete(0, tk.END)
            self.entry_src.insert(0, p)

    def _browse_out(self):
        if self.var_osame.get():
            return
        p = filedialog.askdirectory(title="选择输出目录")
        if p:
            self.entry_out.config(state=tk.NORMAL)
            self.entry_out.delete(0, tk.END)
            self.entry_out.insert(0, p)

    # -------------------- 消息轮询 --------------------
    def _poll(self):
        lp = None
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                t = msg[0]
                if t == "LOG":
                    lv = msg[2] if len(msg) > 2 else "INFO"
                    self._log(msg[1], lv)
                elif t == "STATUS":
                    self.lbl_st.config(text=f"状态: {msg[1]}")
                elif t == "ETA":
                    self.lbl_eta.config(text=f"预计剩余: {msg[1]}")
                elif t == "FILE_NAME":
                    self.lbl_fn.config(text=f"当前文件: {msg[1]}")
                    self.prog_f['value'] = 0
                    self.lbl_fp.config(text="0%")
                elif t == "PROG_TOTAL":
                    self.prog_t['value'] = msg[1]
                    self.lbl_tp.config(text=f"{int(msg[1])}%")
                elif t == "PROG_FILE":
                    lp = msg[1]
                elif t == "ASK_PWD":
                    pwd = simpledialog.askstring(
                        "设置密码", "请输入压缩包密码（取消则不加密）:",
                        show="*", parent=self.root)
                    msg[2].put(pwd)
                elif t == "DONE":
                    self._finished(msg[1] if len(msg) > 1 else None)
        except queue.Empty:
            pass
        if lp is not None:
            self.prog_f['value'] = lp
            self.lbl_fp.config(text=f"{int(lp)}%")
        self.root.after(QUEUE_POLL_MS, self._poll)

    # -------------------- 文件夹过滤 --------------------
    def _get_folders(self, src):
        ft = self.entry_flt.get().strip()
        pats = [p.strip() for p in ft.split(';')
                if p.strip()] if ft else []
        items = sorted(
            [i for i in os.listdir(src)
             if os.path.isdir(os.path.join(src, i))],
            key=sort_key_by_name)
        return [(i, get_folder_size(os.path.join(src, i)))
                for i in items if match_folder_filter(i, pats)]

    # ============================================================
    #  任务控制 (FIX1+FIX2+FIX3: 完整实现)
    # ============================================================
    def _start(self):
        if not self.seven_zip_path:
            messagebox.showerror("错误", "未找到 7-Zip 引擎！")
            return
        src = self.entry_src.get().strip()
        if not src or not os.path.isdir(src):
            messagebox.showerror("错误", "请选择有效的总文件夹路径！")
            return
        out = src if self.var_osame.get() else (
            self.entry_out.get().strip() or src)

        self._log("🔍 正在扫描文件夹…", "INFO")
        self.root.update()
        fws = self._get_folders(src)
        if not fws:
            messagebox.showinfo("提示", "未找到匹配的子文件夹。")
            return

        # 弹出预览
        dlg = FolderPreviewDialog(
            self.root, fws, self.colors, self.is_dark)
        sel = dlg.show()
        if sel is None:
            self._log("❌ 已取消", "WARNING")
            return

        sm = dict(fws)
        sel_ws = [(n, sm[n]) for n in sel if n in sm]
        self._save_cfg()

        self.is_running = True
        with self._skip_lock:
            self._skip_events.clear()
        self._pause_event.clear()
        self.btn_go.config(state=tk.DISABLED)
        self.btn_pa.config(state=tk.NORMAL)
        self.btn_sk.config(state=tk.NORMAL)
        self.btn_sp.config(state=tk.NORMAL)
        self._clear_log()
        self.prog_t['value'] = 0
        self.prog_f['value'] = 0
        self.lbl_tp.config(text="0%")
        self.lbl_fp.config(text="0%")
        self.lbl_eta.config(text="")

        lk = self.cmb_lv.get()
        cl = COMPRESS_LEVELS.get(lk, 5)
        excs = [x.strip() for x in self.entry_exc.get().split(';')
                if x.strip()]
        mw = int(self.spn_wk.get())
        smart = self.var_smart.get()
        thr = int(self.spn_thr.get()) * BYTES_PER_GB
        rec = getattr(self, '_recovery_done', set())

        threading.Thread(
            target=self._worker,
            args=(src, out, self.var_pwd.get(),
                  self.var_del.get(), self.var_skip.get(),
                  self.var_enh.get(), cl, excs, mw,
                  rec, sel_ws, smart, thr),
            daemon=True
        ).start()

    def _toggle_pause(self):
        if self._pause_event.is_set():
            self._pause_event.clear()
            self.btn_pa.config(text="⏸ 暂停")
            self._log("▶️ 已恢复", "INFO")
            self.lbl_st.config(text="状态: 运行中…")
        else:
            self._pause_event.set()
            self.btn_pa.config(text="▶ 恢复")
            self._log("⏸ 暂停中，当前完成后等待", "WARNING")
            self.lbl_st.config(text="状态: 暂停中…")

    # FIX2: 跳过只影响当前文件夹
    def _skip_current(self):
        if not self.is_running:
            return
        name = self._current_item
        with self._skip_lock:
            ev = self._skip_events.get(name)
            if ev:
                ev.set()
        self.engine.terminate_one(name)
        self._log(f"⏭️ 正在跳过: {name}…", "WARNING")

    def _stop(self):
        self.is_running = False
        self._pause_event.clear()
        with self._skip_lock:
            for ev in self._skip_events.values():
                ev.set()
        self._log("⚠️ 正在终止所有进程…", "WARNING")
        self.engine.terminate_all()
        self.btn_sp.config(state=tk.DISABLED)
        self.btn_sk.config(state=tk.DISABLED)
        self.btn_pa.config(state=tk.DISABLED, text="⏸ 暂停")

    def _finished(self, stats):
        self.is_running = False
        self.btn_go.config(state=tk.NORMAL)
        self.btn_sp.config(state=tk.DISABLED)
        self.btn_sk.config(state=tk.DISABLED)
        self.btn_pa.config(state=tk.DISABLED, text="⏸ 暂停")
        self.lbl_fn.config(text="当前文件: -")
        self.prog_f['value'] = 0
        self.lbl_fp.config(text="0%")
        self.lbl_eta.config(text="")
        self._recovery_done = set()
        clear_progress()

        if stats:
            self.lbl_st.config(text="状态: 已完成")
            self.prog_t['value'] = 100
            self.lbl_tp.config(text="100%")
            s = (f"\n{'=' * 40}\n📊 任务汇总\n"
                 f"   总计: {stats['total']} 个文件夹\n"
                 f"   ✅ 成功: {stats['success']}\n"
                 f"   ⏭️ 跳过(已存在): {stats['skipped']}\n"
                 f"   ⏭️ 跳过(手动): {stats['skipped_user']}\n"
                 f"   ⚠️ 校验拦截(未删除): {stats['verify_blocked']}\n"
                 f"   ❌ 失败: {stats['failed']}\n"
                 f"   ⏱️ 耗时: {stats['elapsed']}\n"
                 f"   📁 压缩前: {stats['sz_before']}\n"
                 f"   📦 压缩后: {stats['sz_after']}\n"
                 f"{'=' * 40}")
            self._log(s, "INFO")
        else:
            self.lbl_st.config(text="状态: 已停止")

        try:
            if sys.platform == 'win32':
                import winsound
                winsound.MessageBeep(winsound.MB_ICONINFORMATION)
        except Exception:
            pass

    # ============================================================
    #  工作线程 (FIX3: 智能并行完整实现)
    # ============================================================
   
    # ============================================================
    #  工作线程 (FIX3: 智能并行完整实现)
    # ============================================================
    def _worker(self, src, out, use_pwd, del_src,
                skip_exist, enh_verify, comp_lv,
                excludes, max_wk, recovery,
                sel_ws, smart, threshold):
        os.makedirs(out, exist_ok=True)
        src = os.path.abspath(src)
        out = os.path.abspath(out)
        total = len(sel_ws)

        if total == 0:
            self.msg_queue.put(("LOG", "未找到文件夹。", "WARNING"))
            self.msg_queue.put(("DONE", None))
            return

        # 密码
        password = None
        if use_pwd:
            pq = queue.Queue()
            self.msg_queue.put(("ASK_PWD", None, pq))
            password = pq.get()
            if not password:
                self.msg_queue.put((
                    "LOG", "⚠️ 未输入密码，不加密。", "WARNING"))

        t0 = time.time()
        ct = {"ok": 0, "skip": 0, "skip_u": 0,
              "vblock": 0, "fail": 0}
        sz = {"before": 0, "after": 0}
        done_n = 0
        lock = threading.Lock()
        done_names = set(recovery)

        save_progress({
            "source_dir": src, "output_dir": out,
            "total": total,
            "completed": list(done_names)
        })

        def upd_eta():
            if done_n > 0:
                ela = time.time() - t0
                rem = ela / done_n * (total - done_n)
                self.msg_queue.put(("ETA", format_eta(rem)))

        def do_one(idx, item, item_size):
            nonlocal done_n
            if not self.is_running:
                return

            # 已完成的跳过
            if item in done_names:
                self.msg_queue.put((
                    "LOG", f"⏭️ 跳过(已完成): {item}", "INFO"))
                with lock:
                    ct["skip"] += 1
                    done_n += 1
                    self.msg_queue.put((
                        "PROG_TOTAL", done_n / total * 100))
                    upd_eta()
                return

            # 已存在的跳过
            ap = os.path.join(out, f"{item}.7z")
            if skip_exist and os.path.exists(ap):
                self.msg_queue.put((
                    "LOG", f"⏭️ 跳过(已存在): {item}.7z", "INFO"))
                with lock:
                    ct["skip"] += 1
                    done_n += 1
                    done_names.add(item)
                    save_progress({
                        "source_dir": src,
                        "output_dir": out,
                        "total": total,
                        "completed": list(done_names)
                    })
                    self.msg_queue.put((
                        "PROG_TOTAL", done_n / total * 100))
                    upd_eta()
                return

            # 暂停等待
            while self._pause_event.is_set() and self.is_running:
                time.sleep(0.3)
            if not self.is_running:
                return

            # FIX2: 独立 skip event
            skip_ev = threading.Event()
            with self._skip_lock:
                self._skip_events[item] = skip_ev
            self._current_item = item

            self.msg_queue.put((
                "STATUS",
                f"正在处理 ({idx + 1}/{total}): {item}"))
            self.msg_queue.put(("FILE_NAME", item))
            self.msg_queue.put((
                "LOG",
                f"\n{'=' * 30}\n"
                f"📦 开始压缩: {item} "
                f"({format_size(item_size)})", "INFO"))

            def cb(mt, *a):
                if mt == "LOG":
                    lv = a[1] if len(a) > 1 else "INFO"
                    self.msg_queue.put(("LOG", a[0], lv))
                elif mt == "PROGRESS":
                    self.msg_queue.put(("PROG_FILE", a[0]))

            ip = os.path.join(src, item)
            r = self.engine.compress_folder(
                item, ip, ap, src, out, password,
                comp_lv, excludes, del_src,
                enh_verify, cb, skip_event=skip_ev)

            # 清除 skip event
            with self._skip_lock:
                self._skip_events.pop(item, None)

            # 统计
            with lock:
                done_n += 1
                sz["before"] += r["size_before"]
                sz["after"] += r["size_after"]
                if r.get("skipped_by_user"):
                    ct["skip_u"] += 1
                elif r.get("verify_blocked"):
                    ct["vblock"] += 1
                elif r["success"]:
                    ct["ok"] += 1
                    done_names.add(item)
                    save_progress({
                        "source_dir": src,
                        "output_dir": out,
                        "total": total,
                        "completed": list(done_names)
                    })
                else:
                    ct["fail"] += 1
                self.msg_queue.put((
                    "PROG_TOTAL", done_n / total * 100))
                upd_eta()

        # ---- FIX3: 智能并行策略 ----
        if smart and max_wk > 1:
            large = [(i, (n, s)) for i, (n, s)
                     in enumerate(sel_ws) if s >= threshold]
            small = [(i, (n, s)) for i, (n, s)
                     in enumerate(sel_ws) if s < threshold]
            self.msg_queue.put((
                "LOG",
                f"📊 智能并行: {len(large)} 个大文件夹(串行), "
                f"{len(small)} 个小文件夹(并行×{max_wk})",
                "INFO"))

            # 阶段1: 大文件夹串行
            for idx, (name, size) in large:
                if not self.is_running:
                    break
                do_one(idx, name, size)

            # 阶段2: 小文件夹并行
            if self.is_running and small:
                with ThreadPoolExecutor(
                        max_workers=max_wk) as pool:
                    futs = {
                        pool.submit(do_one, idx, name, size): name
                        for idx, (name, size) in small
                    }
                    for fut in as_completed(futs):
                        if not self.is_running:
                            pool.shutdown(wait=False,
                                         cancel_futures=True)
                            break
                        try:
                            fut.result()
                        except Exception as e:
                            self.msg_queue.put((
                                "LOG",
                                f"❌ 线程异常: {e}",
                                "ERROR"))

        elif max_wk <= 1:
            # 纯串行
            for i, (name, size) in enumerate(sel_ws):
                if not self.is_running:
                    break
                do_one(i, name, size)

        else:
            # 非智能并行
            with ThreadPoolExecutor(
                    max_workers=max_wk) as pool:
                futs = {
                    pool.submit(do_one, i, name, size): name
                    for i, (name, size) in enumerate(sel_ws)
                }
                for fut in as_completed(futs):
                    if not self.is_running:
                        pool.shutdown(wait=False,
                                     cancel_futures=True)
                        break
                    try:
                        fut.result()
                    except Exception as e:
                        self.msg_queue.put((
                            "LOG",
                            f"❌ 线程异常: {e}",
                            "ERROR"))

        # 汇总
        ela = time.time() - t0
        m, s = divmod(int(ela), 60)
        h, m = divmod(m, 60)
        if h > 0:
            es = f"{h}小时{m}分{s}秒"
        elif m > 0:
            es = f"{m}分{s}秒"
        else:
            es = f"{s}秒"

        stats = {
            "total": total,
            "success": ct["ok"],
            "skipped": ct["skip"],
            "skipped_user": ct["skip_u"],
            "verify_blocked": ct["vblock"],
            "failed": ct["fail"],
            "elapsed": es,
            "sz_before": format_size(sz["before"]),
            "sz_after": format_size(sz["after"]),
        }
        self.msg_queue.put(("PROG_TOTAL", 100))
        self.msg_queue.put((
            "LOG", "\n🎉 所有任务处理完毕！", "SUCCESS"))
        self.msg_queue.put(("DONE", stats))


# ============================================================
#  程序入口
# ============================================================
if __name__ == "__main__":
    root = tk.Tk()
    app = CompressApp7Z(root)

    def on_closing():
        if app.is_running:
            if messagebox.askokcancel(
                    "退出", "任务正在进行中，确定要强制退出吗？"):
                app.is_running = False
                app._pause_event.clear()
                with app._skip_lock:
                    for ev in app._skip_events.values():
                        ev.set()
                app.engine.terminate_all()
                app._save_cfg()
                root.destroy()
        else:
            app._save_cfg()
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()


