
import os
import sys
import json
import shutil
import stat
import subprocess
import threading
import queue
import re
import locale
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
#  常量定义
# ============================================================

WINDOW_TITLE = "批量 7Z 压缩工具 v2.0"
WINDOW_SIZE = "780x680"
MIN_WINDOW_SIZE = (700, 580)
QUEUE_POLL_INTERVAL_MS = 80
PROCESS_TERMINATE_TIMEOUT = 3

COMPRESS_LEVELS = {
    "存储 (不压缩, -mx=0)": 0,
    "快速 (-mx=1)": 1,
    "标准 (-mx=5)": 5,
    "极限 (-mx=9)": 9,
}
DEFAULT_COMPRESS_LEVEL_KEY = "标准 (-mx=5)"

DEFAULT_EXCLUDES = "Thumbs.db;desktop.ini;.DS_Store;*.tmp"

CONFIG_FILENAME = "compress7z_config.json"


# ============================================================
#  工具函数
# ============================================================

def get_runtime_dir():
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    else:
        return os.path.dirname(os.path.abspath(__file__))


def get_application_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))


def get_config_path():
    return os.path.join(get_application_path(), CONFIG_FILENAME)


def get_system_encoding():
    try:
        return locale.getpreferredencoding(False)
    except Exception:
        return 'utf-8'


def force_remove_readonly(func, path, excinfo):
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except Exception:
        pass


def get_subprocess_flags():
    if sys.platform == 'win32':
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        return {'creationflags': subprocess.CREATE_NO_WINDOW,
                'startupinfo': si}
    return {}


def get_folder_size(path):
    total = 0
    try:
        for dirpath, dirnames, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    except OSError:
        pass
    return total


def format_size(size_bytes):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def get_free_space(path):
    try:
        usage = shutil.disk_usage(path)
        return usage.free
    except Exception:
        return None


def long_path(p):
    if sys.platform == 'win32' and len(p) > 240 \
            and not p.startswith('\\\\?\\'):
        return '\\\\?\\' + os.path.abspath(p)
    return p


# ============================================================
#  配置管理
# ============================================================

def load_config():
    path = get_config_path()
    defaults = {
        "source_dir": get_application_path(),
        "output_same_as_source": True,
        "output_dir": "",
        "use_password": False,
        "delete_source": False,
        "skip_existing": True,
        "compress_level": DEFAULT_COMPRESS_LEVEL_KEY,
        "excludes": DEFAULT_EXCLUDES,
        "max_workers": 1,
    }
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            defaults.update(saved)
    except Exception:
        pass
    return defaults


