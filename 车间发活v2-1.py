import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog, font
import sqlite3
import os
import shutil
import csv
import threading
import datetime
import html
from http.server import BaseHTTPRequestHandler, HTTPServer

# ==========================================
# 1. 数据库管理层 (Model) - 支持平滑升级
# ==========================================
class DatabaseManager:
    def __init__(self, db_name="workshop_mes.db"):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.db_path = os.path.join(script_dir, db_name)
        self.init_db()

    def get_conn(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def init_db(self):
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS workers 
                         (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, status TEXT DEFAULT 'active')''')
            c.execute('''CREATE TABLE IF NOT EXISTS task_types 
                         (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE)''')
            
            # 创建 logs 表（包含所有新字段）
            c.execute('''CREATE TABLE IF NOT EXISTS logs 
                         (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, task_type TEXT, 
                          task_name TEXT, worker_name TEXT, quantity INTEGER DEFAULT 1,
                          status INTEGER DEFAULT 0, difficulty REAL DEFAULT 1.0, 
                          completed_qty INTEGER DEFAULT 0, is_rework INTEGER DEFAULT 0, 
                          original_log_id INTEGER, priority TEXT DEFAULT '普通')''')
            
            c.execute('''CREATE TABLE IF NOT EXISTS pointers 
                         (task_type TEXT PRIMARY KEY, next_worker_id INTEGER DEFAULT 1)''')
            conn.commit()
            
            # 平滑升级：为旧版本数据库补充缺失的字段
            self.upgrade_db(conn)

    def upgrade_db(self, conn):
        """检查并添加缺失的字段，保证旧数据不丢失"""
        c = conn.cursor()
        c.execute("PRAGMA table_info(logs)")
        existing_cols = [row[1] for row in c.fetchall()]
        
        new_cols = {
            "status": "INTEGER DEFAULT 0",
            "difficulty": "REAL DEFAULT 1.0",
            "completed_qty": "INTEGER DEFAULT 0",
            "is_rework": "INTEGER DEFAULT 0",
            "original_log_id": "INTEGER",
            "priority": "TEXT DEFAULT '普通'"
        }
        
        for col, col_type in new_cols.items():
            if col not in existing_cols:
                try:
                    conn.execute(f"ALTER TABLE logs ADD COLUMN {col} {col_type}")
                except: pass
        conn.commit()

    def backup_db(self, dest_path): shutil.copy2(self.db_path, dest_path)
    def restore_db(self, src_path): shutil.copy2(src_path, self.db_path)


# ==========================================
# 2. 业务逻辑层 (Controller)
# ==========================================
class TaskDispatcher:
    def __init__(self, db: DatabaseManager):
        self.db = db

    # --- 基础管理 ---
    def get_workers(self):
        with self.db.get_conn() as conn: return conn.execute("SELECT id, name, status FROM workers").fetchall()
    def add_worker(self, name):
        with self.db.get_conn() as conn:
            try: conn.execute("INSERT INTO workers (name) VALUES (?)", (name,)); return True
            except sqlite3.IntegrityError: return False
    def remove_worker(self, worker_id):
        with self.db.get_conn() as conn: conn.execute("DELETE FROM workers WHERE id=?", (worker_id,))
    def toggle_worker_status(self, worker_id):
        with self.db.get_conn() as conn:
            status = conn.execute("SELECT status FROM workers WHERE id=?", (worker_id,)).fetchone()[0]
            conn.execute("UPDATE workers SET status=? WHERE id=?", ('leave' if status=='active' else 'active', worker_id))
    def get_task_types(self):
        with self.db.get_conn() as conn: return [r[0] for r in conn.execute("SELECT name FROM task_types").fetchall()]
    def add_task_type(self, name):
        with self.db.get_conn() as conn:
            try:
                conn.execute("INSERT INTO task_types (name) VALUES (?)", (name,))
                conn.execute("INSERT INTO pointers (task_type) VALUES (?)", (name,))
                return True
            except sqlite3.IntegrityError: return False
    def remove_task_type(self, name):
        with self.db.get_conn() as conn:
            conn.execute("DELETE FROM task_types WHERE name=?", (name,))
            conn.execute("DELETE FROM pointers WHERE task_type=?", (name,))

    # --- 发活逻辑 ---
    def get_recommended_worker(self, task_type):
        with self.db.get_conn() as conn:
            workers = conn.execute("SELECT id, name, status FROM workers WHERE status='active'").fetchall()
            if not workers: return None
            next_id = conn.execute("SELECT next_worker_id FROM pointers WHERE task_type=?", (task_type,)).fetchone()[0]
            for w in workers:
                if w[0] >= next_id: return w[1]
            return workers[0][1]

    def confirm_dispatch(self, task_type, task_name, worker_name, quantity, priority, is_rework=0, original_id=None):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.db.get_conn() as conn:
            conn.execute("""INSERT INTO logs (timestamp, task_type, task_name, worker_name, quantity, 
                         priority, is_rework, original_log_id, status, completed_qty) 
                         VALUES (?,?,?,?,?,?,?,?,0,?)""", 
                         (timestamp, task_type, task_name, worker_name, quantity, priority, is_rework, original_id, quantity))
            
            c = conn.execute("SELECT id FROM workers WHERE name=?", (worker_name,))
            row = c.fetchone()
            if row:
                next_id = row[0] + 1
                conn.execute("UPDATE pointers SET next_worker_id=? WHERE task_type=?", (next_id, task_type))
            return timestamp

    # --- 报工逻辑 ---
    def get_pending_tasks(self, worker_name):
        """获取某员工名下未完成的活"""
        with self.db.get_conn() as conn:
            return conn.execute("SELECT id, task_name, task_type, quantity FROM logs WHERE worker_name=? AND status=0", (worker_name,)).fetchall()

    def report_work(self, log_id, completed_qty, difficulty):
        with self.db.get_conn() as conn:
            conn.execute("UPDATE logs SET status=1, completed_qty=?, difficulty=? WHERE id=?", 
                         (completed_qty, difficulty, log_id))

    # --- 查询与统计 ---
    def get_logs_paged(self, page, page_size):
        with self.db.get_conn() as conn:
            offset = (page - 1) * page_size
            total = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
            rows = conn.execute("""SELECT id, timestamp, task_type, task_name, worker_name, quantity, 
                                   status, priority, is_rework, original_log_id FROM logs 
                                   ORDER BY id DESC LIMIT ? OFFSET ?""", (page_size, offset)).fetchall()
            return rows, total

    def get_all_logs_for_export(self):
        with self.db.get_conn() as conn:
            return conn.execute("SELECT id, timestamp, task_type, task_name, worker_name, quantity, status, priority, is_rework FROM logs ORDER BY id DESC").fetchall()

    def get_statistics(self, start_t, end_t, worker):
        with self.db.get_conn() as conn:
            query = """SELECT COUNT(*), SUM(quantity), 
                       SUM(CASE WHEN status=1 THEN completed_qty ELSE 0 END),
                       SUM(CASE WHEN status=1 THEN completed_qty * difficulty ELSE 0 END)
                       FROM logs WHERE timestamp >= ? AND timestamp <= ?"""
            params = [start_t, end_t]
            if worker != "全部":
                query += " AND worker_name = ?"
                params.append(worker)
            
            row = conn.execute(query, params).fetchone()
            return {
                "dispatch_count": row[0] or 0,
                "dispatch_qty": row[1] or 0,
                "completed_qty": row[2] or 0,
                "total_difficulty": row[3] or 0
            }

    def update_log_worker(self, log_id, new_worker):
        with self.db.get_conn() as conn: conn.execute("UPDATE logs SET worker_name=? WHERE id=?", (new_worker, log_id))


# ==========================================
# 3. Web 看板服务 (支持紧急高亮)
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
        
        # 获取最新 20 条，包含优先级和状态
        c.execute("SELECT timestamp, task_type, worker_name, quantity, priority, status, is_rework FROM logs ORDER BY id DESC LIMIT 20")
        recent_logs = c.fetchall()
        conn.close()

        log_rows = ""
        for l in recent_logs:
            # 紧急高亮逻辑
            row_class = ""
            prefix = ""
            if l[4] == '特急': row_class = "class='critical'"
            elif l[4] == '紧急': row_class = "class='urgent'"
            
            if l[6]: prefix = "🔄" # 返工标记
            status_icon = "✅" if l[5] else "⏳"
            
            safe_vals = [html.escape(str(v)) for v in l[:4]]
            log_rows += f"<tr {row_class}><td>{safe_vals[0]}</td><td>{prefix}{safe_vals[1]}</td><td>{safe_vals[2]}</td><td>{safe_vals[3]}</td><td>{l[4]}</td><td>{status_icon}</td></tr>"
        
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
            /* 紧急高亮 CSS */
            .urgent {{ background-color: #ffcccc !important; font-weight: bold; }}
            .critical {{ background-color: #ff4d4d !important; color: white; font-weight: bold; animation: blink 1s infinite; }}
            @keyframes blink {{ 50% {{ opacity: 0.6; }} }}
        </style></head>
        <body>
            <h1 style="text-align:center;">🏭 车间生产实时看板</h1>
            <div class="card">
                <h3>📅 今日 ({today}) 生产概况</h3>
                <div class="stat">
                    <div><h2>{today_count or 0}</h2><p>总发活任务</p></div>
                    <div><h2>{today_qty or 0}</h2><p>总发活数量</p></div>
                </div>
            </div>
            <div class="card">
                <h3>📜 最新记录 (Top 20) | ⏳进行中 ✅已完工 | 🔄返工</h3>
                <table>
                    <tr><th>时间</th><th>类型</th><th>接收人</th><th>数量</th><th>优先级</th><th>状态</th></tr>
                    {log_rows}
                </table>
            </div>
            <script>setTimeout(()=>location.reload(), 5000);</script>
        </body></html>
        """
    def log_message(self, format, *args): pass


# ==========================================
# 4. 界面交互层 (View)
# ==========================================
class DispatchApp:
    def __init__(self, root):
        self.root = root
        self.root.title("车间智能 MES 调度终端 (闭环追溯版)")
        self.root.geometry("1150x800")
        
        self.db = DatabaseManager()
        self.dispatcher = TaskDispatcher(self.db)
        
        self.current_page = 1
        self.page_size = 50
        self.total_logs = 0
        self.is_large_font = False
        self.web_server = None
        self.web_server_running = False
        
        self.setup_ui()
        self.init_default_data()
        self.refresh_all_ui()

    def init_default_data(self):
        if not self.dispatcher.get_workers():
            for name in ["张三", "李四", "王五", "赵六", "孙七"]: self.dispatcher.add_worker(name)
        if not self.dispatcher.get_task_types():
            for t in ["类型A (常规件)", "类型B (急件)", "类型C (返工件)", "类型D (新品件)"]: self.dispatcher.add_task_type(t)

    def setup_ui(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("TLabel", font=("微软雅黑", 10))
        style.configure("TButton", font=("微软雅黑", 10), padding=5)
        style.configure("TNotebook.Tab", font=("微软雅黑", 11, "bold"), padding=[15, 5])
        style.configure("Big.TLabel", font=("微软雅黑", 24, "bold"), foreground="#2980b9")

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # ================= Tab 1: 日常发活与报工 =================
        tab1 = ttk.Frame(self.notebook)
        self.notebook.add(tab1, text="📝 发活与报工")

        toolbar = ttk.Frame(tab1)
        toolbar.pack(fill=tk.X, pady=5)
        ttk.Button(toolbar, text="🔠 大字号", command=self.toggle_font).pack(side=tk.RIGHT, padx=5)
        ttk.Button(toolbar, text="🌐 Web看板", command=self.toggle_web_server).pack(side=tk.RIGHT, padx=5)
        self.web_status_lbl = ttk.Label(toolbar, text="Web: 关", foreground="gray")
        self.web_status_lbl.pack(side=tk.RIGHT, padx=5)

        # 发活与报工 左右分栏
        mid_frame = ttk.Frame(tab1)
        mid_frame.pack(fill=tk.X, pady=5)

        # 左侧：发活
        dispatch_frame = ttk.LabelFrame(mid_frame, text="发活操作台", padding=10)
        dispatch_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        
        row1 = ttk.Frame(dispatch_frame)
        row1.pack(fill=tk.X, pady=3)
        ttk.Label(row1, text="类型:").pack(side=tk.LEFT)
        self.type_combo = ttk.Combobox(row1, state="readonly", width=12)
        self.type_combo.pack(side=tk.LEFT, padx=5)
        self.type_combo.bind("<<ComboboxSelected>>", lambda e: self.update_recommended())
        ttk.Label(row1, text="优先级:").pack(side=tk.LEFT, padx=(10,0))
        self.prio_combo = ttk.Combobox(row1, values=["普通", "紧急", "特急"], state="readonly", width=6)
        self.prio_combo.pack(side=tk.LEFT, padx=5)
        self.prio_combo.current(0)

        row2 = ttk.Frame(dispatch_frame)
        row2.pack(fill=tk.X, pady=3)
        ttk.Label(row2, text="单号:").pack(side=tk.LEFT)
        self.task_entry = ttk.Entry(row2, width=15)
        self.task_entry.pack(side=tk.LEFT, padx=5)
        self.task_entry.bind('<Return>', lambda e: self.do_confirm())
        ttk.Label(row2, text="数量:").pack(side=tk.LEFT, padx=(10,0))
        self.qty_entry = ttk.Entry(row2, width=6)
        self.qty_entry.pack(side=tk.LEFT, padx=5)
        self.qty_entry.insert(0, "1")

        row3 = ttk.Frame(dispatch_frame)
        row3.pack(fill=tk.X, pady=3)
        ttk.Label(row3, text="推荐:").pack(side=tk.LEFT)
        self.rec_label = ttk.Label(row3, text="无", style="Big.TLabel")
        self.rec_label.pack(side=tk.LEFT, padx=5)
        ttk.Label(row3, text="发给:").pack(side=tk.LEFT, padx=(10,0))
        self.target_combo = ttk.Combobox(row3, state="readonly", width=10)
        self.target_combo.pack(side=tk.LEFT, padx=5)
        ttk.Button(row3, text="✅ 确认发活", command=self.do_confirm).pack(side=tk.LEFT, padx=10)

        # 右侧：报工
        report_frame = ttk.LabelFrame(mid_frame, text="报工操作台 (完工登记)", padding=10)
        report_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 0))
        
        r_row1 = ttk.Frame(report_frame)
        r_row1.pack(fill=tk.X, pady=3)
        ttk.Label(r_row1, text="员工:").pack(side=tk.LEFT)
        self.rep_worker_combo = ttk.Combobox(r_row1, state="readonly", width=10)
        self.rep_worker_combo.pack(side=tk.LEFT, padx=5)
        self.rep_worker_combo.bind("<<ComboboxSelected>>", lambda e: self.load_pending_tasks())

        r_row2 = ttk.Frame(report_frame)
        r_row2.pack(fill=tk.X, pady=3)
        ttk.Label(r_row2, text="任务:").pack(side=tk.LEFT)
        self.rep_task_combo = ttk.Combobox(r_row2, state="readonly", width=18)
        self.rep_task_combo.pack(side=tk.LEFT, padx=5)

        r_row3 = ttk.Frame(report_frame)
        r_row3.pack(fill=tk.X, pady=3)
        ttk.Label(r_row3, text="完工数:").pack(side=tk.LEFT)
        self.rep_qty_entry = ttk.Entry(r_row3, width=6)
        self.rep_qty_entry.pack(side=tk.LEFT, padx=5)
        self.rep_qty_entry.insert(0, "1")
        ttk.Label(r_row3, text="难度:").pack(side=tk.LEFT, padx=(10,0))
        self.rep_diff_entry = ttk.Entry(r_row3, width=5)
        self.rep_diff_entry.pack(side=tk.LEFT, padx=5)
        self.rep_diff_entry.insert(0, "1.0")

        ttk.Button(report_frame, text="✅ 确认报工", command=self.do_report).pack(fill=tk.X, pady=10)

        # 历史记录表格
        log_frame = ttk.LabelFrame(tab1, text="历史记录 (双击状态列报工 | 右键生成返工单)", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        cols = ("ID", "时间", "类型", "单号", "接收人", "数量", "状态", "优先级")
        self.tree = ttk.Treeview(log_frame, columns=cols, show="headings", height=12)
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, anchor=tk.CENTER, width=100)
        
        # 配置行高亮 Tag
        self.tree.tag_configure('urgent', background='#ffcccc')
        self.tree.tag_configure('critical', background='#ff9999')
        self.tree.tag_configure('rework', background='#e6ccff') # 返工淡紫色

        self.tree.bind("<Double-1>", self.on_tree_double_click)
        self.tree.bind("<Button-3>", self.on_tree_right_click) # 右键菜单
        
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
        ttk.Button(page_frame, text="📊 导出全部 CSV", command=self.export_csv).pack(side=tk.RIGHT, padx=5)


        # ================= Tab 2: 管理与统计 =================
        tab2 = ttk.Frame(self.notebook)
        self.notebook.add(tab2, text="⚙️ 管理与统计")

        left_frame = ttk.Frame(tab2, width=350)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left_frame.pack_propagate(False)

        w_frame = ttk.LabelFrame(left_frame, text="人员 (双击切状态)", padding=10)
        w_frame.pack(fill=tk.X, pady=5)
        add_w = ttk.Frame(w_frame)
        add_w.pack(fill=tk.X)
        self.new_w_entry = ttk.Entry(add_w, width=15)
        self.new_w_entry.pack(side=tk.LEFT, padx=(0,5))
        ttk.Button(add_w, text="➕ 添加", command=self.add_worker).pack(side=tk.LEFT)
        self.w_listbox = tk.Listbox(w_frame, height=8, font=("微软雅黑", 10))
        self.w_listbox.pack(fill=tk.X, pady=5)
        self.w_listbox.bind("<Double-1>", self.toggle_status)
        ttk.Button(w_frame, text="🗑️ 删除", command=self.remove_worker).pack(fill=tk.X)

        t_frame = ttk.LabelFrame(left_frame, text="任务类型", padding=10)
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
        ttk.Button(sys_frame, text="💾 备份", command=self.backup_db).pack(fill=tk.X, pady=2)
        ttk.Button(sys_frame, text="📂 恢复", command=self.restore_db).pack(fill=tk.X, pady=2)

        right_frame = ttk.Frame(tab2)
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        stat_frame = ttk.LabelFrame(right_frame, text="多维度数据统计 (含完工与难度)", padding=10)
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
        if types and self.type_combo.get() not in types: self.type_combo.current(0)
        
        workers = self.dispatcher.get_workers()
        w_names = [w[1] for w in workers]
        self.target_combo['values'] = w_names
        self.rep_worker_combo['values'] = w_names
        self.s_worker['values'] = ["全部"] + w_names
        self.s_worker.current(0)

        self.w_listbox.delete(0, tk.END)
        for w in workers:
            status_str = "[请假]" if w[2] == 'leave' else "[在岗]"
            self.w_listbox.insert(tk.END, f"{w[1]} {status_str}")
            
        self.t_listbox.delete(0, tk.END)
        for t in types: self.t_listbox.insert(tk.END, t)

        self.update_recommended()
        self.load_logs_page()

    def update_recommended(self):
        t = self.type_combo.get()
        rec = self.dispatcher.get_recommended_worker(t) if t else None
        self.rec_label.config(text=rec if rec else "无")
        if rec: self.target_combo.set(rec)

    def do_confirm(self):
        t_type = self.type_combo.get()
        t_name = self.task_entry.get().strip() or "无单号"
        w_name = self.target_combo.get()
        priority = self.prio_combo.get()
        try: qty = int(self.qty_entry.get()); 
        except: qty = 1

        if not w_name: messagebox.showwarning("提示", "没有可分配的员工！"); return

        self.dispatcher.confirm_dispatch(t_type, t_name, w_name, qty, priority)
        self.task_entry.delete(0, tk.END)
        self.task_entry.focus_set() 
        self.current_page = 1
        self.load_logs_page()
        self.update_recommended()

    def load_pending_tasks(self):
        """加载员工名下未完成的活到报工下拉框"""
        w = self.rep_worker_combo.get()
        if not w: return
        tasks = self.dispatcher.get_pending_tasks(w)
        self.rep_task_combo['values'] = [f"[{t[0]}] {t[2]} - {t[1]} (发{t[3]}件)" for t in tasks]
        self.rep_task_combo._task_ids = [t[0] for t in tasks] # 隐藏存储 ID

    def do_report(self):
        w = self.rep_worker_combo.get()
        sel = self.rep_task_combo.current()
        if sel == -1 or not w:
            messagebox.showwarning("提示", "请选择员工和对应的任务！"); return
        
        log_id = self.rep_task_combo._task_ids[sel]
        try: 
            qty = int(self.rep_qty_entry.get())
            diff = float(self.rep_diff_entry.get())
        except:
            messagebox.showwarning("提示", "数量或难度格式错误！"); return

        self.dispatcher.report_work(log_id, qty, diff)
        messagebox.showinfo("成功", f"报工成功！完工 {qty} 件，难度系数 {diff}")
        self.load_pending_tasks()
        self.load_logs_page()

    def load_logs_page(self):
        for i in self.tree.get_children(): self.tree.delete(i)
        rows, total = self.dispatcher.get_logs_paged(self.current_page, self.page_size)
        self.total_logs = total
        
        for r in rows:
            # r: id, time, type, name, worker, qty, status, priority, is_rework, orig_id
            status_str = "✅已完工" if r[6] == 1 else "⏳进行中"
            rework_str = "🔄返工" if r[8] else ""
            display_name = f"{r[4]} {rework_str}"
            
            vals = (r[0], r[1], r[2], r[3], display_name, r[5], status_str, r[7])
            
            # 决定行标签 (高亮)
            tags = ()
            if r[8]: tags = ('rework',) # 返工优先
            elif r[7] == '特急': tags = ('critical',)
            elif r[7] == '紧急': tags = ('urgent',)
            
            self.tree.insert("", tk.END, values=vals, tags=tags)
        
        total_pages = max(1, (total + self.page_size - 1) // self.page_size)
        self.page_lbl.config(text=f"第 {self.current_page} 页 / 共 {total_pages} 页 (共 {total} 条)")

    def on_tree_double_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell": return
        col = self.tree.identify_column(event.x)
        row_id = self.tree.identify_row(event.y)
        vals = self.tree.item(row_id, 'values')
        
        if col == "#7":  # 状态列 -> 快捷报工
            if vals[6] == "⏳进行中":
                log_db_id = vals[0]
                worker = vals[4].split(" ")[0]
                qty = simpledialog.askinteger("快捷报工", f"为 {worker} 报工\n请输入完工数量:", initialvalue=vals[5])
                if qty:
                    self.dispatcher.report_work(log_db_id, qty, 1.0)
                    self.load_logs_page()
        elif col == "#5": # 接收人列 -> 修改人员
            log_db_id = vals[0]
            current_w = vals[4].split(" ")[0]
            new_w = simpledialog.askstring("修改接收人", f"当前: {current_w}\n输入新名字:", initialvalue=current_w)
            if new_w and new_w != current_w:
                self.dispatcher.update_log_worker(log_db_id, new_w)
                self.load_logs_page()

    def on_tree_right_click(self, event):
        """右键菜单：生成返工单"""
        row_id = self.tree.identify_row(event.y)
        if not row_id: return
        vals = self.tree.item(row_id, 'values')
        
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="🔄 生成返工单 (关联此单)", command=lambda: self.create_rework(vals))
        menu.post(event.x_root, event.y_root)

    def create_rework(self, original_vals):
        orig_id = original_vals[0]
        orig_type = original_vals[2]
        orig_name = original_vals[3]
        orig_worker = original_vals[4].split(" ")[0]
        
        # 弹窗确认返工信息
        rework_name = f"{orig_name}-R"
        new_name = simpledialog.askstring("生成返工单", f"原单号: {orig_name}\n请输入返工单号:", initialvalue=rework_name)
        if not new_name: return
        
        self.dispatcher.confirm_dispatch(orig_type, new_name, orig_worker, 1, "紧急", is_rework=1, original_id=orig_id)
        messagebox.showinfo("成功", f"返工单 {new_name} 已发给 {orig_worker}，优先级设为【紧急】！")
        self.current_page = 1
        self.load_logs_page()

    # --- 其他基础方法 ---
    def prev_page(self):
        if self.current_page > 1: self.current_page -= 1; self.load_logs_page()
    def next_page(self):
        total_pages = max(1, (self.total_logs + self.page_size - 1) // self.page_size)
        if self.current_page < total_pages: self.current_page += 1; self.load_logs_page()
    def add_worker(self):
        name = self.new_w_entry.get().strip()
        if self.dispatcher.add_worker(name): self.new_w_entry.delete(0, tk.END); self.refresh_all_ui()
        else: messagebox.showwarning("提示", "人员已存在或为空")
    def remove_worker(self):
        sel = self.w_listbox.curselection()
        if not sel: return
        name = self.w_listbox.get(sel[0]).split(" ")[0]
        if messagebox.askyesno("确认", f"删除 {name}？"):
            self.dispatcher.remove_worker(self.dispatcher.get_workers()[sel[0]][0])
            self.refresh_all_ui()
    def toggle_status(self, event):
        sel = self.w_listbox.curselection()
        if not sel: return
        self.dispatcher.toggle_worker_status(self.dispatcher.get_workers()[sel[0]][0])
        self.refresh_all_ui()
    def add_task_type(self):
        name = self.new_t_entry.get().strip()
        if self.dispatcher.add_task_type(name): self.new_t_entry.delete(0, tk.END); self.refresh_all_ui()
        else: messagebox.showwarning("提示", "类型已存在或为空")
    def remove_task_type(self):
        sel = self.t_listbox.curselection()
        if not sel: return
        if messagebox.askyesno("确认", f"删除 {self.t_listbox.get(sel[0])}？"):
            self.dispatcher.remove_task_type(self.t_listbox.get(sel[0]))
            self.refresh_all_ui()

    def do_statistics(self):
        s, e, w = self.s_start.get(), self.s_end.get(), self.s_worker.get()
        stats = self.dispatcher.get_statistics(s, e, w)
        self.stat_text.config(state=tk.NORMAL)
        self.stat_text.delete(1.0, tk.END)
        self.stat_text.insert(tk.END, f"🎯 统计结果 ({w})：\n")
        self.stat_text.insert(tk.END, f"📦 总发活任务数：{stats['dispatch_count']} 个\n")
        self.stat_text.insert(tk.END, f"📦 总发活数量：{stats['dispatch_qty']} 件\n")
        self.stat_text.insert(tk.END, f"✅ 总完工数量：{stats['completed_qty']} 件\n")
        self.stat_text.insert(tk.END, f"⏱️ 总难度工时：{stats['total_difficulty']:.1f} (完工数×难度系数)\n")
        self.stat_text.insert(tk.END, "="*50 + "\n")
        self.stat_text.config(state=tk.DISABLED)

    def export_csv(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=f"发活记录_{datetime.datetime.now().strftime('%Y%m%d')}.csv")
        if path:
            rows = self.dispatcher.get_all_logs_for_export()
            with open(path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(["ID", "时间", "类型", "单号", "接收人", "数量", "状态", "优先级", "是否返工"])
                for r in rows:
                    writer.writerow([r[0], r[1], r[2], r[3], r[4], r[5], "已完工" if r[6] else "进行中", r[7], "是" if r[8] else "否"])
            messagebox.showinfo("成功", f"导出成功！共 {len(rows)} 条。")

    def backup_db(self):
        path = filedialog.asksaveasfilename(defaultextension=".db", initialfile=f"backup_{datetime.datetime.now().strftime('%Y%m%d')}.db")
        if path: self.db.backup_db(path); messagebox.showinfo("成功", "备份成功！")
    def restore_db(self):
        path = filedialog.askopenfilename(filetypes=[("Database", "*.db")])
        if path and messagebox.askyesno("警告", "恢复将覆盖当前数据！确定？"):
            self.db.restore_db(path); messagebox.showinfo("成功", "恢复成功！请重启。"); self.root.destroy()

    def toggle_font(self):
        self.is_large_font = not self.is_large_font
        size = 14 if self.is_large_font else 10
        style = ttk.Style()
        style.configure(".", font=("微软雅黑", size))
        style.configure("Big.TLabel", font=("微软雅黑", 32 if self.is_large_font else 24, "bold"))
        messagebox.showinfo("提示", "字号已切换！")

    def toggle_web_server(self):
        if not self.web_server_running:
            WebDashboardHandler.db_path = self.db.db_path
            try:
                self.web_server = HTTPServer(('0.0.0.0', 8080), WebDashboardHandler)
            except OSError:
                messagebox.showerror("错误", "端口 8080 被占用！"); return
            threading.Thread(target=self.web_server.serve_forever, daemon=True).start()
            self.web_server_running = True
            self.web_status_lbl.config(text="Web: 开(8080)", foreground="green")
            messagebox.showinfo("成功", "Web 看板已开启！\n手机访问: http://本机IP:8080")
        else:
            threading.Thread(target=self._stop_server, daemon=True).start()
            self.web_server_running = False
            self.web_status_lbl.config(text="Web: 关", foreground="gray")
    def _stop_server(self):
        if self.web_server: self.web_server.shutdown(); self.web_server.server_close()


if __name__ == "__main__":
    root = tk.Tk()
    app = DispatchApp(root)
    root.mainloop()