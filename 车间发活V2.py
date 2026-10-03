import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog, font
import sqlite3
import os
import shutil
import csv
import threading
import datetime
import html  # 用于 Web 看板防注入
from http.server import BaseHTTPRequestHandler, HTTPServer

# ==========================================
# 1. 数据库管理层 (Model)
# ==========================================
class DatabaseManager:
    def __init__(self, db_name="workshop_mes.db"):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.db_path = os.path.join(script_dir, db_name)
        self.init_db()

    def get_conn(self):
        # 允许跨线程访问（虽然目前主要在单线程，但 Web 端需要）
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def init_db(self):
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS workers 
                         (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, status TEXT DEFAULT 'active')''')
            c.execute('''CREATE TABLE IF NOT EXISTS task_types 
                         (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE)''')
            c.execute('''CREATE TABLE IF NOT EXISTS logs 
                         (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, task_type TEXT, 
                          task_name TEXT, worker_name TEXT, quantity INTEGER DEFAULT 1)''')
            c.execute('''CREATE TABLE IF NOT EXISTS pointers 
                         (task_type TEXT PRIMARY KEY, next_worker_id INTEGER DEFAULT 1)''')
            conn.commit()

    def backup_db(self, dest_path):
        shutil.copy2(self.db_path, dest_path)

    def restore_db(self, src_path):
        shutil.copy2(src_path, self.db_path)


# ==========================================
# 2. 业务逻辑层 (Controller)
# ==========================================
class TaskDispatcher:
    def __init__(self, db: DatabaseManager):
        self.db = db

    def get_workers(self):
        with self.db.get_conn() as conn:
            return conn.execute("SELECT id, name, status FROM workers").fetchall()

    def add_worker(self, name):
        with self.db.get_conn() as conn:
            try:
                conn.execute("INSERT INTO workers (name) VALUES (?)", (name,))
                return True
            except sqlite3.IntegrityError:
                return False

    def remove_worker(self, worker_id):
        with self.db.get_conn() as conn:
            conn.execute("DELETE FROM workers WHERE id=?", (worker_id,))

    def toggle_worker_status(self, worker_id):
        with self.db.get_conn() as conn:
            c = conn.execute("SELECT status FROM workers WHERE id=?", (worker_id,))
            status = c.fetchone()[0]
            new_status = 'leave' if status == 'active' else 'active'
            conn.execute("UPDATE workers SET status=? WHERE id=?", (new_status, worker_id))

    def get_task_types(self):
        with self.db.get_conn() as conn:
            return [row[0] for row in conn.execute("SELECT name FROM task_types").fetchall()]

    def add_task_type(self, name):
        with self.db.get_conn() as conn:
            try:
                conn.execute("INSERT INTO task_types (name) VALUES (?)", (name,))
                conn.execute("INSERT INTO pointers (task_type) VALUES (?)", (name,))
                return True
            except sqlite3.IntegrityError:
                return False

    def remove_task_type(self, name):
        with self.db.get_conn() as conn:
            conn.execute("DELETE FROM task_types WHERE name=?", (name,))
            conn.execute("DELETE FROM pointers WHERE task_type=?", (name,))

    def get_recommended_worker(self, task_type):
        with self.db.get_conn() as conn:
            workers = conn.execute("SELECT id, name, status FROM workers WHERE status='active'").fetchall()
            if not workers: return None
            
            c = conn.execute("SELECT next_worker_id FROM pointers WHERE task_type=?", (task_type,))
            row = c.fetchone()
            next_id = row[0] if row else 1
            
            for w in workers:
                if w[0] >= next_id:
                    return w[1]
            return workers[0][1]

    def confirm_dispatch(self, task_type, task_name, worker_name, quantity):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.db.get_conn() as conn:
            conn.execute("INSERT INTO logs (timestamp, task_type, task_name, worker_name, quantity) VALUES (?,?,?,?,?)",
                         (timestamp, task_type, task_name, worker_name, quantity))
            
            c = conn.execute("SELECT id FROM workers WHERE name=?", (worker_name,))
            row = c.fetchone()
            if row:
                next_id = row[0] + 1
                conn.execute("UPDATE pointers SET next_worker_id=? WHERE task_type=?", (next_id, task_type))
            return timestamp

    def update_log_worker(self, log_id, new_worker):
        with self.db.get_conn() as conn:
            conn.execute("UPDATE logs SET worker_name=? WHERE id=?", (new_worker, log_id))

    def get_logs_paged(self, page, page_size):
        with self.db.get_conn() as conn:
            offset = (page - 1) * page_size
            total = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
            rows = conn.execute("SELECT id, timestamp, task_type, task_name, worker_name, quantity FROM logs ORDER BY id DESC LIMIT ? OFFSET ?", 
                                (page_size, offset)).fetchall()
            return rows, total

    def get_all_logs_for_export(self):
        with self.db.get_conn() as conn:
            return conn.execute("SELECT id, timestamp, task_type, task_name, worker_name, quantity FROM logs ORDER BY id DESC").fetchall()

    def get_statistics(self, start_t, end_t, worker):
        with self.db.get_conn() as conn:
            query = "SELECT COUNT(*), SUM(quantity) FROM logs WHERE timestamp >= ? AND timestamp <= ?"
            params = [start_t, end_t]
            if worker != "全部":
                query += " AND worker_name = ?"
                params.append(worker)
            
            row = conn.execute(query, params).fetchone()
            count = row[0] or 0
            total_qty = row[1] or 0
            
            details = conn.execute("SELECT timestamp, task_type, task_name, worker_name, quantity FROM logs WHERE timestamp >= ? AND timestamp <= ? ORDER BY id DESC", 
                                   (start_t, end_t)).fetchall()
            if worker != "全部":
                details = [d for d in details if d[3] == worker]
                
            return count, total_qty, details


# ==========================================
# 3. Web 看板服务
# ==========================================
class WebDashboardHandler(BaseHTTPRequestHandler):
    db_path = "" 
    
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(self.generate_html().encode('utf-8'))

    def generate_html(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        c.execute("SELECT COUNT(*), SUM(quantity) FROM logs WHERE timestamp LIKE ?", (f"{today}%",))
        today_count, today_qty = c.fetchone()
        
        c.execute("SELECT timestamp, task_type, worker_name, quantity FROM logs ORDER BY id DESC LIMIT 20")
        recent_logs = c.fetchall()
        conn.close()

        # 修复 BUG 5：使用 html.escape 防止特殊字符破坏网页
        safe_logs = [(html.escape(str(l[0])), html.escape(str(l[1])), html.escape(str(l[2])), l[3]) for l in recent_logs]
        log_rows = "".join([f"<tr><td>{l[0]}</td><td>{l[1]}</td><td>{l[2]}</td><td>{l[3]}</td></tr>" for l in safe_logs])
        
        return f"""
        <!DOCTYPE html>
        <html><head><meta charset="utf-8"><title>车间实时看板</title>
        <style>
            body {{ font-family: sans-serif; background: #f4f7f6; margin: 0; padding: 20px; }}
            .card {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 20px; }}
            .stat {{ display: flex; justify-content: space-around; text-align: center; }}
            .stat div h2 {{ margin: 0; color: #e74c3c; font-size: 36px; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ border: 1px solid #ddd; padding: 10px; text-align: center; }}
            th {{ background: #34495e; color: white; }}
        </style></head>
        <body>
            <h1 style="text-align:center;">🏭 车间生产实时看板</h1>
            <div class="card">
                <h3>📅 今日 ({today}) 生产概况</h3>
                <div class="stat">
                    <div><h2>{today_count or 0}</h2><p>总任务数</p></div>
                    <div><h2>{today_qty or 0}</h2><p>总生产数量</p></div>
                </div>
            </div>
            <div class="card">
                <h3>📜 最新发活记录 (Top 20)</h3>
                <table><tr><th>时间</th><th>类型</th><th>接收人</th><th>数量</th></tr>{log_rows}</table>
            </div>
            <script>setTimeout(()=>location.reload(), 10000);</script>
        </body></html>
        """

    def log_message(self, format, *args):
        pass


# ==========================================
# 4. 界面交互层 (View)
# ==========================================
class DispatchApp:
    def __init__(self, root):
        self.root = root
        self.root.title("车间智能 MES 调度终端 (完美修复版)")
        self.root.geometry("1100x750")
        
        self.db = DatabaseManager()
        self.dispatcher = TaskDispatcher(self.db)
        
        self.current_page = 1
        self.page_size = 50
        self.total_logs = 0  # 缓存总数，避免频繁查库
        self.is_large_font = False
        self.web_server = None
        self.web_server_running = False
        
        self.setup_ui()
        self.init_default_data()
        self.refresh_all_ui()

    def init_default_data(self):
        """首次运行初始化默认数据"""
        if not self.dispatcher.get_workers():
            for name in ["张三", "李四", "王五", "赵六", "孙七"]:
                self.dispatcher.add_worker(name)
        if not self.dispatcher.get_task_types():
            for t in ["类型A (常规件)", "类型B (急件)", "类型C (返工件)", "类型D (新品件)"]:
                self.dispatcher.add_task_type(t)

    def setup_ui(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("TLabel", font=("微软雅黑", 10))
        style.configure("TButton", font=("微软雅黑", 10), padding=5)
        style.configure("TNotebook.Tab", font=("微软雅黑", 11, "bold"), padding=[15, 5])
        style.configure("Big.TLabel", font=("微软雅黑", 24, "bold"), foreground="#2980b9")

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # ================= Tab 1: 日常发活 =================
        tab1 = ttk.Frame(self.notebook)
        self.notebook.add(tab1, text="📝 日常发活")

        toolbar = ttk.Frame(tab1)
        toolbar.pack(fill=tk.X, pady=5)
        ttk.Button(toolbar, text="🔠 切换大字号模式", command=self.toggle_font).pack(side=tk.RIGHT, padx=5)
        ttk.Button(toolbar, text="🌐 开启/关闭 Web 手机看板", command=self.toggle_web_server).pack(side=tk.RIGHT, padx=5)
        self.web_status_lbl = ttk.Label(toolbar, text="Web看板: 关闭", foreground="gray")
        self.web_status_lbl.pack(side=tk.RIGHT, padx=5)

        dispatch_frame = ttk.LabelFrame(tab1, text="发活操作台 (支持扫码枪直接回车)", padding=10)
        dispatch_frame.pack(fill=tk.X, pady=5)

        row1 = ttk.Frame(dispatch_frame)
        row1.pack(fill=tk.X, pady=5)
        ttk.Label(row1, text="类型:").pack(side=tk.LEFT)
        self.type_combo = ttk.Combobox(row1, state="readonly", width=15)
        self.type_combo.pack(side=tk.LEFT, padx=5)
        self.type_combo.bind("<<ComboboxSelected>>", lambda e: self.update_recommended())

        ttk.Label(row1, text="单号/扫码:").pack(side=tk.LEFT, padx=(15,0))
        self.task_entry = ttk.Entry(row1, width=20)
        self.task_entry.pack(side=tk.LEFT, padx=5)
        self.task_entry.bind('<Return>', lambda e: self.do_confirm())

        ttk.Label(row1, text="数量:").pack(side=tk.LEFT, padx=(15,0))
        self.qty_entry = ttk.Entry(row1, width=8)
        self.qty_entry.pack(side=tk.LEFT, padx=5)
        self.qty_entry.insert(0, "1")

        row2 = ttk.Frame(dispatch_frame)
        row2.pack(fill=tk.X, pady=5)
        ttk.Label(row2, text="系统推荐:").pack(side=tk.LEFT)
        self.rec_label = ttk.Label(row2, text="无", style="Big.TLabel")
        self.rec_label.pack(side=tk.LEFT, padx=10)
        
        ttk.Label(row2, text="实际发给:").pack(side=tk.LEFT, padx=(20,0))
        self.target_combo = ttk.Combobox(row2, state="readonly", width=15)
        self.target_combo.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(row2, text="✅ 确认发活", command=self.do_confirm).pack(side=tk.LEFT, padx=20)

        log_frame = ttk.LabelFrame(tab1, text="发活历史记录 (双击'接收人'可修改)", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        cols = ("ID", "时间", "类型", "单号", "接收人", "数量")
        self.tree = ttk.Treeview(log_frame, columns=cols, show="headings", height=15)
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, anchor=tk.CENTER, width=120)
        self.tree.bind("<Double-1>", self.on_tree_double_click)
        
        scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        page_frame = ttk.Frame(tab1)
        page_frame.pack(fill=tk.X, pady=5)
        ttk.Button(page_frame, text="⬅️ 上一页", command=self.prev_page).pack(side=tk.LEFT, padx=5)
        self.page_lbl = ttk.Label(page_frame, text="第 1 页 / 共 1 页")
        self.page_lbl.pack(side=tk.LEFT, padx=20)
        ttk.Button(page_frame, text="下一页 ➡️", command=self.next_page).pack(side=tk.LEFT, padx=5)
        # 修复 BUG 6：改为导出全部数据
        ttk.Button(page_frame, text="📊 导出全部记录 CSV", command=self.export_csv).pack(side=tk.RIGHT, padx=5)

        # ================= Tab 2: 管理与统计 =================
        tab2 = ttk.Frame(self.notebook)
        self.notebook.add(tab2, text="⚙️ 管理与统计")

        left_frame = ttk.Frame(tab2, width=350)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left_frame.pack_propagate(False)

        w_frame = ttk.LabelFrame(left_frame, text="人员管理 (双击切换在岗/请假)", padding=10)
        w_frame.pack(fill=tk.X, pady=5)
        add_w = ttk.Frame(w_frame)
        add_w.pack(fill=tk.X)
        self.new_w_entry = ttk.Entry(add_w, width=15)
        self.new_w_entry.pack(side=tk.LEFT, padx=(0,5))
        ttk.Button(add_w, text="➕ 添加", command=self.add_worker).pack(side=tk.LEFT)
        
        self.w_listbox = tk.Listbox(w_frame, height=8, font=("微软雅黑", 10))
        self.w_listbox.pack(fill=tk.X, pady=5)
        self.w_listbox.bind("<Double-1>", self.toggle_status)
        
        btn_w = ttk.Frame(w_frame)
        btn_w.pack(fill=tk.X)
        ttk.Button(btn_w, text="🗑️ 删除", command=self.remove_worker).pack(side=tk.LEFT, expand=True, fill=tk.X)

        t_frame = ttk.LabelFrame(left_frame, text="任务类型管理", padding=10)
        t_frame.pack(fill=tk.X, pady=5)
        add_t = ttk.Frame(t_frame)
        add_t.pack(fill=tk.X)
        self.new_t_entry = ttk.Entry(add_t, width=15)
        self.new_t_entry.pack(side=tk.LEFT, padx=(0,5))
        ttk.Button(add_t, text="➕ 添加", command=self.add_task_type).pack(side=tk.LEFT)
        
        self.t_listbox = tk.Listbox(t_frame, height=5, font=("微软雅黑", 10))
        self.t_listbox.pack(fill=tk.X, pady=5)
        ttk.Button(t_frame, text="🗑️ 删除", command=self.remove_task_type).pack(fill=tk.X)

        sys_frame = ttk.LabelFrame(left_frame, text="系统维护", padding=10)
        sys_frame.pack(fill=tk.X, pady=5)
        ttk.Button(sys_frame, text="💾 备份数据库", command=self.backup_db).pack(fill=tk.X, pady=2)
        ttk.Button(sys_frame, text="📂 恢复数据库", command=self.restore_db).pack(fill=tk.X, pady=2)

        right_frame = ttk.Frame(tab2)
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        stat_frame = ttk.LabelFrame(right_frame, text="多维度数据统计", padding=10)
        stat_frame.pack(fill=tk.BOTH, expand=True)

        s_row1 = ttk.Frame(stat_frame)
        s_row1.pack(fill=tk.X, pady=5)
        ttk.Label(s_row1, text="开始:").pack(side=tk.LEFT)
        self.s_start = ttk.Entry(s_row1, width=16)
        self.s_start.pack(side=tk.LEFT, padx=5)
        self.s_start.insert(0, datetime.datetime.now().replace(hour=0, minute=0).strftime("%Y-%m-%d %H:%M"))
        
        ttk.Label(s_row1, text="结束:").pack(side=tk.LEFT, padx=(10,0))
        self.s_end = ttk.Entry(s_row1, width=16)
        self.s_end.pack(side=tk.LEFT, padx=5)
        self.s_end.insert(0, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))

        s_row2 = ttk.Frame(stat_frame)
        s_row2.pack(fill=tk.X, pady=5)
        ttk.Label(s_row2, text="员工:").pack(side=tk.LEFT)
        self.s_worker = ttk.Combobox(s_row2, state="readonly", width=12)
        self.s_worker.pack(side=tk.LEFT, padx=5)
        ttk.Button(s_row2, text="📊 开始统计", command=self.do_statistics).pack(side=tk.LEFT, padx=15)

        self.stat_text = tk.Text(stat_frame, font=("微软雅黑", 10), state=tk.DISABLED)
        self.stat_text.pack(fill=tk.BOTH, expand=True, pady=10)

    # ================= 事件与逻辑 =================
    
    def refresh_all_ui(self):
        types = self.dispatcher.get_task_types()
        self.type_combo['values'] = types
        if types and self.type_combo.get() not in types: 
            self.type_combo.current(0)
        
        workers = self.dispatcher.get_workers()
        w_names = [w[1] for w in workers]
        self.target_combo['values'] = w_names
        self.s_worker['values'] = ["全部"] + w_names
        self.s_worker.current(0)

        self.w_listbox.delete(0, tk.END)
        for w in workers:
            status_str = "[请假]" if w[2] == 'leave' else "[在岗]"
            self.w_listbox.insert(tk.END, f"{w[1]} {status_str}")
            
        self.t_listbox.delete(0, tk.END)
        for t in types:
            self.t_listbox.insert(tk.END, t)

        self.update_recommended()
        self.load_logs_page()

    def update_recommended(self):
        t = self.type_combo.get()
        rec = self.dispatcher.get_recommended_worker(t) if t else None
        self.rec_label.config(text=rec if rec else "无可用人员")
        if rec: self.target_combo.set(rec)

    def do_confirm(self):
        t_type = self.type_combo.get()
        t_name = self.task_entry.get().strip() or "无单号"
        w_name = self.target_combo.get()
        
        # 修复 BUG 4：数量合法性校验
        try:
            qty = int(self.qty_entry.get())
            if qty < 1: qty = 1
        except ValueError:
            qty = 1

        if not w_name:
            messagebox.showwarning("提示", "没有可分配的员工！")
            return

        self.dispatcher.confirm_dispatch(t_type, t_name, w_name, qty)
        
        self.task_entry.delete(0, tk.END)
        self.qty_entry.delete(0, tk.END)
        self.qty_entry.insert(0, "1")
        self.task_entry.focus_set() 
        
        self.current_page = 1
        self.load_logs_page()
        self.update_recommended()

    def load_logs_page(self):
        for i in self.tree.get_children(): self.tree.delete(i)
        rows, total = self.dispatcher.get_logs_paged(self.current_page, self.page_size)
        self.total_logs = total  # 缓存总数
        
        for r in rows:
            self.tree.insert("", tk.END, values=r)
        
        total_pages = max(1, (total + self.page_size - 1) // self.page_size)
        self.page_lbl.config(text=f"第 {self.current_page} 页 / 共 {total_pages} 页 (共 {total} 条)")

    def prev_page(self):
        if self.current_page > 1:
            self.current_page -= 1
            self.load_logs_page()

    def next_page(self):
        # 修复 BUG 7：使用缓存的 total_logs，避免重复查库
        total_pages = max(1, (self.total_logs + self.page_size - 1) // self.page_size)
        if self.current_page < total_pages:
            self.current_page += 1
            self.load_logs_page()

    def on_tree_double_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell": return
        col = self.tree.identify_column(event.x)
        row_id = self.tree.identify_row(event.y)
        
        if col == "#5": 
            vals = self.tree.item(row_id, 'values')
            log_db_id = vals[0]
            current_w = vals[4]
            new_w = simpledialog.askstring("修改接收人", f"当前: {current_w}\n输入新名字:", initialvalue=current_w)
            if new_w and new_w != current_w:
                self.dispatcher.update_log_worker(log_db_id, new_w)
                self.load_logs_page()

    def add_worker(self):
        name = self.new_w_entry.get().strip()
        if self.dispatcher.add_worker(name):
            self.new_w_entry.delete(0, tk.END)
            self.refresh_all_ui()
        else: messagebox.showwarning("提示", "人员已存在或为空")

    def remove_worker(self):
        sel = self.w_listbox.curselection()
        if not sel: return
        name = self.w_listbox.get(sel[0]).split(" ")[0]
        if messagebox.askyesno("确认", f"删除 {name}？"):
            w_id = self.dispatcher.get_workers()[sel[0]][0]
            self.dispatcher.remove_worker(w_id)
            self.refresh_all_ui()

    def toggle_status(self, event):
        sel = self.w_listbox.curselection()
        if not sel: return
        w_id = self.dispatcher.get_workers()[sel[0]][0]
        self.dispatcher.toggle_worker_status(w_id)
        self.refresh_all_ui()

    def add_task_type(self):
        name = self.new_t_entry.get().strip()
        if self.dispatcher.add_task_type(name):
            self.new_t_entry.delete(0, tk.END)
            self.refresh_all_ui()
        else: messagebox.showwarning("提示", "类型已存在或为空")

    def remove_task_type(self):
        sel = self.t_listbox.curselection()
        if not sel: return
        name = self.t_listbox.get(sel[0])
        if messagebox.askyesno("确认", f"删除类型 {name}？"):
            self.dispatcher.remove_task_type(name)
            self.refresh_all_ui()

    def do_statistics(self):
        s, e, w = self.s_start.get(), self.s_end.get(), self.s_worker.get()
        count, qty, details = self.dispatcher.get_statistics(s, e, w)
        self.stat_text.config(state=tk.NORMAL)
        self.stat_text.delete(1.0, tk.END)
        self.stat_text.insert(tk.END, f"🎯 统计结果：总任务数 {count} 个，总生产数量 {qty} 件\n")
        self.stat_text.insert(tk.END, "="*50 + "\n")
        for d in details:
            self.stat_text.insert(tk.END, f"[{d[0]}] {d[1]} | {d[3]} | 数量:{d[4]} | 单号:{d[2]}\n")
        self.stat_text.config(state=tk.DISABLED)

    def export_csv(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")], initialfile=f"发活记录_{datetime.datetime.now().strftime('%Y%m%d')}.csv")
        if path:
            rows = self.dispatcher.get_all_logs_for_export()
            with open(path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(["ID", "时间", "类型", "单号", "接收人", "数量"])
                writer.writerows(rows)
            messagebox.showinfo("成功", f"导出成功！共 {len(rows)} 条记录。\n(可用 Excel 打开)")

    def backup_db(self):
        path = filedialog.asksaveasfilename(defaultextension=".db", initialfile=f"backup_{datetime.datetime.now().strftime('%Y%m%d')}.db")
        if path:
            self.db.backup_db(path)
            messagebox.showinfo("成功", "备份成功！")

    def restore_db(self):
        path = filedialog.askopenfilename(filetypes=[("Database", "*.db")])
        if path:
            if messagebox.askyesno("警告", "恢复将覆盖当前所有数据！确定继续？"):
                self.db.restore_db(path)
                messagebox.showinfo("成功", "恢复成功！请重启程序。")
                self.root.destroy()

    # 修复 BUG 3：使用 Style 全局修改 ttk 字体
    def toggle_font(self):
        self.is_large_font = not self.is_large_font
        size = 14 if self.is_large_font else 10
        big_size = 32 if self.is_large_font else 24
        
        style = ttk.Style()
        style.configure(".", font=("微软雅黑", size))
        style.configure("TLabel", font=("微软雅黑", size))
        style.configure("TButton", font=("微软雅黑", size))
        style.configure("Big.TLabel", font=("微软雅黑", big_size, "bold"))
        
        # 非 ttk 控件仍需手动修改
        for w in self.root.winfo_children():
            self._update_widget_font(w, size)
        messagebox.showinfo("提示", "字号已切换！")

    def _update_widget_font(self, widget, size):
        try:
            if isinstance(widget, (tk.Listbox, tk.Text, tk.Entry)):
                widget.configure(font=("微软雅黑", size))
        except: pass
        for child in widget.winfo_children():
            self._update_widget_font(child, size)

    # 修复 BUG 1 & 2：解决卡死与端口冲突
    def toggle_web_server(self):
        if not self.web_server_running:
            WebDashboardHandler.db_path = self.db.db_path
            try:
                self.web_server = HTTPServer(('0.0.0.0', 8080), WebDashboardHandler)
            except OSError:
                messagebox.showerror("错误", "端口 8080 被占用！\n请关闭占用该端口的程序，或在代码中修改端口号。")
                return
            
            self.web_server_thread = threading.Thread(target=self.web_server.serve_forever, daemon=True)
            self.web_server_thread.start()
            self.web_server_running = True
            self.web_status_lbl.config(text="Web看板: 已开启 (端口8080)", foreground="green")
            messagebox.showinfo("成功", "Web 看板已开启！\n手机连接同一 WiFi，浏览器访问:\nhttp://本机IP:8080")
        else:
            # 在后台线程执行 shutdown，防止主线程卡死
            threading.Thread(target=self._stop_server, daemon=True).start()
            self.web_server_running = False
            self.web_status_lbl.config(text="Web看板: 关闭", foreground="gray")

    def _stop_server(self):
        if self.web_server:
            self.web_server.shutdown()
            self.web_server.server_close()


if __name__ == "__main__":
    root = tk.Tk()
    app = DispatchApp(root)
    root.mainloop()