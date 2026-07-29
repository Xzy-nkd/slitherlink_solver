"""
Slitherlink 在线自动求解器 GUI。

启动后弹出界面，配置参数后点击按钮自动求解。
"""
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import asyncio
import sys
import json
from pathlib import Path
from queue import Queue, Empty

sys.path.insert(0, str(Path(__file__).parent))
from auto_solver import solve_loop, do_login, LOGIN_PAGE, DEFAULT_SESSION, SET_AUTH_JS


SIZE_OPTIONS = {
    "5×5 困难":   "?size=4",
    "7×7 困难":   "?size=11",
    "10×10 困难": "?size=5",
    "15×15 困难": "?size=6",
    "20×20 困难": "?size=7",
    "25×30 困难": "?size=9",
    "每月谜题":    "?size=14",
}

BROWSER_OPTIONS = ["chromium", "firefox", "webkit"]


class LogPipe:
    """捕获 print 输出并转发到 GUI 日志窗口。"""
    def __init__(self, gui):
        self._gui = gui
        self._real_stdout = sys.stdout

    def write(self, s):
        self._real_stdout.write(s)
        self._real_stdout.flush()
        if s.strip():
            self._gui._queue.put(("log", s))

    def flush(self):
        self._real_stdout.flush()

    @property
    def buffer(self):
        return self._real_stdout.buffer


class AutoSolverGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Slitherlink 在线自动求解器")
        self.root.geometry("780x620")
        self.root.minsize(600, 450)

        self._running = False
        self._email = None
        self._token = None
        self._session_file = DEFAULT_SESSION
        self._queue = Queue()

        self._build_ui()
        self._install_log_pipe()
        self._poll_queue()

        self.root.update()
        self.root.attributes('-topmost', True)
        self.root.lift()
        self.root.focus_force()
        self.root.after(500, lambda: self.root.attributes('-topmost', False))

        print("Slitherlink 在线自动求解器 启动。")
        self._check_existing_session()

    # ── UI ──

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        # ── Left panel ──
        left = ttk.Frame(main, padding=5)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))

        # 谜题设置
        f = ttk.LabelFrame(left, text="谜题设置", padding=8)
        f.pack(fill=tk.X, pady=(0, 5))

        ttk.Label(f, text="难度/尺寸:").pack(anchor=tk.W)
        self.size_var = tk.StringVar(value="15×15 困难")
        cb = ttk.Combobox(f, textvariable=self.size_var,
                          values=list(SIZE_OPTIONS), state="readonly")
        cb.pack(fill=tk.X, pady=(2, 0))
        cb.bind("<<ComboboxSelected>>", self._on_size_changed)

        ttk.Label(f, text="URL:").pack(anchor=tk.W, pady=(8, 0))
        self.url_var = tk.StringVar(value="https://cn.puzzle-loop.com/?size=6")
        ttk.Entry(f, textvariable=self.url_var).pack(fill=tk.X, pady=(2, 0))

        ttk.Separator(left, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)

        # 浏览器设置
        f = ttk.LabelFrame(left, text="浏览器设置", padding=8)
        f.pack(fill=tk.X, pady=(0, 5))

        ttk.Label(f, text="引擎:").pack(anchor=tk.W)
        self.browser_var = tk.StringVar(value="chromium")
        ttk.Combobox(f, textvariable=self.browser_var,
                     values=BROWSER_OPTIONS, state="readonly").pack(fill=tk.X, pady=(2, 4))

        self.visible_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="可见模式", variable=self.visible_var).pack(anchor=tk.W)

        ttk.Separator(left, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)

        # 登录
        f = ttk.LabelFrame(left, text="账号登录", padding=8)
        f.pack(fill=tk.X, pady=(0, 5))

        ttk.Label(f, text="Email:").pack(anchor=tk.W)
        self.email_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.email_var).pack(fill=tk.X, pady=(2, 4))

        ttk.Label(f, text="密码:").pack(anchor=tk.W)
        self.pass_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.pass_var, show="•").pack(fill=tk.X, pady=(2, 4))

        row = ttk.Frame(f)
        row.pack(fill=tk.X)
        self.login_btn = ttk.Button(row, text="登录", command=self._on_login)
        self.login_btn.pack(side=tk.LEFT, padx=(0, 5))
        self.login_status = tk.StringVar(value="未登录 (游客)")
        ttk.Label(row, textvariable=self.login_status, foreground="gray").pack(side=tk.LEFT)

        ttk.Separator(left, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)

        # 操作按钮
        f = ttk.LabelFrame(left, text="操作", padding=8)
        f.pack(fill=tk.X)

        style = ttk.Style()
        style.configure("Solve.TButton", font=("Microsoft YaHei", 11, "bold"))

        self.solve_btn = ttk.Button(
            f, text="🧠 求解当前题目", command=lambda: self._on_solve(new_puzzle=False),
            style="Solve.TButton"
        )
        self.solve_btn.pack(fill=tk.X, ipady=6, pady=(0, 4))

        self.new_solve_btn = ttk.Button(
            f, text="🔄 获取新题目并求解", command=lambda: self._on_solve(new_puzzle=True),
            style="Solve.TButton"
        )
        self.new_solve_btn.pack(fill=tk.X, ipady=6)

        # ── Right panel: log ──
        right = ttk.Frame(main, padding=5)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        ttk.Label(right, text="运行日志", font=("Microsoft YaHei", 10, "bold")).pack(anchor=tk.W)
        self.log_text = scrolledtext.ScrolledText(
            right, wrap=tk.WORD, state=tk.DISABLED,
            font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4", padx=8, pady=8
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # ── Bottom bar ──
        self.progress = ttk.Progressbar(self.root, mode="indeterminate")
        self.progress.pack(fill=tk.X, padx=10, pady=(0, 5))
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(self.root, textvariable=self.status_var,
                  relief=tk.SUNKEN, anchor=tk.W, padding=5).pack(fill=tk.X)

    # ── Queue polling ──

    def _install_log_pipe(self):
        self._real_stdout = sys.stdout
        sys.stdout = LogPipe(self)

    def _poll_queue(self):
        try:
            while True:
                msg_type, data = self._queue.get_nowait()
                if msg_type == "log":
                    self.log_text.configure(state=tk.NORMAL)
                    self.log_text.insert(tk.END, data)
                    self.log_text.see(tk.END)
                    self.log_text.configure(state=tk.DISABLED)
                elif msg_type == "status":
                    self.status_var.set(data)
                elif msg_type == "progress_start":
                    self.progress.start(10)
                elif msg_type == "progress_stop":
                    self.progress.stop()
                elif msg_type == "btns_disable":
                    self.solve_btn.configure(state=tk.DISABLED, text="求解中...")
                    self.new_solve_btn.configure(state=tk.DISABLED, text="求解中...")
                elif msg_type == "btns_enable":
                    self.solve_btn.configure(state=tk.NORMAL, text="🧠 求解当前题目")
                    self.new_solve_btn.configure(state=tk.NORMAL, text="🔄 获取新题目并求解")
                elif msg_type == "login_status":
                    self.login_status.set(data)
                elif msg_type == "login_btn_enable":
                    self.login_btn.configure(state=tk.NORMAL)
                elif msg_type == "login_btn_disable":
                    self.login_btn.configure(state=tk.DISABLED)
        except Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _log(self, text: str):
        self._queue.put(("log", text))

    # ── Callbacks ──

    def _on_size_changed(self, event=None):
        label = self.size_var.get()
        param = SIZE_OPTIONS.get(label, "?size=6")
        base = self.url_var.get().split("?")[0] if "?" in self.url_var.get() else "https://cn.puzzle-loop.com/"
        self.url_var.set(f"{base}{param}")

    def _on_login(self):
        email = self.email_var.get().strip()
        password = self.pass_var.get()
        if not email or not password:
            messagebox.showwarning("提示", "请输入邮箱和密码。")
            return

        self._queue.put(("login_btn_disable", None))
        self._queue.put(("login_status", "登录中..."))

        def do():
            try:
                token = asyncio.run(_login_async(email, password))
                if token:
                    self._token = token
                    self._email = email
                    asyncio.run(_save_session_async(self._session_file, token))
                    self._queue.put(("login_status", f"✅ {email}"))
                    self._log("登录成功！会话已保存。\n")
                else:
                    self._queue.put(("login_status", "❌ 登录失败"))
                    self._log("登录失败，请检查邮箱和密码。\n")
            except Exception as e:
                self._queue.put(("login_status", f"❌ {e}"))
                self._log(f"登录异常: {e}\n")
            finally:
                self._queue.put(("login_btn_enable", None))

        threading.Thread(target=do, daemon=True).start()

    def _check_existing_session(self):
        path = Path(self._session_file)
        if not path.exists():
            print("请配置参数后点击按钮开始求解。\n")
            return
        try:
            cookies = json.loads(path.read_text(encoding='utf-8'))
            for c in cookies:
                if c.get("name") == "api_token" and c.get("value"):
                    self._token = c["value"]
                    email_cookie = next((x for x in cookies if x.get("name") == "email"), None)
                    if email_cookie:
                        self._email = email_cookie["value"]
                    self._queue.put(("login_status", "✅ 已加载会话"))
                    self._log("📂 已加载保存的登录会话。\n")
                    return
        except Exception:
            pass
        print("请配置参数后点击按钮开始求解。\n")

    def _on_solve(self, new_puzzle: bool = False):
        if self._running:
            return
        self._running = True
        self._queue.put(("btns_disable", None))
        self._queue.put(("progress_start", None))

        label = "获取新题目并求解" if new_puzzle else "求解当前题目"
        self._queue.put(("status", f"正在{label}..."))

        url = self.url_var.get().strip()
        headless = not self.visible_var.get()
        browser_name = self.browser_var.get()

        def do():
            try:
                asyncio.run(solve_loop(
                    url=url,
                    headless=headless,
                    browser_name=browser_name,
                    email=self._email,
                    password=None,
                    session_file=self._session_file,
                    dry_run=False,
                    new_puzzle=new_puzzle,
                ))
                self._queue.put(("status", "✅ 求解完成"))
            except Exception as e:
                self._log(f"\n❌ 错误: {e}\n")
                self._queue.put(("status", f"❌ {e}"))
            finally:
                self._running = False
                self._queue.put(("btns_enable", None))
                self._queue.put(("progress_stop", None))

        threading.Thread(target=do, daemon=True).start()


# ── Async helpers ──

async def _login_async(email: str, password: str) -> str | None:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        token = await do_login(page, email, password)
        await browser.close()
        return token


async def _save_session_async(path: str, token: str):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(LOGIN_PAGE, wait_until="networkidle", timeout=30000)
        await page.evaluate(SET_AUTH_JS, token)
        cookies = await ctx.cookies()
        Path(path).write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding='utf-8')
        await browser.close()


def run_gui():
    root = tk.Tk()
    AutoSolverGUI(root)
    root.mainloop()


if __name__ == "__main__":
    run_gui()
