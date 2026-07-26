"""Test 25x30 puzzle with progress output."""
import time, sys
sys.setrecursionlimit(100000)

from solver import SlitherlinkState
from image_parser import parse_image
from pathlib import Path

grid = parse_image(Path('puzzles/25_1.png'))
state = SlitherlinkState(grid)
state.propagate()

print(f'Grid: {len(grid)}x{len(grid[0])}')
print(f'Unknown after init: {len(state._unknown_edges)}')

# Add progress tracking to solve
_orig_solve = SlitherlinkState.solve
_nodes = [0]
_t0 = [time.time()]
_last_report = [0]

def solve_with_progress(self):
    _nodes[0] += 1
    if _nodes[0] % 500 == 0 and _nodes[0] != _last_report[0]:
        _last_report[0] = _nodes[0]
        elapsed = time.time() - _t0[0]
        print(f'  nodes={_nodes[0]}, unknown={len(self._unknown_edges)}, '
              f'dead={len(self._dead_cache)}, t={elapsed:.1f}s')
    return _orig_solve(self)

SlitherlinkState.solve = solve_with_progress

t0 = time.time()
sol = state.solve()
t = time.time() - t0
print(f'Time: {t:.2f}s, Solved: {sol is not None}')
if sol:
    print(sol.render())
