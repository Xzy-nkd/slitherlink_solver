"""Quick performance test for solver optimizations."""
import time
from solver import solve_puzzle
from image_parser import parse_image
from pathlib import Path

for name in ['15_2', '20_1']:
    grid = parse_image(Path(f'puzzles/{name}.png'))
    t0 = time.time()
    sol = solve_puzzle(grid)
    t = time.time() - t0
    status = "OK" if sol else "FAIL"
    print(f'{name} {len(grid)}x{len(grid[0])}: {t:.2f}s, {status}')
