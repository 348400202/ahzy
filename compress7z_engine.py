
"""
批量 7Z 压缩工具 v2.3 - Part 1: 引擎与工具函数
文件名: compress7z_engine.py
"""
import os, sys, json, shutil, stat, subprocess
import threading, re, locale, fnmatch

# ============================================================
#  常量
# ============================================================
CONFIG_FILENAME = "compress7z_config.json"
PROGRESS_FILENAME = "compress7z_progress.json"
PROCESS_TERMINATE_TIMEOUT = 3
COMPRESS_LEVELS = {
    "存储 (不压缩, -mx=0)": 0,
    "快速 (-mx=1)": 1,
    "标准 (-mx=5)": 5,
    "极限 (-mx=9)": 9,
}
DEFAULT_COMPRESS_LEVEL_KEY = "标准 (-mx=5)"
DEFAULT_EXCLUDES = "Thumbs.db;desktop.ini;.DS_Store;*.tmp"
SMART_PARALLEL_THRESHOLD_GB_DEFAULT = 1
BYTES_PER_GB = 1024 * 1024 * 1024

DARK_COLORS = {
    "bg": "#1e1e1e", "fg": "#d4d4d4",
    "entry_bg": "#2d2d2d", "entry_fg": "#d4d4d4",
    "log_bg": "#1a1a1a", "log_fg": "#cccccc",
    "frame_bg": "#252526", "select_bg": "#264f78",
    "btn_bg": "#333333",
    "tree_bg": "#1e1e1e", "tree_fg": "#d4d4d4",
    "tree_select": "#264f78",
}
LIGHT_COLORS = {
    "bg": "#f0f0f0", "fg": "#000000",
    "entry_bg": "#ffffff", "entry_fg": "#000000",
    "log_bg": "#ffffff", "log_fg": "#000000",
    "frame_bg": "#f0f0f0", "select_bg": "#0078d4",
    "btn_bg": "#e0e0e0",
    "tree_bg": "#ffffff", "tree_fg": "#000000",
    "tree_select": "#cce8ff",
}


# ============================================================
#  工具函数
# ============================================================
def get_runtime_dir():
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def get_application_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_config_path():
    return os.path.join(get_application_path(), CONFIG_FILENAME)


def get_progress_path():
    return os.path.join(get_application_path(), PROGRESS_FILENAME)


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
        return {
            'creationflags': subprocess.CREATE_NO_WINDOW,
            'startupinfo': si
        }
    return {}


def get_folder_size(path):
    total = 0
    try:
        for dp, _, fns in os.walk(path):
            for f in fns:
                try:
                    total += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def get_folder_file_count(path):
    count = 0
    try:
        for _, _, fns in os.walk(path):
            count += len(fns)
    except OSError:
        pass
    return count


def format_size(b):
    for u in ('B', 'KB', 'MB', 'GB', 'TB'):
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} PB"


def format_eta(s):
    if s < 0:
        return "--"
    s = int(s)
    if s < 60:
        return f"{s}秒"
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}小时{m}分"
    return f"{m}分{s}秒"


def get_free_space(path):
    try:
        return shutil.disk_usage(path).free
    except Exception:
        return None


def long_path(p):
    if sys.platform == 'win32' and len(p) > 240 \
            and not p.startswith('\\\\?\\'):
        return '\\\\?\\' + os.path.abspath(p)
    return p


def sort_key_by_name(n):
    try:
        return locale.strxfrm(n)
    except Exception:
        return n.lower()


def is_windows_dark_mode():
    if sys.platform != 'win32':
        return False
    try:
        import winreg
        k = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion"
            r"\Themes\Personalize")
        v, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
        winreg.CloseKey(k)
        return v == 0
    except Exception:
        return False


def match_folder_filter(name, patterns):
    if not patterns:
        return True
    return any(
        fnmatch.fnmatch(name, p.strip())
        for p in patterns if p.strip()
    )


# ============================================================
#  配置 & 进度
# ============================================================
def load_config():
    defaults = {
        "source_dir": get_application_path(),
        "output_same_as_source": True,
        "output_dir": "",
        "use_password": False,
        "delete_source": False,
        "skip_existing": True,
        "enhanced_verify": False,
        "compress_level": DEFAULT_COMPRESS_LEVEL_KEY,
        "excludes": DEFAULT_EXCLUDES,
        "folder_filter": "",
        "max_workers": 1,
        "smart_parallel": False,
        "smart_threshold_gb": SMART_PARALLEL_THRESHOLD_GB_DEFAULT,
        "dark_mode": "auto",
        "window_x": None,
        "window_y": None,
    }
    try:
        p = get_config_path()
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8') as f:
                defaults.update(json.load(f))
    except Exception:
        pass
    return defaults


