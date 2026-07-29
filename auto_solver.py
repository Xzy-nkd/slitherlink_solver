"""
Automatically solve a Slitherlink puzzle on puzzle-loop.com.

Usage:
    python auto_solver.py [url] [options]

Options:
    url          : Puzzle URL (default: https://cn.puzzle-loop.com/?size=6)
    --headless   : Run browser in headless mode (default: visible)
    --browser B  : Browser engine: chromium / firefox / webkit (default: chromium)
    --login E P  : Login with email and password (saves session)
    --session    : Path to saved session file (default: ./session.json)

Examples:
    python auto_solver.py
    python auto_solver.py --headless
    python auto_solver.py --browser firefox
    python auto_solver.py --login user@mail.com mypassword
    python auto_solver.py --session ./my_session.json
    python auto_solver.py "https://cn.puzzle-loop.com/?size=10" --headless
"""
import asyncio
import sys
import time
import json
import argparse
import io
import hashlib
from pathlib import Path
from playwright.async_api import async_playwright

# Fix Windows console encoding for emoji
if hasattr(sys.stdout, 'buffer'):
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass

# Add project root for imports
sys.path.insert(0, str(Path(__file__).parent))
from sat_solver import solve_sat


# ─── Constants ───

LOGIN_API = "https://www.puzzles-mobile.com/api/login"
LOGIN_PAGE = "https://cn.puzzle-loop.com"
DEFAULT_SESSION = str(Path(__file__).parent / "session.json")

BROWSERS = {
    "chromium": None,   # Lazy init
    "firefox": None,
    "webkit": None,
}


# ─── JS snippets ───

EXTRACT_TASK_JS = """() => {
    if (typeof Game === 'undefined' || !Game.task) return null;
    return {
        task: Game.task,
        width: Game.puzzleWidth,
        height: Game.puzzleHeight,
        ident: (Game.getSaveIdent ? Game.getSaveIdent() : 'unknown')
    };
}"""

INJECT_AND_CHECK_JS = """(data) => {
    // Directly assign internal state arrays (1 op instead of 480)
    Game.currentState.cellHorizontalStatus = data.h;
    Game.currentState.cellVerticalStatus = data.v;
    Game.drawCurrentState();
    Game.check();
    var clock = document.querySelector('.clock');
    return {
        lines: data.h.flat().filter(function(x){return x===1}).length
            + data.v.flat().filter(function(x){return x===1}).length,
        crosses: data.h.flat().filter(function(x){return x===2}).length
            + data.v.flat().filter(function(x){return x===2}).length,
        total: (data.h.length * data.h[0].length) + (data.v.length * data.v[0].length),
        clockSolved: clock ? clock.classList.contains('solved') : false
    };
}"""

SET_AUTH_JS = """(token) => {
    localStorage.setItem('api_token', token);
    document.cookie = 'api_token=' + token + ';path=/;max-age=' + (365*24*60*60);
}"""


# ─── Auth ───

def _obfuscate(s: str) -> str:
    """Light obfuscation for stored credentials (NOT encryption)."""
    key = "pzl-loop-solver-2024"
    return hashlib.sha256((s + key).encode()).hexdigest()[:32]


async def do_login(page, email: str, password: str) -> str | None:
    """Login via the API and return the token, or None on failure."""
    print(f"🔑 登录 {email}...")

    # POST to login API
    resp = await page.request.post(LOGIN_API, form={
        "email": email,
        "password": password,
    })

    if resp.status != 200:
        body = await resp.text()
        print(f"   ❌ 登录失败 (HTTP {resp.status}): {body[:200]}")
        return None

    token = (await resp.text()).strip()
    if not token or len(token) < 10:
        print(f"   ❌ 登录失败: 无效的 token")
        return None

    print(f"   ✅ 登录成功 (token: {token[:8]}...)")

    # Navigate to the game site to set auth state
    await page.goto(LOGIN_PAGE, wait_until="networkidle", timeout=30000)
    await page.evaluate(SET_AUTH_JS, token)

    # Reload to apply auth
    await page.reload(wait_until="networkidle")

    # Verify login state
    user_info = await page.evaluate("""() => {
        if (typeof Game !== 'undefined' && Game.user) {
            return {name: Game.user.name || Game.user.nick, loggedIn: true};
        }
        return {loggedIn: false, token: localStorage.getItem('api_token')};
    }""")

    if user_info.get("loggedIn"):
        print(f"   👤 已登录: {user_info.get('name', 'Unknown')}")
    else:
        print(f"   ⚠️  登录状态未能确认")

    return token


async def load_session(context, session_file: str) -> bool:
    """Load saved browser session (cookies, localStorage)."""
    path = Path(session_file)
    if path.exists():
        try:
            await context.add_cookies(json.loads(path.read_text(encoding='utf-8')))
            print(f"📂 加载会话: {session_file}")
            return True
        except Exception:
            pass
    return False


