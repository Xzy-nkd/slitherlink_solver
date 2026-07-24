"""
Slitherlink 求解器的 Tkinter 图形化界面。

提供：
- 文件对话框加载谜题
- 可视化展示原始谜题与求解结果
- 用不同颜色区分连线（蓝）、排除的边（灰叉）、未知边
- 状态栏显示当前操作结果
"""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
from typing import Optional
import threading
import time

from solver import solve_puzzle, SlitherlinkState
from image_parser import parse_input, IMAGE_EXTENSIONS


class SlitherlinkGUI:
    """数回求解器的主窗口。"""

    CELL_SIZE = 60          # 相邻黑点之间的像素距离
    DOT_RADIUS = 4          # 黑点半径
    LINE_WIDTH = 3          # 回路线宽
    CROSS_SIZE = 10         # 排除标记叉的大小
    PADDING = 50            # 画布内边距

    # 颜色配置
    COLOR_BG = "white"
    COLOR_DOT = "black"
    COLOR_LINE = "#1a73e8"      # 蓝色
    COLOR_CROSS = "#9aa0a6"     # 灰色
    COLOR_TEXT = "#202124"
    COLOR_HIGHLIGHT = "#ea4335" # 红色（用于错误提示）

    COLOR_STEP_LINE = "#34a853"      # 绿色（逐步求解中刚确定的连线）
    COLOR_STEP_CROSS = "#fbbc04"     # 黄色（逐步求解中刚确定的排除边）

    def __init__(self, root: tk.Tk, puzzle_path: Optional[Path] = None):
        self.root = root
        self.root.title("Slitherlink 数回求解器")
        self.root.configure(bg=self.COLOR_BG)

        self.state: Optional[SlitherlinkState] = None
        self.original_grid: Optional[list] = None
        self.file_path: Optional[Path] = None
        self._solving = False  # 防止重复点击

        self._build_ui()
        if puzzle_path is not None:
            self.load_puzzle(puzzle_path)

    def _build_ui(self) -> None:
        """构建窗口控件。"""
        # 顶部按钮栏
        toolbar = ttk.Frame(self.root, padding=10)
        toolbar.pack(fill=tk.X)

        ttk.Button(toolbar, text="加载谜题", command=self._on_load_click).pack(side=tk.LEFT, padx=5)
        self.solve_btn = ttk.Button(toolbar, text="求解", command=self._on_solve_click, state=tk.DISABLED)
        self.solve_btn.pack(side=tk.LEFT, padx=5)
        self.prop_btn = ttk.Button(
            toolbar, text="显示传播状态", command=self._on_propagate_click, state=tk.DISABLED
        )
        self.prop_btn.pack(side=tk.LEFT, padx=5)
        self.step_btn = ttk.Button(
            toolbar, text="逐步求解", command=self._on_solve_step_click, state=tk.DISABLED
        )
        self.step_btn.pack(side=tk.LEFT, padx=5)
        ttk.Button(toolbar, text="退出", command=self.root.quit).pack(side=tk.RIGHT, padx=5)

        # 画布区域（带滚动条，防止谜题过大时超出屏幕）
        canvas_frame = ttk.Frame(self.root)
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.canvas = tk.Canvas(
            canvas_frame, bg=self.COLOR_BG, highlightthickness=1, highlightbackground="#dadce0"
        )
        self.scroll_x = ttk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL, command=self.canvas.xview)
        self.scroll_y = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=self.scroll_x.set, yscrollcommand=self.scroll_y.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scroll_x.grid(row=1, column=0, sticky="ew")
        self.scroll_y.grid(row=0, column=1, sticky="ns")
        canvas_frame.grid_rowconfigure(0, weight=1)
        canvas_frame.grid_columnconfigure(0, weight=1)

        # 底部状态栏
        self.status_var = tk.StringVar(value="就绪：请加载谜题文件")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W, padding=5)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    def _on_load_click(self) -> None:
        """点击“加载谜题”按钮。"""
        img_exts = " ".join(f"*{e}" for e in sorted(IMAGE_EXTENSIONS))
        path = filedialog.askopenfilename(
            title="选择谜题文件",
            filetypes=[
                ("所有支持的文件", "*.txt " + img_exts),
                ("文本文件", "*.txt"),
                ("图片文件", img_exts),
                ("所有文件", "*.*")
            ]
        )
        if path:
            self.load_puzzle(Path(path))

    def load_puzzle(self, path: Path) -> bool:
        """从文本或图片文件加载并显示谜题。"""
        if not path.exists():
            messagebox.showerror("错误", f"文件不存在：{path}")
            return False

        try:
            grid = parse_input(path)
        except Exception as exc:
            messagebox.showerror("错误", f"解析谜题失败：{exc}")
            return False

        if not grid or not grid[0]:
            messagebox.showerror("错误", "未从文件中识别出有效谜题。")
            return False

        self.file_path = path
        self.original_grid = grid
        self.state = SlitherlinkState(grid)
        self._render(self.state)
        self.solve_btn.configure(state=tk.NORMAL)
        self.prop_btn.configure(state=tk.NORMAL)
        self.step_btn.configure(state=tk.NORMAL)
        source = "图片" if path.suffix.lower() in IMAGE_EXTENSIONS else "文本"
        self.status_var.set(f"已加载{source}：{path.name}  ({len(grid)}×{len(grid[0])})")
        return True

    def _on_solve_click(self) -> None:
        """点击"求解"按钮。"""
        if self.original_grid is None or self._solving:
            return

        self._solving = True
        self.solve_btn.configure(state=tk.DISABLED)

        self.status_var.set("正在求解...")
        self.root.update_idletasks()

        try:
            solution = solve_puzzle(self.original_grid)
            if solution is None:
                self.status_var.set("未找到解。")
                messagebox.showinfo("求解结果", "未找到满足所有约束的解。")
                return

            self.state = solution
            self._render(solution)
            self.status_var.set("求解完成。")
        except Exception as exc:
            self.status_var.set(f"求解出错：{exc}")
            messagebox.showerror("求解错误", str(exc))
        finally:
            self._solving = False
            self.solve_btn.configure(state=tk.NORMAL)

    def _on_propagate_click(self) -> None:
        """点击“显示传播状态”按钮：仅执行约束传播，不进入回溯。"""
        if self.original_grid is None:
            return

        state = SlitherlinkState(self.original_grid)
        try:
            state.propagate()
        except Exception as exc:
            messagebox.showerror("错误", f"约束传播出错：{exc}")
            return

        self.state = state
        self._render(state)
        self.status_var.set("已显示约束传播后的状态（黄色虚线表示仍未知）。")

    def _on_solve_step_click(self) -> None:
        """点击"逐步求解"按钮：在后台线程中逐步求解，实时更新画布。"""
        if self.original_grid is None or self._solving:
            return

        self._solving = True
        self.step_btn.configure(state=tk.DISABLED)
        self.solve_btn.configure(state=tk.DISABLED)
        self.prop_btn.configure(state=tk.DISABLED)

        # 存储每一步刚被确定的边（用于高亮显示刚变化的边）
        self._last_edge = None

        def on_edge_set(state, kind, r, c, val):
            """每当一条边被确定时，在主线程中更新画布并等待片刻。"""
            self._last_edge = (kind, r, c, val)
            # 使用 root.after 在主线程安全地更新 UI
            self.root.after(0, self._step_render, state)
            # 等待一小段时间让用户看清变化（每次边变化约停 50ms）
            time.sleep(0.05)

        # 创建带回调的状态
        state = SlitherlinkState(self.original_grid, on_edge_set=on_edge_set)

        def solve_thread():
            try:
                result = state.solve()
                if result is not None:
                    # 求解成功，最终渲染一次
                    self.root.after(0, lambda: self._step_render(result, done=True))
                else:
                    self.root.after(0, lambda: self.status_var.set("未找到解。"))
            finally:
                self._solving = False
                self.root.after(0, self._step_enable_buttons)

        self.status_var.set("正在逐步求解...")
        t = threading.Thread(target=solve_thread, daemon=True)
        t.start()

    def _step_render(self, state: SlitherlinkState, done: bool = False) -> None:
        """逐步求解的渲染：高亮刚确定的边，然后等待一段时间。"""
        self.canvas.delete("all")
        R, C = state.R, state.C

        width = 2 * self.PADDING + C * self.CELL_SIZE
        height = 2 * self.PADDING + R * self.CELL_SIZE
        self.canvas.configure(scrollregion=(0, 0, width, height))

        # 绘制数字
        for r in range(R):
            for c in range(C):
                num = state.grid[r][c]
                if num is not None:
                    x = self.PADDING + c * self.CELL_SIZE + self.CELL_SIZE / 2
                    y = self.PADDING + r * self.CELL_SIZE + self.CELL_SIZE / 2
                    self.canvas.create_text(
                        x, y,
                        text=str(num),
                        font=("Microsoft YaHei", 18, "bold"),
                        fill=self.COLOR_TEXT
                    )

        # 绘制横向边
        for r in range(R + 1):
            for c in range(C):
                x1 = self.PADDING + c * self.CELL_SIZE
                y1 = self.PADDING + r * self.CELL_SIZE
                x2 = x1 + self.CELL_SIZE
                y2 = y1
                self._draw_edge(state, 'h', r, c, x1, y1, x2, y2)

        # 绘制纵向边
        for r in range(R):
            for c in range(C + 1):
                x1 = self.PADDING + c * self.CELL_SIZE
                y1 = self.PADDING + r * self.CELL_SIZE
                x2 = x1
                y2 = y1 + self.CELL_SIZE
                self._draw_edge(state, 'v', r, c, x1, y1, x2, y2)

        # 绘制黑点
        for r in range(R + 1):
            for c in range(C + 1):
                x = self.PADDING + c * self.CELL_SIZE
                y = self.PADDING + r * self.CELL_SIZE
                self.canvas.create_oval(
                    x - self.DOT_RADIUS, y - self.DOT_RADIUS,
                    x + self.DOT_RADIUS, y + self.DOT_RADIUS,
                    fill=self.COLOR_DOT, outline=self.COLOR_DOT
                )

        if done:
            self.status_var.set("逐步求解完成！")
        else:
            self.status_var.set("求解中...（边被确定时实时更新）")
            # 等待一小段时间让用户看清变化，然后继续
            self.root.update()

    def _draw_edge(self, state: SlitherlinkState, kind: str, r: int, c: int,
                   x1: float, y1: float, x2: float, y2: float) -> None:
        """绘制一条边，如果是刚被确定的边则使用高亮颜色。"""
        val = state.get(kind, r, c)
        if val == SlitherlinkState.LINE:
            # 判断是否是刚确定的边
            if self._last_edge and self._last_edge == (kind, r, c, SlitherlinkState.LINE):
                self.canvas.create_line(x1, y1, x2, y2, width=self.LINE_WIDTH, fill=self.COLOR_STEP_LINE)
            else:
                self.canvas.create_line(x1, y1, x2, y2, width=self.LINE_WIDTH, fill=self.COLOR_LINE)
        elif val == SlitherlinkState.CROSS:
            if self._last_edge and self._last_edge == (kind, r, c, SlitherlinkState.CROSS):
                # 刚确定的排除边用黄色绘制
                self._draw_cross((x1 + x2) / 2, (y1 + y2) / 2, fill=self.COLOR_STEP_CROSS)
            else:
                self._draw_cross((x1 + x2) / 2, (y1 + y2) / 2)
        else:
            self.canvas.create_line(x1, y1, x2, y2, width=1, fill="#e8eaed", dash=(2, 4))

    def _step_enable_buttons(self) -> None:
        """恢复按钮状态。"""
        self.step_btn.configure(state=tk.NORMAL)
        self.solve_btn.configure(state=tk.NORMAL)
        self.prop_btn.configure(state=tk.NORMAL)
        self._last_edge = None

    def _render(self, state: SlitherlinkState) -> None:
        """在画布上绘制当前状态。"""
        self.canvas.delete("all")
        R, C = state.R, state.C

        width = 2 * self.PADDING + C * self.CELL_SIZE
        height = 2 * self.PADDING + R * self.CELL_SIZE
        self.canvas.configure(scrollregion=(0, 0, width, height))

        # 绘制数字
        for r in range(R):
            for c in range(C):
                num = state.grid[r][c]
                if num is not None:
                    x = self.PADDING + c * self.CELL_SIZE + self.CELL_SIZE / 2
                    y = self.PADDING + r * self.CELL_SIZE + self.CELL_SIZE / 2
                    self.canvas.create_text(
                        x, y,
                        text=str(num),
                        font=("Microsoft YaHei", 18, "bold"),
                        fill=self.COLOR_TEXT
                    )

        # 绘制横向边
        for r in range(R + 1):
            for c in range(C):
                x1 = self.PADDING + c * self.CELL_SIZE
                y1 = self.PADDING + r * self.CELL_SIZE
                x2 = x1 + self.CELL_SIZE
                y2 = y1
                val = state.h[r][c]
                if val == SlitherlinkState.LINE:
                    self.canvas.create_line(x1, y1, x2, y2, width=self.LINE_WIDTH, fill=self.COLOR_LINE)
                elif val == SlitherlinkState.CROSS:
                    self._draw_cross((x1 + x2) / 2, (y1 + y2) / 2)
                else:
                    # 未知边用很淡的虚线提示位置，方便观察
                    self.canvas.create_line(
                        x1, y1, x2, y2, width=1, fill="#e8eaed", dash=(2, 4)
                    )

        # 绘制纵向边
        for r in range(R):
            for c in range(C + 1):
                x1 = self.PADDING + c * self.CELL_SIZE
                y1 = self.PADDING + r * self.CELL_SIZE
                x2 = x1
                y2 = y1 + self.CELL_SIZE
                val = state.v[r][c]
                if val == SlitherlinkState.LINE:
                    self.canvas.create_line(x1, y1, x2, y2, width=self.LINE_WIDTH, fill=self.COLOR_LINE)
                elif val == SlitherlinkState.CROSS:
                    self._draw_cross((x1 + x2) / 2, (y1 + y2) / 2)
                else:
                    self.canvas.create_line(
                        x1, y1, x2, y2, width=1, fill="#e8eaed", dash=(2, 4)
                    )

        # 绘制黑点
        for r in range(R + 1):
            for c in range(C + 1):
                x = self.PADDING + c * self.CELL_SIZE
                y = self.PADDING + r * self.CELL_SIZE
                self.canvas.create_oval(
                    x - self.DOT_RADIUS, y - self.DOT_RADIUS,
                    x + self.DOT_RADIUS, y + self.DOT_RADIUS,
                    fill=self.COLOR_DOT, outline=self.COLOR_DOT
                )

    def _draw_cross(self, x: float, y: float, fill: Optional[str] = None) -> None:
        """在指定坐标绘制排除标记 x。"""
        if fill is None:
            fill = self.COLOR_CROSS
        half = self.CROSS_SIZE / 2
        self.canvas.create_line(
            x - half, y - half, x + half, y + half,
            width=2, fill=fill
        )
        self.canvas.create_line(
            x - half, y + half, x + half, y - half,
            width=2, fill=fill
        )


def run_gui(puzzle_path: Optional[Path] = None) -> None:
    """启动 GUI 事件循环。"""
    root = tk.Tk()
    root.geometry("900x700")
    SlitherlinkGUI(root, puzzle_path=puzzle_path)
    root.mainloop()
