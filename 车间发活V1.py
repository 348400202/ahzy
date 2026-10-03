import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import pandas as pd
from datetime import datetime
import json
import os

# ================= 1. 核心逻辑与持久化层 =================

class TaskDispatcher:
    """车间发活调度器 (带本地持久化)"""
    def __init__(self, task_types):
        self.task_types = task_types
        self.worker_names = []
        self.type_pointers = {t: 0 for t in task_types}
        self.logs = []
        
        # 数据保存路径（与程序同目录下的 data.json）
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.data_file = os.path.join(script_dir, "dispatch_data.json")
        
        self.load_data()  # 启动时加载历史数据

    def save_data(self):
        """将当前状态保存到本地 JSON 文件"""
        data = {
            "workers": self.worker_names,
            "pointers": self.type_pointers,
            "logs": self.logs
        }
        try:
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存数据失败: {e}")

    def load_data(self):
        """从本地 JSON 文件加载历史数据"""
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.worker_names = data.get("workers", [])
                    # 兼容处理：确保所有任务类型都有指针
                    saved_pointers = data.get("pointers", {})
                    self.type_pointers = {t: saved_pointers.get(t, 0) for t in self.task_types}
                    self.logs = data.get("logs", [])
            except Exception as e:
                print(f"加载数据失败，使用默认配置: {e}")

    # --- 人员管理 ---
    def add_worker(self, name):
        if name and name not in self.worker_names:
            self.worker_names.append(name)
            self.save_data()
            return True
        return False

    def remove_worker(self, name):
        if name in self.worker_names:
            self.worker_names.remove(name)
            for t in self.task_types:
                if self.worker_names and self.type_pointers[t] >= len(self.worker_names):
                    self.type_pointers[t] = 0
            self.save_data()
            return True
        return False

    def get_recommended_worker(self, task_type):
        if not self.worker_names: return None
        idx = self.type_pointers[task_type]
        return self.worker_names[idx]

    # --- 发活与修改 ---
    def confirm_dispatch(self, task_type, task_name, target_worker):
        if not target_worker: return None
        
        if target_worker in self.worker_names:
            idx = self.worker_names.index(target_worker)
            self.type_pointers[task_type] = (idx + 1) % len(self.worker_names)
        
        log_entry = {
            "时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "任务类型": task_type,
            "任务单号": task_name,
            "接收员工": target_worker,
        }
        self.logs.append(log_entry)
        self.save_data()  # 自动保存
        return log_entry

    def update_log_worker(self, log_index, new_worker):
        if 0 <= log_index < len(self.logs):
            self.logs[log_index]["接收员工"] = new_worker
            self.save_data()  # 自动保存

    # --- 统计功能 ---
    def get_statistics(self, start_time_str, end_time_str, target_worker="全部"):
        count = 0
        details = []
        try:
            start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M")
            end_dt = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M")
        except ValueError:
            return -1, "时间格式错误，请使用：年-月-日 时:分"

        for log in self.logs:
            log_dt = datetime.strptime(log["时间"], "%Y-%m-%d %H:%M:%S")
            if start_dt <= log_dt <= end_dt:
                if target_worker == "全部" or log["接收员工"] == target_worker:
                    count += 1
                    details.append(f"[{log['时间']}] {log['任务类型']} - {log['任务单号']}")
        return count, details


# ================= 2. 可视化界面层 =================