async def save_session(context, session_file: str):
    """Save browser session to file."""
    cookies = await context.cookies()
    Path(session_file).write_text(
        json.dumps(cookies, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )
    # Also save localStorage
    # (handled separately since Playwright doesn't expose it directly)


# ─── Core helpers (shared by solve_loop and GUI) ───

async def extract_grid_from_page(page):
    """Extract puzzle grid from current page. Returns (grid, R, C, ident, user_status)."""
    puzzle_data = await page.evaluate(EXTRACT_TASK_JS)
    if not puzzle_data:
        return None, 0, 0, "?", "?"
    grid_raw = puzzle_data["task"]
    R, C = len(grid_raw), len(grid_raw[0])
    grid = [[v if v >= 0 else None for v in row] for row in grid_raw]

    user_status = await page.evaluate("""() => {
        if (typeof Game !== 'undefined' && Game.user)
            return Game.user.name || Game.user.nick || 'LoggedIn';
        return 'Guest';
    }""")
    return grid, R, C, puzzle_data.get("ident", "?"), user_status


def build_state_arrays(solution, R, C):
    """Build 2D arrays matching Game.currentState internal format.
    Solver values: 1=LINE, 0=CROSS  →  Game values: 1=LINE, 2=CROSS."""
    h = []
    for r in range(R + 1):
        row = []
        for c in range(C):
            v = solution.h[r][c]
            row.append(1 if v == 1 else (2 if v == 0 else 0))
        h.append(row)

    v = []
    for r in range(R):
        row = []
        for c in range(C + 1):
            val = solution.v[r][c]
            row.append(1 if val == 1 else (2 if val == 0 else 0))
        v.append(row)

    return h, v


async def inject_solution(page, h, v):
    """Inject solution into page by directly assigning Game.currentState arrays."""
    total_lines = sum(1 for row in h for x in row if x == 1) + \
                  sum(1 for row in v for x in row if x == 1)
    total_crosses = sum(1 for row in h for x in row if x == 2) + \
                    sum(1 for row in v for x in row if x == 2)
    print(f"🖱️  直接注入 {total_lines} 条线 + {total_crosses} 个叉 = "
          f"{total_lines + total_crosses} 条边...")

    t0 = time.perf_counter()
    result = await page.evaluate(INJECT_AND_CHECK_JS, {"h": h, "v": v})

    await page.wait_for_timeout(1000)
    result2 = await page.evaluate("""() => {
        var c = document.querySelector('.clock');
        var cs = c ? c.classList.contains('solved') : false;
        var f = (typeof Game.checkFinished === 'function') ? Game.checkFinished() : false;
        return {clockSolved: cs, checkFinished: f,
                stateSolved: Game.currentState ? Game.currentState.solved : false};
    }""")
    result.update(result2)
    result["solved"] = result.get("clockSolved") or result.get("checkFinished") or result.get("stateSolved")
    result["time"] = time.perf_counter() - t0

    if result.get("solved"):
        print(f"🎉 谜题已解决！({result.get('lines', '?')} 线 + {result.get('crosses', '?')} 叉)")
    else:
        print(f"⚠️  验证未通过 (clock={result.get('clockSolved')}, "
              f"checkFinished={result.get('checkFinished')})")
    return result


async def load_new_puzzle(page):
    """Load a new puzzle by clicking the '新题目' button (form POST → new page)."""
    print("🔄 获取新题目...")

    try:
        # Click the "新题目" submit button — causes page navigation
        await page.click("#btnNew", timeout=10000)
    except Exception:
        # Fallback: try clicking by text
        try:
            await page.click("text=新题目", timeout=5000)
        except Exception as e:
            print(f"❌ 找不到新题目按钮: {e}")
            return False

    # Wait for the new page to fully load
    try:
        await page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass  # continue even if networkidle times out

    # Wait for Game.task to be populated with the new puzzle
    try:
        await page.wait_for_function(
            "typeof Game !== 'undefined' && Game.task && Game.task.length > 0",
            timeout=15000
        )
    except Exception:
        print("❌ 新题目页面加载超时")
        return False

    await page.wait_for_timeout(500)
    print("   ✅ 新题目已加载")
    return True


# ─── Main pipeline ───

async def solve_loop(
    url: str,
    headless: bool = False,
    browser_name: str = "chromium",
    email: str | None = None,
    password: str | None = None,
    session_file: str | None = None,
    dry_run: bool = False,
    new_puzzle: bool = False,
):
    action = "新题目并" if new_puzzle else ""
    print(f"🎯 目标: {url}  ({action}求解)")
    t_start = time.perf_counter()

    async with async_playwright() as p:
        # ── 1. Select browser ──
        browser_getter = {
            "chromium": p.chromium,
            "firefox": p.firefox,
            "webkit": p.webkit,
        }.get(browser_name, p.chromium)

        browser = await browser_getter.launch(headless=headless)
        print(f"🌍 浏览器: {browser_name.upper()} ({'headless' if headless else 'visible'})")

        # ── 2. Session loading ──
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )

        if session_file:
            await load_session(context, session_file)

        page = await context.new_page()

        # ── 3. Login if credentials provided ──
        if email and password:
            token = await do_login(page, email, password)
            if token and session_file:
                await save_session(context, session_file)
                print(f"💾 会话已保存: {session_file}")
            if token is None:
                print("⚠️  登录失败，继续以游客模式运行")

        # ── 4. Navigate & wait for game ──
        print("🌐 加载页面...")
        await page.goto(url, wait_until="networkidle", timeout=30000)

        try:
            await page.wait_for_function(
                "typeof Game !== 'undefined' && Game.task && Game.task.length > 0",
                timeout=15000
            )
        except Exception:
            print("❌ 页面加载超时或 Game 对象不存在")
            await browser.close()
            return

        t_load = time.perf_counter()
        print(f"   ✅ 页面加载完成 ({t_load - t_start:.1f}s)")

        # ── 4b. Load new puzzle if requested ──
        if new_puzzle:
            if not await load_new_puzzle(page):
                await browser.close()
                return

        # ── 5. Extract puzzle ──
        grid, R, C, ident, user_status = await extract_grid_from_page(page)
        if grid is None:
            print("❌ 无法提取题目数据")
            await browser.close()
            return

        print(f"📋 题目: {R}×{C}  |  ID: {ident}  |  模式: {user_status}")

        # ── 6. Solve ──
        print("🧠 求解中...")
        t_solve_start = time.perf_counter()
        solution = solve_sat(grid)
        t_solve = time.perf_counter() - t_solve_start

        if solution is None:
            print("❌ 无解 (UNSAT)")
            await browser.close()
            return

        print(f"   ✅ 求解完成 ({t_solve:.3f}s)")

        if dry_run:
            print("\n📐 解 (仅展示，未注入):")
            print(solution.render())
            await browser.close()
            print(f"\n⏱️  总耗时: {time.perf_counter() - t_start:.2f}s")
            return

        # ── 7-9. Inject & verify ──
        h_arr, v_arr = build_state_arrays(solution, R, C)
        t_solve_end = time.perf_counter()
        result = await inject_solution(page, h_arr, v_arr)
        t_inject = time.perf_counter() - t_solve_end

        # Screenshot
        screenshot_path = str(Path(__file__).parent / "solved.png")
        await page.screenshot(path=screenshot_path, full_page=True)
        print(f"📸 截图保存: {screenshot_path}")

        # Summary
        t_total = time.perf_counter() - t_start
        print(f"\n⏱️  总耗时: {t_total:.2f}s")
        print(f"   ├─ 页面加载: {t_load - t_start:.1f}s")
        print(f"   ├─ SAT 求解: {t_solve:.3f}s")
        print(f"   └─ JS 注入: {t_inject:.1f}s")

        # Save session
        if session_file:
            await save_session(context, session_file)

        # Keep browser open in visible mode so user can see the result
        if not headless:
            print("🔒 浏览器保持打开，关闭窗口或按 Ctrl+C 退出。")
            try:
                # Wait indefinitely — user closes browser manually
                await page.wait_for_timeout(86400000)  # 24h
            except KeyboardInterrupt:
                pass

        await browser.close()


