"""
Slitherlink 求解器入口。

用法示例：
    python main.py puzzles/image_puzzle.txt          # 启动图形界面
    python main.py puzzles/image_puzzle.png          # 图形界面读取图片谜题
    python main.py puzzles/image_puzzle.txt --cli    # 命令行字符画输出
    python main.py                                   # 直接打开图形界面，通过菜单加载谜题
    python main.py puzzles/image_puzzle.txt --steps  # 命令行输出传播中间状态
"""

import argparse
import sys
from pathlib import Path

from solver import solve_puzzle
from image_parser import parse_input, IMAGE_EXTENSIONS


def run_cli(path: Path, show_steps: bool, debug: bool = False) -> int:
    """命令行模式：保持原有字符画输出，支持文本或图片输入。"""
    try:
        grid = parse_input(path, debug=debug)
    except Exception as exc:
        print(f'解析谜题失败：{exc}', file=sys.stderr)
        return 1

    print('读取的谜题：')
    for row in grid:
        print(' '.join(str(x) if x is not None else '.' for x in row))
    print()

    solution = solve_puzzle(grid)
    if solution is None:
        print('未找到解。')
        return 1

    if show_steps:
        print('约束传播完成状态：')
        print(solution.render())
        print()

    print('解答：')
    print(solution.render())
    return 0


def main():
    parser = argparse.ArgumentParser(description='Slitherlink 自动求解器')
    parser.add_argument('puzzle', nargs='?', help='谜题文件路径（可选）')
    parser.add_argument('--cli', action='store_true',
                        help='使用命令行字符画输出，不启动图形界面')
    parser.add_argument('--steps', action='store_true',
                        help='打印传播后的中间状态（仅与 --cli 一起使用）')
    parser.add_argument('--debug', action='store_true',
                        help='图片解析调试模式：输出带网格线和识别结果的调试图（仅对图片文件有效）')
    args = parser.parse_args()

    puzzle_path: Path | None = None
    if args.puzzle:
        puzzle_path = Path(args.puzzle)
        if not puzzle_path.exists():
            print(f'文件不存在：{puzzle_path}', file=sys.stderr)
            sys.exit(1)

    if args.cli:
        if puzzle_path is None:
            print('命令行模式需要指定谜题文件路径。', file=sys.stderr)
            sys.exit(1)
        sys.exit(run_cli(puzzle_path, args.steps, debug=args.debug))

    # 默认启动图形界面
    try:
        from gui import run_gui
    except ImportError as exc:
        print(f'无法加载图形界面：{exc}', file=sys.stderr)
        print('请使用 --cli 选项运行命令行模式，或安装 Tkinter 支持。', file=sys.stderr)
        sys.exit(1)

    run_gui(puzzle_path)


if __name__ == '__main__':
    main()
