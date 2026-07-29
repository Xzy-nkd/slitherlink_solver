# Slitherlink（数回）自动求解器

## 项目简介

Python 实现的 Slitherlink 自动求解器。Slitherlink 是一种画线类逻辑谜题：在由黑点组成的网格中画唯一的闭合回路，回路不能交叉或分叉；棋盘中的数字表示该格子四周被多少条线段包围。

### 核心能力

- 🧩 **CSP 求解器**：约束传播 + 回溯搜索，支持 20×20 以内的谜题秒级求解。
- ⚡ **SAT 求解器**：基于 Glucose3，30×25 谜题约 0.05s，50×40 约 0.2s。
- 🖼️ **图片识别**：从截图/扫描图自动提取网格和数字（NCC 模板匹配 + 几何特征）。
- 🖥️ **图形界面**：Tkinter GUI，支持逐步求解动画。
- 🌐 **在线自动求解**：Playwright 自动化 puzzle-loop.com，截题 → 求解 → 注入 → 验证。
- 📐 **局部定式**：内置 3-3 相邻、角-3、3-顶点-外侧等经过验证的模式。

---

## 文件结构

```text
slitherlink_solver/
├── solver.py              # CSP 核心求解器（约束传播 + 回溯搜索 + 增量优化）
├── sat_solver.py           # SAT 求解器（Glucose3，预传播 + 顶点度数编码）
├── main.py                 # 程序入口（默认 GUI，--cli 命令行，--sat 启用 SAT）
├── gui.py                  # Tkinter 图形界面（含逐步求解动画）
├── image_parser.py         # 图片谜题解析（网格检测 + 两轮 NCC 数字识别）
├── auto_solver.py          # 在线自动求解器（Playwright，CLI）
├── auto_gui.py             # 在线自动求解器 GUI
├── README.md               # 本说明
├── WIKI.md                 # 算法详解与性能分析
├── .gitignore
└── puzzles/
    ├── 5_1.png             # 5×5 测试谜题
    ├── 10_1.png            # 10×10 测试谜题
    ├── 15_1.png            # 15×15 测试谜题
    ├── 15_2.png            # 15×15 测试谜题
    ├── 20_1.png            # 20×20 测试谜题
    ├── 25_1.png            # 30×25 测试谜题
    ├── July.png            # 50×40 大谜题
    └── image_puzzle.txt    # 5×5 示例（文本格式）
```

---

## 快速开始

### 环境要求

- **Python 3.10+**
- **Pillow**：`pip install Pillow`
- **python-sat**：`pip install python-sat`（可选，缺失时降级到 CSP 求解器）
- **Playwright**：`pip install playwright`（在线自动求解需要）

### 本地求解

```bash
# 文本谜题，CSP 求解器
python main.py puzzles/image_puzzle.txt --cli

# 图片谜题，SAT 求解器（大图推荐）
python main.py puzzles/July.png --cli --sat

# 图形界面
python main.py
```

输出示例：

```text
读取的谜题：
. 3 . 3 3
1 . . . 1
. . . 1 2
2 . 2 . .
3 2 2 2 0

解答：
+---+ x +---+ x +---+
|   | 3 |   | 3 | 3 |
+ x +---+ x +---+ x +
| 1 x   x   x   x 1 |
+ x +---+---+ x + x +
|   |   x   | 1 x 2 |
+---+ x +---+ x +---+
x 2 x   | 2 x   |   x
+---+---+ x +---+ x +
| 3 x 2 x 2 | 2 x 0 x
+---+---+---+ x + x +
```

### 在线自动求解（GUI）

```bash
python auto_gui.py
```

- 选择谜题尺寸、浏览器、登录（可选）
- 「求解当前题目」或「获取新题目并求解」
- Chrome 浏览器自动打开 puzzle-loop.com → 截题 → 求解 → 注入 → 验证

### 作为库使用

```python
from image_parser import parse_input
from sat_solver import solve_sat

grid = parse_input(Path("puzzles/July.png"))
solution = solve_sat(grid)  # → (h, v) 元组
```

---

## 算法概览

| 算法/技术 | 应用于 | 说明 |
|---|---|---|
| 约束传播 | CSP 求解器 | 数字、度数、局部模式快速推导 |
| 回溯搜索（DFS） | CSP 求解器 | 假设-验证，含死状态缓存剪枝 |
| SAT 编码 | SAT 求解器 | 变量对应边状态，子句对应约束 |
| Glucose3 | SAT 求解器 | CDCL 算法，大规模高效求解 |
| NCC 模板匹配 | 图片识别 | 归一化互相关数字分类 |
| Otsu 自适应阈值 | 图片识别 | 全局 + 逐格二值化 |
| 可回滚并查集 | CSP 求解器 | O(1) 环路检测与回溯恢复 |

> 📖 详细算法说明、约束类型、编码策略、性能基准及优化细节见 **[WIKI.md](WIKI.md)**。

---

## 性能基准

| 谜题 | 尺寸 | SAT 求解耗时 |
|---|---|---|
| 5×5 | 25格 | 0.013s |
| 10×10 | 100格 | 0.004s |
| 15×15 | 225格 | 0.008s |
| 20×20 | 400格 | 0.020s |
| 30×25 | 750格 | 0.048s |
| 50×40 | 2000格 | 0.180s |

---

## 可扩展方向

- 🔬 更多验证过的高级模式：边界上的 1-3 相邻、2-2 对角
- 🌲 分支策略优化：conflict-directed backjumping
- 🎨 图片识别增强：倾斜校正、透视变换
- 🔄 交互式编辑：GUI 中支持点击输入数字
- 📱 Web 界面：Flask/FastAPI 后端 + 前端渲染
- 🏆 更大规模：MaxSAT 或 ILP 编码处理 100×100 级别