class DispatchApp:
    def __init__(self, root):
        self.root = root
        self.root.title("车间智能发活管理系统 (专业持久化版)")
        self.root.geometry("1000x650")
        self.root.minsize(900, 600)
        
        task_types = ["类型A (常规件)", "类型B (急件)", "类型C (返工件)", "类型D (新品件)"]
        self.dispatcher = TaskDispatcher(task_types)
        
        # 如果是第一次运行，预置几个初始员工
        if not self.dispatcher.worker_names:
            for name in ["张三", "李四", "王五", "赵六", "孙七"]:
                self.dispatcher.add_worker(name)

        self.setup_ui()
        self.refresh_worker_ui()
        self.load_history_to_tree() # 启动时加载历史记录到表格

    def setup_ui(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("TLabel", font=("微软雅黑", 10))
        style.configure("TButton", font=("微软雅黑", 10), padding=5)
        style.configure("TNotebook.Tab", font=("微软雅黑", 11, "bold"), padding=[15, 5])

        # 使用 Notebook 实现标签页（默认隐藏管理功能）
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # ================= Tab 1: 日常发活 (默认显示) =================
        tab1 = ttk.Frame(self.notebook)
        self.notebook.add(tab1, text="📝 日常发活与记录")

        # 发活操作台
        dispatch_frame = ttk.LabelFrame(tab1, text="发活操作台", padding=10)
        dispatch_frame.pack(fill=tk.X, pady=(0, 10))

        row1 = ttk.Frame(dispatch_frame)
        row1.pack(fill=tk.X, pady=5)
        ttk.Label(row1, text="任务类型:").pack(side=tk.LEFT)
        self.type_combo = ttk.Combobox(row1, values=self.dispatcher.task_types, state="readonly", width=20)
        self.type_combo.pack(side=tk.LEFT, padx=5)
        self.type_combo.current(0)
        self.type_combo.bind("<<ComboboxSelected>>", lambda e: self.update_recommended_worker())

        ttk.Label(row1, text="单号/备注:").pack(side=tk.LEFT, padx=(15,0))
        self.task_entry = ttk.Entry(row1, width=20)
        self.task_entry.pack(side=tk.LEFT, padx=5)
        self.task_entry.bind('<Return>', lambda event: self.do_confirm()) # 回车发活

        row2 = ttk.Frame(dispatch_frame)
        row2.pack(fill=tk.X, pady=5)
        ttk.Label(row2, text="发给(默认推荐):").pack(side=tk.LEFT)
        self.target_worker_combo = ttk.Combobox(row2, state="readonly", width=20)
        self.target_worker_combo.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(row2, text="✅ 确认发活", command=self.do_confirm).pack(side=tk.LEFT, padx=15)
        self.update_recommended_worker()

        # 历史记录与导出
        log_frame = ttk.LabelFrame(tab1, text="发活历史记录 (双击'接收员工'列可修改)", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("时间", "任务类型", "任务单号", "接收员工")
        self.tree = ttk.Treeview(log_frame, columns=columns, show="headings", height=15)
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, anchor=tk.CENTER, width=180)
        
        self.tree.bind("<Double-1>", self.on_tree_double_click)

        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 底部导出按钮
        btn_frame = ttk.Frame(tab1)
        btn_frame.pack(fill=tk.X, pady=5)
        ttk.Button(btn_frame, text="📊 一键导出 Excel 报表", command=self.export_data).pack(side=tk.LEFT, padx=5)
        ttk.Label(btn_frame, text="💡 提示：所有数据已自动实时保存到本地，重启不丢失。", foreground="gray").pack(side=tk.RIGHT, padx=5)


        # ================= Tab 2: 管理与统计 (手动点击显示) =================
        tab2 = ttk.Frame(self.notebook)
        self.notebook.add(tab2, text="⚙️ 人员与统计管理")

        # 左侧：人员管理
        left_frame = ttk.Frame(tab2, width=300)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 15))
        left_frame.pack_propagate(False)

        worker_frame = ttk.LabelFrame(left_frame, text="车间人员管理", padding=10)
        worker_frame.pack(fill=tk.X, pady=(0, 10))

        add_frame = ttk.Frame(worker_frame)
        add_frame.pack(fill=tk.X, pady=5)
        self.new_worker_entry = ttk.Entry(add_frame, width=15)
        self.new_worker_entry.pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(add_frame, text="➕ 添加", command=self.add_worker).pack(side=tk.LEFT)

        self.worker_listbox = tk.Listbox(worker_frame, height=12, font=("微软雅黑", 10))
        self.worker_listbox.pack(fill=tk.X, pady=5)
        
        ttk.Button(worker_frame, text="🗑️ 删除选中人员", command=self.remove_worker).pack(fill=tk.X)

        # 右侧：数据统计
        right_frame = ttk.Frame(tab2)
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        stat_frame = ttk.LabelFrame(right_frame, text="任务数据统计", padding=10)
        stat_frame.pack(fill=tk.BOTH, expand=True)

        time_frame = ttk.Frame(stat_frame)
        time_frame.pack(fill=tk.X, pady=5)
        ttk.Label(time_frame, text="开始:").pack(side=tk.LEFT)
        self.start_time_entry = ttk.Entry(time_frame, width=18)
        self.start_time_entry.pack(side=tk.LEFT, padx=5)
        self.start_time_entry.insert(0, datetime.now().replace(hour=0, minute=0).strftime("%Y-%m-%d %H:%M"))

        ttk.Label(time_frame, text="结束:").pack(side=tk.LEFT, padx=(10,0))
        self.end_time_entry = ttk.Entry(time_frame, width=18)
        self.end_time_entry.pack(side=tk.LEFT, padx=5)
        self.end_time_entry.insert(0, datetime.now().strftime("%Y-%m-%d %H:%M"))

        filter_frame = ttk.Frame(stat_frame)
        filter_frame.pack(fill=tk.X, pady=5)
        ttk.Label(filter_frame, text="选择员工:").pack(side=tk.LEFT)
        self.stat_worker_combo = ttk.Combobox(filter_frame, state="readonly", width=15)
        self.stat_worker_combo.pack(side=tk.LEFT, padx=5)
        ttk.Button(filter_frame, text="📊 开始统计", command=self.do_statistics).pack(side=tk.LEFT, padx=15)

        self.stat_result_text = tk.Text(stat_frame, font=("微软雅黑", 10), state=tk.DISABLED)
        self.stat_result_text.pack(fill=tk.BOTH, expand=True, pady=10)


    # ================= 事件处理逻辑 =================

    def load_history_to_tree(self):
        """启动时从内存加载历史记录到表格（倒序显示，最新的在上面）"""
        for log in reversed(self.dispatcher.logs):
            self.tree.insert("", tk.END, values=(log["时间"], log["任务类型"], log["任务单号"], log["接收员工"]))

    def refresh_worker_ui(self):
        """刷新所有与人员相关的UI组件"""
        self.worker_listbox.delete(0, tk.END)
        for name in self.dispatcher.worker_names:
            self.worker_listbox.insert(tk.END, name)
        
        self.stat_worker_combo['values'] = ["全部"] + self.dispatcher.worker_names
        self.stat_worker_combo.current(0)

        self.target_worker_combo['values'] = self.dispatcher.worker_names
        self.update_recommended_worker()

    def update_recommended_worker(self):
        task_type = self.type_combo.get()
        rec = self.dispatcher.get_recommended_worker(task_type)
        self.target_worker_combo.set(rec if rec else "")

    def add_worker(self):
        name = self.new_worker_entry.get().strip()
        if self.dispatcher.add_worker(name):
            self.new_worker_entry.delete(0, tk.END)
            self.refresh_worker_ui()
        else:
            messagebox.showwarning("提示", "人员已存在或名称为空！")

    def remove_worker(self):
        sel = self.worker_listbox.curselection()
        if not sel:
            messagebox.showwarning("提示", "请先在列表中选中要删除的人员！")
            return
        name = self.worker_listbox.get(sel[0])
        if messagebox.askyesno("确认", f"确定要删除员工【{name}】吗？\n(不会删除其历史记录)"):
            self.dispatcher.remove_worker(name)
            self.refresh_worker_ui()

    def do_confirm(self):
        task_type = self.type_combo.get()
        task_name = self.task_entry.get().strip() or "无单号"
        target = self.target_worker_combo.get()

        if not target:
            messagebox.showwarning("提示", "没有可用员工，请先添加人员！")
            return

        log = self.dispatcher.confirm_dispatch(task_type, task_name, target)
        # 插入表格第一行 (最新记录在最上)
        self.tree.insert("", 0, values=(log["时间"], log["任务类型"], log["任务单号"], log["接收员工"]))
        
        self.task_entry.delete(0, tk.END)
        self.update_recommended_worker()

    def on_tree_double_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell": return
        
        col = self.tree.identify_column(event.x)
        row_id = self.tree.identify_row(event.y)
        
        if col == "#4":  # 第4列：接收员工
            current_values = self.tree.item(row_id, 'values')
            current_worker = current_values[3]
            
            new_worker = simpledialog.askstring("修改接收人", f"当前: {current_worker}\n请输入新的员工姓名:", initialvalue=current_worker)
            
            if new_worker and new_worker != current_worker:
                children = self.tree.get_children()
                index = children.index(row_id)
                # 因为表格是倒序的，需要计算真实的 log 索引
                log_index = len(self.dispatcher.logs) - 1 - index 
                
                self.dispatcher.update_log_worker(log_index, new_worker) # 内部会自动保存
                
                new_values = list(current_values)
                new_values[3] = new_worker
                self.tree.item(row_id, values=new_values)

    def do_statistics(self):
        start_t = self.start_time_entry.get().strip()
        end_t = self.end_time_entry.get().strip()
        worker = self.stat_worker_combo.get()

        count, details = self.dispatcher.get_statistics(start_t, end_t, worker)
        
        self.stat_result_text.config(state=tk.NORMAL)
        self.stat_result_text.delete(1.0, tk.END)
        
        if count == -1:
            self.stat_result_text.insert(tk.END, details)
        else:
            self.stat_result_text.insert(tk.END, f"🎯 统计结果：共 {count} 个任务\n")
            self.stat_result_text.insert(tk.END, "="*40 + "\n")
            if details:
                for d in details:
                    self.stat_result_text.insert(tk.END, d + "\n")
            else:
                self.stat_result_text.insert(tk.END, "该时段内无任务记录。")
        self.stat_result_text.config(state=tk.DISABLED)

    def export_data(self):
        if not self.dispatcher.logs:
            messagebox.showinfo("提示", "暂无记录可导出！")
            return
        try:
            default_name = f"车间发活记录_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            file_path = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")], initialfile=default_name)
            if file_path:
                df = pd.DataFrame(self.dispatcher.logs)
                df.to_excel(file_path, index=False, engine='openpyxl')
                messagebox.showinfo("成功", f"已导出至:\n{file_path}")
        except Exception as e:
            messagebox.showerror("错误", f"导出失败：{e}\n请确保安装了 pandas 和 openpyxl")


if __name__ == "__main__":
    root = tk.Tk()
    app = DispatchApp(root)
    root.mainloop()