def main():
    parser = argparse.ArgumentParser(
        description="自动求解 puzzle-loop.com 上的 Slitherlink 谜题",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python auto_solver.py
  python auto_solver.py --headless
  python auto_solver.py --browser firefox
  python auto_solver.py --login user@mail.com mypassword
  python auto_solver.py --session ./session.json
  python auto_solver.py "https://cn.puzzle-loop.com/?size=10"
        """
    )
    parser.add_argument(
        "url", nargs="?",
        default="https://cn.puzzle-loop.com/?size=6",
        help="谜题 URL"
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="无头模式（默认可见）"
    )
    parser.add_argument(
        "--browser", choices=["chromium", "firefox", "webkit"],
        default="chromium",
        help="浏览器引擎 (default: chromium)"
    )
    parser.add_argument(
        "--login", nargs=2, metavar=("EMAIL", "PASSWORD"),
        help="登录账号"
    )
    parser.add_argument(
        "--session", default=DEFAULT_SESSION,
        help=f"会话保存路径 (default: {DEFAULT_SESSION})"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅提取题目并求解，不注入"
    )
    args = parser.parse_args()

    asyncio.run(solve_loop(
        url=args.url,
        headless=args.headless,
        browser_name=args.browser,
        email=args.login[0] if args.login else None,
        password=args.login[1] if args.login else None,
        session_file=args.session,
        dry_run=args.dry_run,
    ))


if __name__ == "__main__":
    main()