def save_config(cfg):
    try:
        with open(get_config_path(), 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_progress():
    try:
        p = get_progress_path()
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return None


def save_progress(data):
    try:
        with open(get_progress_path(), 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def clear_progress():
    try:
        p = get_progress_path()
        if os.path.exists(p):
            os.remove(p)
    except Exception:
        pass


# ============================================================
#  压缩引擎 (FIX5: verify_blocked / FIX6: 7z l -slt)
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
        rd = get_runtime_dir()
        e = os.path.join(rd, "7z.exe")
        if os.path.exists(e):
            return e
        fe = shutil.which("7z")
        if fe:
            return fe
        for p in [r"C:\Program Files\7-Zip\7z.exe",
                   r"C:\Program Files (x86)\7-Zip\7z.exe"]:
            if os.path.exists(p):
                return p
        lo = os.path.join(get_application_path(), "7z.exe")
        if os.path.exists(lo):
            return lo
        return None

    def terminate_all(self):
        with self._lock:
            for proc in self._processes.values():
                try:
                    proc.terminate()
                except Exception:
                    pass

    def terminate_one(self, name):
        with self._lock:
            proc = self._processes.get(name)
            if proc:
                try:
                    proc.terminate()
                except Exception:
                    pass

    def get_archive_file_count(self, archive_path,
                               password=None):
        """FIX6: 使用 7z l -slt 技术模式, 格式稳定"""
        cmd = [self.seven_zip_path, "l", "-slt", archive_path]
        if password:
            cmd.append(f"-p{password}")
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding=self.console_encoding,
                errors='replace', **self.subprocess_flags)
            if r.returncode == 0:
                count = 0
                parts = r.stdout.split("----------")
                if len(parts) >= 2:
                    blocks = parts[1].strip().split("\n\n")
                    for block in blocks:
                        block = block.strip()
                        if not block:
                            continue
                        attrs = {}
                        for line in block.splitlines():
                            if " = " in line:
                                k, v = line.split(" = ", 1)
                                attrs[k.strip()] = v.strip()
                        attr_val = attrs.get("Attributes", "")
                        if "D" not in attr_val and "Path" in attrs:
                            count += 1
                return count
        except Exception:
            pass
        return -1

    def compress_folder(self, item_name, item_path,
                        archive_path, source_dir, output_dir,
                        password, compress_level,
                        exclude_patterns, delete_source,
                        enhanced_verify, callback,
                        skip_event=None):
        """FIX5: verify_blocked 不再算 success"""
        zip_name = os.path.basename(archive_path)
        result = {
            "success": False, "skipped": False,
            "skipped_by_user": False,
            "verify_blocked": False,
            "error_msg": None,
            "size_before": 0, "size_after": 0,
        }

        folder_size = get_folder_size(item_path)
        result["size_before"] = folder_size
        free = get_free_space(output_dir)
        if free is not None and free < folder_size:
            result["error_msg"] = (
                f"磁盘空间不足！需要约 "
                f"{format_size(folder_size)}，"
                f"仅剩 {format_size(free)}")
            callback("LOG", f"❌ {result['error_msg']}", "ERROR")
            return result

        if len(archive_path) > 250:
            callback("LOG",
                     f"⚠️ 路径较长({len(archive_path)}字符)",
                     "WARNING")
            archive_path = long_path(archive_path)

        cmd = [self.seven_zip_path, "a", "-t7z",
               "-mmt=on", f"-mx={compress_level}", "-bsp1"]
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
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True,
                encoding=self.console_encoding,
                errors='replace', **self.subprocess_flags)
            with self._lock:
                self._processes[item_name] = proc

            for line in proc.stdout:
                if skip_event and skip_event.is_set():
                    proc.terminate()
                    try:
                        proc.wait(timeout=PROCESS_TERMINATE_TIMEOUT)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    break
                m = re.search(r'(\d+)%', line)
                if m:
                    callback("PROGRESS", int(m.group(1)))

            proc.wait()
            with self._lock:
                self._processes.pop(item_name, None)

            if skip_event and skip_event.is_set():
                result["skipped_by_user"] = True
                callback("LOG", f"⏭️ 已跳过: {item_name}", "WARNING")
                self._safe_remove(archive_path)
                return result

            if proc.returncode == 0:
                callback("PROGRESS", 100)
                try:
                    result["size_after"] = os.path.getsize(
                        archive_path.replace('\\\\?\\', ''))
                except OSError:
                    result["size_after"] = 0

                if result["size_before"] > 0:
                    ratio = (1 - result["size_after"] /
                             result["size_before"]) * 100
                    callback(
                        "LOG",
                        f"✅ 压缩完成: {zip_name}  "
                        f"({format_size(result['size_before'])}"
                        f" → {format_size(result['size_after'])}"
                        f"  压缩率 {ratio:.1f}%)", "SUCCESS")
                else:
                    callback("LOG",
                             f"✅ 压缩完成: {zip_name}", "SUCCESS")

                # --- 删除前校验 ---
                if delete_source:
                    # 校验1: 7z t
                    callback("LOG",
                             "   🔍 校验1: 7z 完整性测试...",
                             "INFO")
                    tcmd = [self.seven_zip_path, "t", archive_path]
                    if password:
                        tcmd.append(f"-p{password}")
                    t = subprocess.run(
                        tcmd, capture_output=True, text=True,
                        encoding=self.console_encoding,
                        errors='replace', **self.subprocess_flags)
                    if t.returncode != 0:
                        result["verify_blocked"] = True
                        result["error_msg"] = "7z完整性测试失败"
                        callback("LOG",
                                 "   ⚠️ 危险拦截：校验失败，跳过删除",
                                 "ERROR")
                        return result
                    callback("LOG", "   ✅ 校验1 通过", "SUCCESS")

                    # 校验2: 文件数
                    if enhanced_verify:
                        callback("LOG",
                                 "   🔍 校验2: 对比文件数量...",
                                 "INFO")
                        sc = get_folder_file_count(item_path)
                        ac = self.get_archive_file_count(
                            archive_path, password)
                        if ac < 0:
                            callback("LOG",
                                     "   ⚠️ 无法读取压缩包列表，"
                                     "跳过增强校验", "WARNING")
                        elif sc != ac:
                            result["verify_blocked"] = True
                            result["error_msg"] = (
                                f"文件数不匹配！源:{sc} 包:{ac}")
                            callback("LOG",
                                     f"   ⚠️ 危险拦截："
                                     f"{result['error_msg']}",
                                     "ERROR")
                            return result
                        else:
                            callback("LOG",
                                     f"   ✅ 校验2 通过 "
                                     f"(文件数:{sc})", "SUCCESS")

                    # 删除源文件夹
                    try:
                        shutil.rmtree(item_path,
                                      onerror=force_remove_readonly)
                        callback("LOG",
                                 f"   🗑️ 源文件夹已删除: "
                                 f"{item_name}", "INFO")
                    except PermissionError:
                        result["error_msg"] = "删除失败: 权限不足"
                        callback("LOG",
                                 f"   ⚠️ {result['error_msg']}",
                                 "WARNING")
                    except Exception as e:
                        result["error_msg"] = f"删除失败: {e}"
                        callback("LOG",
                                 f"   ⚠️ {result['error_msg']}",
                                 "WARNING")

                result["success"] = True
            else:
                result["error_msg"] = (
                    f"7z 返回码: {proc.returncode}")
                callback("LOG",
                         f"❌ 压缩失败，{result['error_msg']}",
                         "ERROR")
                self._safe_remove(archive_path)

        except FileNotFoundError:
            result["error_msg"] = "7z.exe 未找到"
            callback("LOG", f"❌ {result['error_msg']}", "ERROR")
            self._safe_remove(archive_path)
        except PermissionError:
            result["error_msg"] = "权限不足"
            callback("LOG", f"❌ {result['error_msg']}", "ERROR")
            self._safe_remove(archive_path)
        except Exception as e:
            result["error_msg"] = str(e)
            callback("LOG", f"❌ 异常: {e}", "ERROR")
            self._safe_remove(archive_path)
        finally:
            with self._lock:
                self._processes.pop(item_name, None)

        return result

    @staticmethod
    def _safe_remove(path):
        c = path.replace('\\\\?\\', '') if path else path
        if c and os.path.exists(c):
            try:
                os.remove(c)
            except Exception:
                pass