def save_config(cfg):
    try:
        with open(get_config_path(), 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ============================================================
#  压缩引擎（纯逻辑，与 UI 无关）
# ============================================================

class CompressEngine:

    def __init__(self, seven_zip_path, console_encoding,
                 subprocess_flags):
        self.seven_zip_path = seven_zip_path
        self.console_encoding = console_encoding
        self.subprocess_flags = subprocess_flags
        self._lock = threading.Lock()
        self._processes = {}

    @staticmethod
    def find_7z():
        runtime_dir = get_runtime_dir()
        embedded = os.path.join(runtime_dir, "7z.exe")
        if os.path.exists(embedded):
            return embedded

        from_env = shutil.which("7z")
        if from_env:
            return from_env

        for p in [r"C:\Program Files\7-Zip\7z.exe",
                   r"C:\Program Files (x86)\7-Zip\7z.exe"]:
            if os.path.exists(p):
                return p

        local = os.path.join(get_application_path(), "7z.exe")
        if os.path.exists(local):
            return local
        return None

    def terminate_all(self):
        with self._lock:
            for name, proc in self._processes.items():
                try:
                    proc.terminate()
                except Exception:
                    pass

    def compress_folder(self, item_name, item_path, archive_path,
                        source_dir, output_dir, password,
                        compress_level, exclude_patterns,
                        delete_source, callback):
        zip_name = os.path.basename(archive_path)
        result = {"success": False, "skipped": False,
                  "error_msg": None, "size_before": 0,
                  "size_after": 0}

        folder_size = get_folder_size(item_path)
        result["size_before"] = folder_size
        free_space = get_free_space(output_dir)
        if free_space is not None and free_space < folder_size:
            result["error_msg"] = (
                f"磁盘空间不足！需要约 {format_size(folder_size)}，"
                f"仅剩 {format_size(free_space)}")
            callback("LOG", f"❌ {result['error_msg']}")
            return result

        if len(archive_path) > 250:
            callback("LOG",
                     f"⚠️ 路径较长 ({len(archive_path)} 字符)，"
                     f"已启用长路径模式")
            archive_path = long_path(archive_path)

        cmd = [self.seven_zip_path, "a", "-t7z", "-mmt=on",
               f"-mx={compress_level}", "-bsp1"]

        if os.path.abspath(output_dir) == os.path.abspath(source_dir):
            cmd.append(f"-x!{zip_name}")

        for pat in exclude_patterns:
            pat = pat.strip()
            if pat:
                cmd.append(f"-xr!{pat}")

        cmd.append(archive_path)
        cmd.append(item_path)

        if password:
            cmd.append(f"-p{password}")
            cmd.append("-mhe=on")

        proc = None
        try:
            proc = subprocess.Popen(
                cmd, cwd=source_dir,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding=self.console_encoding,
                errors='replace', **self.subprocess_flags)
            with self._lock:
                self._processes[item_name] = proc

            for line in proc.stdout:
                match = re.search(r'(\d+)%', line)
                if match:
                    callback("PROGRESS", int(match.group(1)))

            proc.wait()
            with self._lock:
                self._processes.pop(item_name, None)

            if proc.returncode == 0:
                callback("PROGRESS", 100)
                callback("LOG", f"✅ 压缩完成: {zip_name}")

                try:
                    result["size_after"] = os.path.getsize(
                        archive_path.replace('\\\\?\\', ''))
                except OSError:
                    result["size_after"] = 0

                if delete_source:
                    callback("LOG", "   🔍 正在校验完整性...")
                    test_cmd = [self.seven_zip_path, "t",
                                archive_path]
                    if password:
                        test_cmd.append(f"-p{password}")

                    test = subprocess.run(
                        test_cmd, capture_output=True, text=True,
                        encoding=self.console_encoding,
                        errors='replace', **self.subprocess_flags)
                    if test.returncode == 0:
                        callback("LOG", "   ✅ 校验通过！")
                        try:
                            shutil.rmtree(
                                item_path,
                                onerror=force_remove_readonly)
                            callback("LOG",
                                     f"   🗑️ 源文件夹已删除: "
                                     f"{item_name}")
                        except PermissionError:
                            result["error_msg"] = \
                                "删除失败: 权限不足，文件可能被占用"
                            callback("LOG",
                                     f"   ⚠️ {result['error_msg']}")
                        except Exception as e:
                            result["error_msg"] = f"删除失败: {e}"
                            callback("LOG",
                                     f"   ⚠️ {result['error_msg']}")
                    else:
                        result["error_msg"] = \
                            "7z 完整性测试失败，已跳过删除"
                        callback("LOG",
                                 f"   ⚠️ 危险拦截："
                                 f"{result['error_msg']}")

                result["success"] = True
            else:
                result["error_msg"] = \
                    f"7z 返回码: {proc.returncode}"
                callback("LOG",
                         f"❌ 压缩失败，{result['error_msg']}")
                self._safe_remove(archive_path)

        except FileNotFoundError:
            result["error_msg"] = \
                "7z.exe 未找到，引擎可能已被移动或删除"
            callback("LOG", f"❌ {result['error_msg']}")
            self._safe_remove(archive_path)
        except PermissionError:
            result["error_msg"] = "权限不足，无法访问文件或目录"
            callback("LOG", f"❌ {result['error_msg']}")
            self._safe_remove(archive_path)
        except Exception as e:
            result["error_msg"] = str(e)
            callback("LOG", f"❌ 发生异常: {e}")
            self._safe_remove(archive_path)
        finally:
            with self._lock:
                self._processes.pop(item_name, None)

        return result

    @staticmethod
    def _safe_remove(path):
        clean = path.replace('\\\\?\\', '') if path else path
        if clean and os.path.exists(clean):
            try:
                os.remove(clean)
            except Exception:
                pass


# ============================================================
#  GUI 界面
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

        self.console_encoding = get_system_encoding()
        self.sp_flags = get_subprocess_flags()
        self.seven_zip_path = CompressEngine.find_7z()
        self.engine = CompressEngine(
            self.seven_zip_path, self.console_encoding, self.sp_flags)
        self.cpu_count = os.cpu_count() or 4

        self._setup_ui()
        self._load_config_to_ui()
        self._setup_dnd()
        self._poll_queue()

    # -------------------- UI 构建 --------------------

    def _setup_ui(self):
        main = ttk.Frame(self.root, padding="12")
        main.pack(fill=tk.BOTH, expand=True)

        # === 引擎状态 ===
        ef = ttk.LabelFrame(main, text="引擎状态", padding="5")
        ef.pack(fill=tk.X, pady=(0, 8))
        if self.seven_zip_path:
            if (getattr(sys, 'frozen', False)
                    and os.path.dirname(self.seven_zip_path)
                    == get_runtime_dir()):
                txt = "✅ 已加载内嵌 7-Zip 引擎"
            else:
                txt = f"✅ 已找到 7-Zip: {self.seven_zip_path}"
            ttk.Label(ef, text=txt,
                      foreground="green").pack(anchor=tk.W)
        else:
            ttk.Label(
                ef, foreground="red",
                text="❌ 未找到 7-Zip 引擎！"
                     "请安装 7-Zip 或将 7z.exe 放在同目录"
            ).pack(anchor=tk.W)

        # === 目录设置 ===
        pf = ttk.LabelFrame(main, text="目录设置", padding="8")
        pf.pack(fill=tk.X, pady=(0, 8))
        pf.columnconfigure(1, weight=1)

        ttk.Label(pf, text="总文件夹:").grid(
            row=0, column=0, sticky=tk.W)
        self.entry_source = ttk.Entry(pf)
        self.entry_source.grid(
            row=0, column=1, sticky=tk.EW, padx=5)
        ttk.Button(pf, text="浏览…", width=7,
                   command=self._browse_source).grid(
            row=0, column=2)

        ttk.Label(pf, text="输出目录:").grid(
            row=1, column=0, sticky=tk.W, pady=5)
        self.entry_output = ttk.Entry(pf)
        self.entry_output.grid(
            row=1, column=1, sticky=tk.EW, padx=5, pady=5)
        self.btn_browse_output = ttk.Button(
            pf, text="浏览…", width=7,
            command=self._browse_output)
        self.btn_browse_output.grid(row=1, column=2, pady=5)

        # 默认输出到总文件夹
        self.var_output_same = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            pf, text="输出到总文件夹（取消勾选可自定义输出目录）",
            variable=self.var_output_same,
            command=self._toggle_output_entry
        ).grid(row=2, column=1, sticky=tk.W, padx=5)

        # 初始状态：禁用输出目录输入和浏览
        self.entry_output.config(state=tk.DISABLED)
        self.btn_browse_output.config(state=tk.DISABLED)

        # === 功能选项 ===
        of = ttk.LabelFrame(main, text="功能选项", padding="8")
        of.pack(fill=tk.X, pady=(0, 8))

        row0 = ttk.Frame(of)
        row0.pack(fill=tk.X)

        self.var_pwd = tk.BooleanVar()
        self.var_del = tk.BooleanVar()
        self.var_skip = tk.BooleanVar(value=True)

        ttk.Checkbutton(row0, text="加密压缩包",
                        variable=self.var_pwd).pack(
            side=tk.LEFT, padx=(0, 15))
        ttk.Checkbutton(row0, text="压缩后删除源文件夹",
                        variable=self.var_del).pack(
            side=tk.LEFT, padx=(0, 15))
        ttk.Checkbutton(row0, text="跳过已存在的压缩包",
                        variable=self.var_skip).pack(side=tk.LEFT)

        row1 = ttk.Frame(of)
        row1.pack(fill=tk.X, pady=(6, 0))

        ttk.Label(row1, text="压缩级别:").pack(side=tk.LEFT)
        self.cmb_level = ttk.Combobox(
            row1, values=list(COMPRESS_LEVELS.keys()),
            state="readonly", width=22)
        self.cmb_level.set(DEFAULT_COMPRESS_LEVEL_KEY)
        self.cmb_level.pack(side=tk.LEFT, padx=(5, 20))

        ttk.Label(row1, text="并行数:").pack(side=tk.LEFT)
        self.spn_workers = ttk.Spinbox(
            row1, from_=1, to=self.cpu_count,
            width=4, state="readonly")
        self.spn_workers.set(1)
        self.spn_workers.pack(side=tk.LEFT, padx=5)
        ttk.Label(row1, text=f"(最大 {self.cpu_count})",
                  foreground="gray").pack(side=tk.LEFT)

        row2 = ttk.Frame(of)
        row2.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(row2, text="排除规则:").pack(side=tk.LEFT)
        self.entry_excludes = ttk.Entry(row2)
        self.entry_excludes.pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Label(row2, text="分号分隔",
                  foreground="gray").pack(side=tk.LEFT)

        # === 进度显示 ===
        pgf = ttk.LabelFrame(main, text="压缩进度", padding="8")
        pgf.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        # 总进度行：状态文字 + 百分比
        total_row = ttk.Frame(pgf)
        total_row.pack(fill=tk.X)
        self.lbl_status = ttk.Label(
            total_row, text="状态: 等待开始…")
        self.lbl_status.pack(side=tk.LEFT, anchor=tk.W)
        self.lbl_total_pct = ttk.Label(
            total_row, text="0%", width=6, anchor=tk.E,
            font=("Consolas", 10, "bold"))
        self.lbl_total_pct.pack(side=tk.RIGHT)

        self.prog_total = ttk.Progressbar(
            pgf, orient=tk.HORIZONTAL, mode='determinate')
        self.prog_total.pack(fill=tk.X, pady=(4, 0))

        # 单文件进度行：文件名 + 百分比
        file_row = ttk.Frame(pgf)
        file_row.pack(fill=tk.X, pady=(8, 0))
        self.lbl_file = ttk.Label(
            file_row, text="当前文件: -")
        self.lbl_file.pack(side=tk.LEFT, anchor=tk.W)
        self.lbl_file_pct = ttk.Label(
            file_row, text="0%", width=6, anchor=tk.E,
            font=("Consolas", 10, "bold"))
        self.lbl_file_pct.pack(side=tk.RIGHT)

        self.prog_file = ttk.Progressbar(
            pgf, orient=tk.HORIZONTAL, mode='determinate')
        self.prog_file.pack(fill=tk.X, pady=(2, 0))

        # 日志 + 滚动条
        log_frame = ttk.Frame(pgf)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        self.txt_log = tk.Text(
            log_frame, height=8, state=tk.DISABLED,
            wrap=tk.WORD, font=("Consolas", 9))
        scrollbar = ttk.Scrollbar(
            log_frame, command=self.txt_log.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.txt_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.txt_log.config(yscrollcommand=scrollbar.set)

        # 日志右键菜单
        self._log_menu = tk.Menu(self.txt_log, tearoff=0)
        self._log_menu.add_command(
            label="复制全部", command=self._copy_log)
        self._log_menu.add_command(
            label="清空日志", command=self._clear_log)
        self.txt_log.bind("<Button-3>", self._show_log_menu)

        # === 按钮栏 ===
        bf = ttk.Frame(main)
        bf.pack(fill=tk.X)
        self.btn_start = ttk.Button(
            bf, text="🚀 开始压缩", command=self._start_task)
        self.btn_start.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_stop = ttk.Button(
            bf, text="⏹ 强制停止",
            command=self._stop_task, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_export = ttk.Button(
            bf, text="📄 导出日志", command=self._export_log)
        self.btn_export.pack(side=tk.LEFT)

    # ---------- 输出目录开关 ----------

    def _toggle_output_entry(self):
        if self.var_output_same.get():
            self.entry_output.config(state=tk.DISABLED)
            self.btn_browse_output.config(state=tk.DISABLED)
        else:
            self.entry_output.config(state=tk.NORMAL)
            self.btn_browse_output.config(state=tk.NORMAL)

    # ---------- 拖拽支持 ----------

    def _setup_dnd(self):
        try:
            from tkinterdnd2 import DND_FILES
            self.entry_source.drop_target_register(DND_FILES)
            self.entry_source.dnd_bind(
                '<<Drop>>', self._on_drop_source)
            self.entry_output.drop_target_register(DND_FILES)
            self.entry_output.dnd_bind(
                '<<Drop>>', self._on_drop_output)
        except Exception:
            pass

    def _on_drop_source(self, event):
        path = event.data.strip('{}')
        if os.path.isdir(path):
            self.entry_source.delete(0, tk.END)
            self.entry_source.insert(0, path)

    def _on_drop_output(self, event):
        path = event.data.strip('{}')
        if os.path.isdir(path):
            self.entry_output.config(state=tk.NORMAL)
            self.entry_output.delete(0, tk.END)
            self.entry_output.insert(0, path)
            self.var_output_same.set(False)
            self.btn_browse_output.config(state=tk.NORMAL)

    # ---------- 配置持久化 ----------

    def _load_config_to_ui(self):
        c = self.config
        self.entry_source.insert(0, c.get("source_dir", ""))

        output_same = c.get("output_same_as_source", True)
        self.var_output_same.set(output_same)
        if not output_same:
            self.entry_output.config(state=tk.NORMAL)
            self.btn_browse_output.config(state=tk.NORMAL)
            self.entry_output.insert(
                0, c.get("output_dir", ""))

        self.var_pwd.set(c.get("use_password", False))
        self.var_del.set(c.get("delete_source", False))
        self.var_skip.set(c.get("skip_existing", True))
        level_key = c.get("compress_level",
                          DEFAULT_COMPRESS_LEVEL_KEY)
        if level_key in COMPRESS_LEVELS:
            self.cmb_level.set(level_key)
        self.entry_excludes.insert(
            0, c.get("excludes", DEFAULT_EXCLUDES))
        workers = c.get("max_workers", 1)
        self.spn_workers.set(min(workers, self.cpu_count))

    def _save_current_config(self):
        cfg = {
            "source_dir": self.entry_source.get().strip(),
            "output_same_as_source": self.var_output_same.get(),
            "output_dir": self.entry_output.get().strip(),
            "use_password": self.var_pwd.get(),
            "delete_source": self.var_del.get(),
            "skip_existing": self.var_skip.get(),
            "compress_level": self.cmb_level.get(),
            "excludes": self.entry_excludes.get().strip(),
            "max_workers": int(self.spn_workers.get()),
        }
        save_config(cfg)

    # ---------- 日志工具 ----------

    def _log(self, message):
        self.txt_log.config(state=tk.NORMAL)
        self.txt_log.insert(tk.END, message + "\n")
        self.txt_log.see(tk.END)
        self.txt_log.config(state=tk.DISABLED)

    def _show_log_menu(self, event):
        self._log_menu.tk_popup(event.x_root, event.y_root)

    def _copy_log(self):
        content = self.txt_log.get("1.0", tk.END).strip()
        if content:
            self.root.clipboard_clear()
            self.root.clipboard_append(content)

    def _clear_log(self):
        self.txt_log.config(state=tk.NORMAL)
        self.txt_log.delete("1.0", tk.END)
        self.txt_log.config(state=tk.DISABLED)

    def _export_log(self):
        content = self.txt_log.get("1.0", tk.END).strip()
        if not content:
            messagebox.showinfo("提示", "日志为空，无需导出。")
            return
        path = filedialog.asksaveasfilename(
            title="导出日志", defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"),
                       ("所有文件", "*.*")])
        if path:
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(content)
                messagebox.showinfo(
                    "成功", f"日志已导出至:\n{path}")
            except Exception as e:
                messagebox.showerror("错误", f"导出失败: {e}")

    # ---------- 浏览按钮 ----------

    def _browse_source(self):
        path = filedialog.askdirectory(title="选择总文件夹")
        if path:
            self.entry_source.delete(0, tk.END)
            self.entry_source.insert(0, path)

    def _browse_output(self):
        if self.var_output_same.get():
            return
        path = filedialog.askdirectory(title="选择输出目录")
        if path:
            self.entry_output.config(state=tk.NORMAL)
            self.entry_output.delete(0, tk.END)
            self.entry_output.insert(0, path)

    # ---------- 消息队列轮询 ----------

    def _poll_queue(self):
        latest_progress = None
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                t = msg[0]
                if t == "LOG":
                    self._log(msg[1])
                elif t == "STATUS":
                    self.lbl_status.config(
                        text=f"状态: {msg[1]}")
                elif t == "FILE_NAME":
                    self.lbl_file.config(
                        text=f"当前文件: {msg[1]}")
                    # 切换文件时重置单文件进度
                    self.prog_file['value'] = 0
                    self.lbl_file_pct.config(text="0%")
                elif t == "PROG_TOTAL":
                    val = msg[1]
                    self.prog_total['value'] = val
                    self.lbl_total_pct.config(
                        text=f"{int(val)}%")
                elif t == "PROG_FILE":
                    latest_progress = msg[1]
                elif t == "ASK_PWD":
                    pwd = simpledialog.askstring(
                        "设置密码",
                        "请输入压缩包密码（取消则不加密）:",
                        show="*", parent=self.root)
                    msg[2].put(pwd)
                elif t == "DONE":
                    self._task_finished(
                        msg[1] if len(msg) > 1 else None)
        except queue.Empty:
            pass

        if latest_progress is not None:
            self.prog_file['value'] = latest_progress
            self.lbl_file_pct.config(
                text=f"{int(latest_progress)}%")

        self.root.after(QUEUE_POLL_INTERVAL_MS, self._poll_queue)

    # ---------- 任务控制 ----------

    def _start_task(self):
        if not self.seven_zip_path:
            messagebox.showerror(
                "错误",
                "未找到 7-Zip 引擎！\n"
                "请安装 7-Zip 或将 7z.exe 放在同目录。")
            return

        source_dir = self.entry_source.get().strip()
        if not source_dir or not os.path.isdir(source_dir):
            messagebox.showerror("错误", "请选择有效的总文件夹路径！")
            return

        # 输出目录：勾选则同源，否则读取输入框
        if self.var_output_same.get():
            output_dir = source_dir
        else:
            output_dir = self.entry_output.get().strip()
            if not output_dir:
                output_dir = source_dir

        self._save_current_config()

        self.is_running = True
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self._clear_log()
        self.prog_total['value'] = 0
        self.prog_file['value'] = 0
        self.lbl_total_pct.config(text="0%")
        self.lbl_file_pct.config(text="0%")

        level_key = self.cmb_level.get()
        compress_level = COMPRESS_LEVELS.get(level_key, 5)
        excludes = [x.strip() for x in
                    self.entry_excludes.get().split(';')
                    if x.strip()]
        max_workers = int(self.spn_workers.get())

        thread = threading.Thread(
            target=self._worker_main,
            args=(source_dir, output_dir,
                  self.var_pwd.get(), self.var_del.get(),
                  self.var_skip.get(), compress_level,
                  excludes, max_workers),
            daemon=True)
        thread.start()

    def _stop_task(self):
        self.is_running = False
        self._log("⚠️ 正在终止所有 7-Zip 进程…")
        self.engine.terminate_all()
        self.btn_stop.config(state=tk.DISABLED)

    def _task_finished(self, stats):
        self.is_running = False
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.lbl_file.config(text="当前文件: -")
        self.prog_file['value'] = 0
        self.lbl_file_pct.config(text="0%")

        if stats:
            self.lbl_status.config(text="状态: 已完成")
            self.prog_total['value'] = 100
            self.lbl_total_pct.config(text="100%")
            summary = (
                f"\n{'=' * 40}\n"
                f"📊 任务汇总\n"
                f"   总计: {stats['total']} 个文件夹\n"
                f"   ✅ 成功: {stats['success']}\n"
                f"   ⏭️ 跳过: {stats['skipped']}\n"
                f"   ❌ 失败: {stats['failed']}\n"
                f"   ⏱️ 耗时: {stats['elapsed']}\n"
                f"   📁 压缩前总大小: {stats['size_before']}\n"
                f"   📦 压缩后总大小: {stats['size_after']}\n"
                f"{'=' * 40}")
            self._log(summary)
        else:
            self.lbl_status.config(text="状态: 已停止")

        try:
            if sys.platform == 'win32':
                import winsound
                winsound.MessageBeep(
                    winsound.MB_ICONINFORMATION)
        except Exception:
            pass

    # ---------- 工作线程 ----------

    def _worker_main(self, source_dir, output_dir,
                     use_password, delete_source,
                     skip_existing, compress_level,
                     exclude_patterns, max_workers):

        os.makedirs(output_dir, exist_ok=True)
        source_dir = os.path.abspath(source_dir)
        output_dir = os.path.abspath(output_dir)

        folders = sorted(
            [item for item in os.listdir(source_dir)
             if os.path.isdir(os.path.join(source_dir, item))],
            key=lambda x: get_folder_size(
                os.path.join(source_dir, x)),
            reverse=True)
        total = len(folders)

        if total == 0:
            self.msg_queue.put(("LOG", "未找到任何子文件夹。"))
            self.msg_queue.put(("DONE", None))
            return

        password = None
        if use_password:
            pwd_q = queue.Queue()
            self.msg_queue.put(("ASK_PWD", None, pwd_q))
            password = pwd_q.get()
            if not password:
                self.msg_queue.put(
                    ("LOG", "⚠️ 未输入密码，按不加密处理。"))

        start_time = time.time()
        count_success = 0
        count_skipped = 0
        count_failed = 0
        total_size_before = 0
        total_size_after = 0
        completed = 0
        completed_lock = threading.Lock()

        def do_compress(idx, item):
            nonlocal count_success, count_skipped, count_failed
            nonlocal total_size_before, total_size_after, completed

            if not self.is_running:
                return

            item_path = os.path.join(source_dir, item)
            archive_path = os.path.join(
                output_dir, f"{item}.7z")

            if skip_existing and os.path.exists(archive_path):
                self.msg_queue.put(
                    ("LOG", f"⏭️ 跳过 (已存在): {item}.7z"))
                with completed_lock:
                    count_skipped += 1
                    completed += 1
                    self.msg_queue.put((
                        "PROG_TOTAL",
                        completed / total * 100))
                return

            self.msg_queue.put((
                "STATUS",
                f"正在处理 ({idx+1}/{total}): {item}"))
            self.msg_queue.put(("FILE_NAME", item))
            self.msg_queue.put((
                "LOG",
                f"\n{'='*30}\n📦 开始压缩: {item}"))

            def cb(msg_type, *args):
                if msg_type == "LOG":
                    self.msg_queue.put(("LOG", args[0]))
                elif msg_type == "PROGRESS":
                    self.msg_queue.put(("PROG_FILE", args[0]))

            result = self.engine.compress_folder(
                item, item_path, archive_path,
                source_dir, output_dir, password,
                compress_level, exclude_patterns,
                delete_source, cb)

            with completed_lock:
                completed += 1
                total_size_before += result["size_before"]
                total_size_after += result["size_after"]
                if result["skipped"]:
                    count_skipped += 1
                elif result["success"]:
                    count_success += 1
                else:
                    count_failed += 1
                self.msg_queue.put((
                    "PROG_TOTAL",
                    completed / total * 100))

        if max_workers <= 1:
            for i, item in enumerate(folders):
                if not self.is_running:
                    break
                do_compress(i, item)
        else:
            with ThreadPoolExecutor(
                    max_workers=max_workers) as pool:
                futures = {
                    pool.submit(do_compress, i, item): item
                    for i, item in enumerate(folders)}
                for future in as_completed(futures):
                    if not self.is_running:
                        pool.shutdown(
                            wait=False, cancel_futures=True)
                        break
                    try:
                        future.result()
                    except Exception as e:
                        self.msg_queue.put(
                            ("LOG", f"❌ 线程异常: {e}"))

        elapsed = time.time() - start_time
        mins, secs = divmod(int(elapsed), 60)
        hours, mins = divmod(mins, 60)
        if hours > 0:
            elapsed_str = f"{hours}小时{mins}分{secs}秒"
        elif mins > 0:
            elapsed_str = f"{mins}分{secs}秒"
        else:
            elapsed_str = f"{secs}秒"

        stats = {
            "total": total,
            "success": count_success,
            "skipped": count_skipped,
            "failed": count_failed,
            "elapsed": elapsed_str,
            "size_before": format_size(total_size_before),
            "size_after": format_size(total_size_after),
        }

        self.msg_queue.put(("PROG_TOTAL", 100))
        self.msg_queue.put(("LOG", "\n🎉 所有任务处理完毕！"))
        self.msg_queue.put(("DONE", stats))


# ============================================================
#  入口
# ============================================================

if __name__ == "__main__":
    root = tk.Tk()
    app = CompressApp7Z(root)

    def on_closing():
        if app.is_running:
            if messagebox.askokcancel(
                    "退出", "任务正在进行中，确定要强制退出吗？"):
                app.is_running = False
                app.engine.terminate_all()
                root.destroy()
        else:
            app._save_current_config()
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()

