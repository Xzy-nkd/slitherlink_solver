from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import List, Optional, Tuple
import random as _random
import heapq

# ── Zobrist 哈希种子（模块级，所有状态共享）──
_ZOBRIST_R = _random.getrandbits(64)

def _zo(kind: str, r: int, c: int, val: int) -> int:
    """O(1) 增量哈希：对 (kind, r, c, val) 取哈希。"""
    return hash((kind, r, c, val, _ZOBRIST_R))


class Contradiction(Exception):
    pass


class RollbackDSU:
    __slots__ = ("parent", "rank", "_trail")

    def __init__(self, n: int):
        self.parent = list(range(n))
        self.rank = [0] * n
        self._trail: List[Tuple[int, int, int]] = []

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            self._trail.append((-1, -1, -1))
            return False
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self._trail.append((rb, self.parent[rb], -1))
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self._trail.append((ra, self.rank[ra], -2))
            self.rank[ra] += 1
        return True

    def save(self) -> int:
        return len(self._trail)

    def restore(self, checkpoint: int) -> None:
        while len(self._trail) > checkpoint:
            idx, old, tag = self._trail.pop()
            if idx == -1:
                continue
            if tag == -1:
                self.parent[idx] = old
            else:
                self.rank[idx] = old


class SlitherlinkState:
    UNKNOWN = -1
    CROSS = 0
    LINE = 1

    def __init__(self, grid: List[List[Optional[int]]], *, on_edge_set=None):
        self.R = len(grid)
        self.C = len(grid[0])
        self.grid = grid
        self.h = [[self.UNKNOWN] * self.C for _ in range(self.R + 1)]
        self.v = [[self.UNKNOWN] * (self.C + 1) for _ in range(self.R)]
        self._on_edge_set = on_edge_set
        self.uf = RollbackDSU((self.R + 1) * (self.C + 1))
        self._cycle_detected = False
        self._trail: List[Tuple[str, int, int]] = []
        self._uf_marks: List[int] = []
        self._cycle_marks: List[bool] = []
        self._dirty_cells: set[Tuple[int, int]] = set()
        self._dirty_dots: set[Tuple[int, int]] = set()
        self._dead_cache: set[int] = set()
        self._line_count: int = 0
        self._zobrist: int = 0  # 增量 Zobrist 哈希
        self._unknown_edges: set[Tuple[str, int, int]] = set()
        for r in range(self.R + 1):
            for c in range(self.C):
                self._unknown_edges.add(("h", r, c))
        for r in range(self.R):
            for c in range(self.C + 1):
                self._unknown_edges.add(("v", r, c))

        # ── 预计算：每条边相邻的格子（加速 _edge_priority）──
        self._edge_cells: dict[Tuple[str, int, int], list] = {}
        for kind, r, c in self._unknown_edges:
            cells = []
            if kind == "h":
                if r > 0: cells.append((r - 1, c))
                if r < self.R: cells.append((r, c))
            else:
                if c > 0: cells.append((r, c - 1))
                if c < self.C: cells.append((r, c))
            self._edge_cells[(kind, r, c)] = cells

        # ── 预计算：3 格子的模式 6 数据（避免每次传播重建）──
        self._pat6_data: list = []
        for r in range(self.R):
            for c in range(self.C):
                if self.grid[r][c] != 3:
                    continue
                cell_set = {("h", r, c), ("v", r, c + 1), ("h", r + 1, c), ("v", r, c)}
                verts = [
                    (r, c,       ("v", r, c + 1), ("h", r + 1, c)),
                    (r, c + 1,   ("v", r, c),     ("h", r + 1, c)),
                    (r + 1, c + 1, ("v", r, c),   ("h", r, c)),
                    (r + 1, c,   ("v", r, c + 1), ("h", r, c)),
                ]
                # 预计算每个顶点的外部边列表
                outside_map = []
                for vr, vc, f1, f2 in verts:
                    outside = [(ek, er, ec) for ek, er, ec in self.dot_edges(vr, vc)
                               if (ek, er, ec) not in cell_set]
                    outside_map.append(((vr, vc, f1, f2), outside))
                self._pat6_data.append(outside_map)

        # ── 预计算：所有格子的四条边（加速 _propagate_numbers / _edge_priority）──
        self._cell_edges_cache: dict[Tuple[int, int], list] = {}
        for r in range(self.R):
            for c in range(self.C):
                self._cell_edges_cache[(r, c)] = self.cell_edges(r, c)

        # ── 预计算：每条边两端点的邻边列表（加速 _edge_priority）──
        self._edge_dots: dict[Tuple[str, int, int], list] = {}
        for kind, r, c in self._unknown_edges:
            if kind == "h":
                self._edge_dots[(kind, r, c)] = [
                    self.dot_edges(r, c), self.dot_edges(r, c + 1)
                ]
            else:
                self._edge_dots[(kind, r, c)] = [
                    self.dot_edges(r, c), self.dot_edges(r + 1, c)
                ]

        # ── 预计算：0/3 格子的位置（加速模式扫描）──
        self._cells_0: List[Tuple[int, int]] = []
        self._cells_3: List[Tuple[int, int]] = []
        for r in range(self.R):
            for c in range(self.C):
                v = self.grid[r][c]
                if v == 0:
                    self._cells_0.append((r, c))
                elif v == 3:
                    self._cells_3.append((r, c))
        self._cells_3_set: set[Tuple[int, int]] = set(self._cells_3)

        # 首次传播前标记全部为脏
        self._queue_all()

    def _vertex_index(self, r: int, c: int) -> int:
        return r * (self.C + 1) + c

    def get(self, kind: str, r: int, c: int) -> int:
        return self.h[r][c] if kind == "h" else self.v[r][c]

    def cell_edges(self, r: int, c: int):
        return [("h", r, c), ("v", r, c + 1), ("h", r + 1, c), ("v", r, c)]

    def dot_edges(self, r: int, c: int):
        edges = []
        if r > 0:
            edges.append(("v", r - 1, c))
        if r < self.R:
            edges.append(("v", r, c))
        if c > 0:
            edges.append(("h", r, c - 1))
        if c < self.C:
            edges.append(("h", r, c))
        return edges

    def set(self, kind: str, r: int, c: int, val: int) -> None:
        cur = self.get(kind, r, c)
        if cur != self.UNKNOWN and cur != val:
            raise Contradiction()
        if cur == val:
            return
        self._trail.append((kind, r, c))
        self._unknown_edges.discard((kind, r, c))
        self._zobrist ^= _zo(kind, r, c, val)  # O(1) 增量哈希
        if val == self.LINE:
            self._line_count += 1
        if kind == "h":
            self.h[r][c] = val
        else:
            self.v[r][c] = val

        if kind == "h":
            if r > 0:
                self._dirty_cells.add((r - 1, c))
            if r < self.R:
                self._dirty_cells.add((r, c))
            self._dirty_dots.add((r, c))
            self._dirty_dots.add((r, c + 1))
        else:
            if c > 0:
                self._dirty_cells.add((r, c - 1))
            if c < self.C:
                self._dirty_cells.add((r, c))
            self._dirty_dots.add((r, c))
            self._dirty_dots.add((r + 1, c))

        if val == self.LINE:
            self._uf_marks.append(self.uf.save())
            self._cycle_marks.append(self._cycle_detected)
            a = self._vertex_index(r, c)
            b = self._vertex_index(r, c + 1) if kind == "h" else self._vertex_index(r + 1, c)
            if not self.uf.union(a, b):
                self._cycle_detected = True

        if self._on_edge_set is not None:
            self._on_edge_set(self, kind, r, c, val)

    def save_state(self):
        return len(self._trail), len(self._uf_marks), len(self._cycle_marks)

    def restore_state(self, checkpoint) -> None:
        trail_cp, uf_cp, cycle_cp = checkpoint
        while len(self._trail) > trail_cp:
            kind, r, c = self._trail.pop()
            old_val = self.get(kind, r, c)
            self._zobrist ^= _zo(kind, r, c, old_val)  # 回退哈希
            if old_val == self.LINE:
                self._line_count -= 1
            self._unknown_edges.add((kind, r, c))
            if kind == "h":
                self.h[r][c] = self.UNKNOWN
            else:
                self.v[r][c] = self.UNKNOWN
        while len(self._uf_marks) > uf_cp:
            self.uf.restore(self._uf_marks.pop())
        while len(self._cycle_marks) > cycle_cp:
            self._cycle_detected = self._cycle_marks.pop()

    def _queue_all(self) -> None:
        for r in range(self.R):
            for c in range(self.C):
                self._dirty_cells.add((r, c))
        for r in range(self.R + 1):
            for c in range(self.C + 1):
                self._dirty_dots.add((r, c))

    def _propagate_numbers(self) -> bool:
        changed = False
        while self._dirty_cells:
            r, c = self._dirty_cells.pop()
            num = self.grid[r][c]
            if num is None:
                continue
            edges = self._cell_edges_cache[(r, c)]
            vals = [self.get(k, rr, cc) for k, rr, cc in edges]
            line = vals.count(self.LINE)
            cross = vals.count(self.CROSS)
            unknown = vals.count(self.UNKNOWN)
            if line > num or cross > 4 - num:
                raise Contradiction()
            if line == num:
                for (k, rr, cc), v in zip(edges, vals):
                    if v == self.UNKNOWN:
                        self.set(k, rr, cc, self.CROSS)
                        changed = True
            elif line + unknown == num:
                for (k, rr, cc), v in zip(edges, vals):
                    if v == self.UNKNOWN:
                        self.set(k, rr, cc, self.LINE)
                        changed = True
        return changed

    def _propagate_dots(self) -> bool:
        changed = False
        while self._dirty_dots:
            r, c = self._dirty_dots.pop()
            edges = self.dot_edges(r, c)
            vals = [self.get(k, rr, cc) for k, rr, cc in edges]
            line = vals.count(self.LINE)
            unknown = vals.count(self.UNKNOWN)
            if line > 2 or (line == 1 and unknown == 0):
                raise Contradiction()
            if line == 2:
                for (k, rr, cc), v in zip(edges, vals):
                    if v == self.UNKNOWN:
                        self.set(k, rr, cc, self.CROSS)
                        changed = True
            elif line == 1 and unknown == 1:
                for (k, rr, cc), v in zip(edges, vals):
                    if v == self.UNKNOWN:
                        self.set(k, rr, cc, self.LINE)
                        changed = True
            elif line == 0 and unknown == 1:
                for (k, rr, cc), v in zip(edges, vals):
                    if v == self.UNKNOWN:
                        self.set(k, rr, cc, self.CROSS)
                        changed = True
        return changed

    def _propagate_basic_patterns(self) -> bool:
        """高级局部模式匹配 —— 使用预计算位置，避免全盘扫描。"""
        changed = False
        R, C = self.R, self.C

        # ── 1. 水平相邻 3-3：外侧 + 共用边 = LINE ──
        for r, c in self._cells_3:
            if c + 1 < C and (r, c + 1) in self._cells_3_set:
                for cc in (c, c + 1, c + 2):
                    if self.v[r][cc] == self.UNKNOWN:
                        self.set("v", r, cc, self.LINE)
                        changed = True

        # ── 2. 垂直相邻 3-3：外侧 + 共用边 = LINE ──
        for r, c in self._cells_3:
            if r + 1 < R and (r + 1, c) in self._cells_3_set:
                for rr in (r, r + 1, r + 2):
                    if self.h[rr][c] == self.UNKNOWN:
                        self.set("h", rr, c, self.LINE)
                        changed = True

        # ── 3. 对角 3-3（左上-右下）──
        for r, c in self._cells_3:
            if r + 1 < R and c + 1 < C and (r + 1, c + 1) in self._cells_3_set:
                if self.h[r][c] == self.UNKNOWN:
                    self.set("h", r, c, self.LINE); changed = True
                if self.v[r][c] == self.UNKNOWN:
                    self.set("v", r, c, self.LINE); changed = True
                if self.h[r + 2][c + 1] == self.UNKNOWN:
                    self.set("h", r + 2, c + 1, self.LINE); changed = True
                if self.v[r + 1][c + 2] == self.UNKNOWN:
                    self.set("v", r + 1, c + 2, self.LINE); changed = True

        # ── 4. 对角 3-3（右上-左下）──
        for r, c in self._cells_3:
            if r + 1 < R and c - 1 >= 0 and (r + 1, c - 1) in self._cells_3_set:
                if self.h[r][c] == self.UNKNOWN:
                    self.set("h", r, c, self.LINE); changed = True
                if self.v[r][c + 1] == self.UNKNOWN:
                    self.set("v", r, c + 1, self.LINE); changed = True
                if self.h[r + 2][c - 1] == self.UNKNOWN:
                    self.set("h", r + 2, c - 1, self.LINE); changed = True
                if self.v[r + 1][c - 1] == self.UNKNOWN:
                    self.set("v", r + 1, c - 1, self.LINE); changed = True

        # ── 5. 角上 3：两条棋盘外侧的边 = LINE ──
        if self.grid[0][0] == 3:
            if self.h[0][0] == self.UNKNOWN:
                self.set("h", 0, 0, self.LINE); changed = True
            if self.v[0][0] == self.UNKNOWN:
                self.set("v", 0, 0, self.LINE); changed = True
        if self.grid[0][C - 1] == 3:
            if self.h[0][C - 1] == self.UNKNOWN:
                self.set("h", 0, C - 1, self.LINE); changed = True
            if self.v[0][C] == self.UNKNOWN:
                self.set("v", 0, C, self.LINE); changed = True
        if self.grid[R - 1][0] == 3:
            if self.h[R][0] == self.UNKNOWN:
                self.set("h", R, 0, self.LINE); changed = True
            if self.v[R - 1][0] == self.UNKNOWN:
                self.set("v", R - 1, 0, self.LINE); changed = True
        if self.grid[R - 1][C - 1] == 3:
            if self.h[R][C - 1] == self.UNKNOWN:
                self.set("h", R, C - 1, self.LINE); changed = True
            if self.v[R - 1][C] == self.UNKNOWN:
                self.set("v", R - 1, C, self.LINE); changed = True

        # ── 6. 3 的顶点已有外连线 → 远离该顶点的两边 = LINE ──
        for outside_map in self._pat6_data:
            for (vr, vc, f1, f2), outside_edges in outside_map:
                has_outside = any(
                    self.get(ek, er, ec) == self.LINE for ek, er, ec in outside_edges
                )
                if has_outside:
                    for fk, fr, fc in (f1, f2):
                        if self.get(fk, fr, fc) == self.UNKNOWN:
                            self.set(fk, fr, fc, self.LINE)
                            changed = True

        # ── 7. 0 格子的四条边显式设为 CROSS ──
        for r, c in self._cells_0:
            for kind, rr, cc in self._cell_edges_cache[(r, c)]:
                if self.get(kind, rr, cc) == self.UNKNOWN:
                    self.set(kind, rr, cc, self.CROSS)
                    changed = True

        # ── 8. 对角 0-3：3 靠近共享顶点的两条边 = LINE ──
        for r, c in self._cells_3:
            # 左上-右下：共享顶点 (r+1, c+1)
            if r > 0 and c > 0 and self.grid[r - 1][c - 1] == 0:
                if self.h[r][c] == self.UNKNOWN:
                    self.set("h", r, c, self.LINE); changed = True
                if self.v[r][c] == self.UNKNOWN:
                    self.set("v", r, c, self.LINE); changed = True
            # 3 在左上，0 在右下
            if r + 1 < R and c + 1 < C and self.grid[r + 1][c + 1] == 0:
                if self.h[r + 1][c] == self.UNKNOWN:
                    self.set("h", r + 1, c, self.LINE); changed = True
                if self.v[r][c + 1] == self.UNKNOWN:
                    self.set("v", r, c + 1, self.LINE); changed = True
            # 右上-左下 (3在右上)
            if r > 0 and c + 1 < C and self.grid[r - 1][c + 1] == 0:
                if self.h[r][c] == self.UNKNOWN:
                    self.set("h", r, c, self.LINE); changed = True
                if self.v[r][c + 1] == self.UNKNOWN:
                    self.set("v", r, c + 1, self.LINE); changed = True
            # 3在左下，0在右上
            if r + 1 < R and c > 0 and self.grid[r + 1][c - 1] == 0:
                if self.h[r + 1][c] == self.UNKNOWN:
                    self.set("h", r + 1, c, self.LINE); changed = True
                if self.v[r][c] == self.UNKNOWN:
                    self.set("v", r, c, self.LINE); changed = True

        return changed

    def _check_cycle(self) -> None:
        if not self._cycle_detected:
            return
        total_line = self._line_count
        if total_line < 4:
            return
        adj = {}
        V = (self.R + 1) * (self.C + 1)
        for r in range(self.R + 1):
            for c in range(self.C):
                if self.h[r][c] == self.LINE:
                    a = self._vertex_index(r, c)
                    b = self._vertex_index(r, c + 1)
                    adj.setdefault(a, []).append(b)
                    adj.setdefault(b, []).append(a)
        for r in range(self.R):
            for c in range(self.C + 1):
                if self.v[r][c] == self.LINE:
                    a = self._vertex_index(r, c)
                    b = self._vertex_index(r + 1, c)
                    adj.setdefault(a, []).append(b)
                    adj.setdefault(b, []).append(a)
        visited = [False] * V
        for start in list(adj):
            if visited[start]:
                continue
            stack = [start]
            comp = []
            visited[start] = True
            while stack:
                v = stack.pop()
                comp.append(v)
                for nb in adj[v]:
                    if not visited[nb]:
                        visited[nb] = True
                        stack.append(nb)
            if all(len(adj[v]) == 2 for v in comp):
                edges = sum(len(adj[v]) for v in comp) // 2
                if edges >= 4 and edges < total_line:
                    raise Contradiction()

    def _state_key(self) -> int:
        """O(1) 增量 Zobrist 哈希 —— 替代原来的 O(N) 全盘序列化。"""
        return self._zobrist

    def propagate(self) -> None:
        """约束传播：反复应用数字/点/模式直到不动点。"""
        while self._dirty_cells or self._dirty_dots:
            if self._propagate_numbers():
                continue
            if self._propagate_dots():
                continue
            if self._propagate_basic_patterns():
                continue
            break  # 不动点
        self._check_cycle()

    def is_complete(self) -> bool:
        return all(self.UNKNOWN not in row for row in self.h) and all(self.UNKNOWN not in row for row in self.v)

    def propagate_light(self) -> None:
        """轻量传播（用于探测）：仅运行数字+点约束，跳过 O(RC) 模式扫描。
        探测只需局部一致性检查，矛盾在轻量传播层面就能捕捉。"""
        while self._dirty_cells or self._dirty_dots:
            if self._propagate_numbers():
                continue
            if self._propagate_dots():
                continue
            break

    def is_valid_solution(self) -> bool:
        for r in range(self.R):
            for c in range(self.C):
                num = self.grid[r][c]
                if num is None:
                    continue
                if sum(1 for k, rr, cc in self.cell_edges(r, c) if self.get(k, rr, cc) == self.LINE) != num:
                    return False
        for r in range(self.R + 1):
            for c in range(self.C + 1):
                deg = sum(1 for k, rr, cc in self.dot_edges(r, c) if self.get(k, rr, cc) == self.LINE)
                if deg not in (0, 2):
                    return False
        edges = []
        for r in range(self.R + 1):
            for c in range(self.C):
                if self.h[r][c] == self.LINE:
                    edges.append((self._vertex_index(r, c), self._vertex_index(r, c + 1)))
        for r in range(self.R):
            for c in range(self.C + 1):
                if self.v[r][c] == self.LINE:
                    edges.append((self._vertex_index(r, c), self._vertex_index(r + 1, c)))
        if not edges:
            return False
        adj = {}
        for a, b in edges:
            adj.setdefault(a, []).append(b)
            adj.setdefault(b, []).append(a)
        for vs in adj.values():
            if len(vs) != 2:
                return False
        start = next(iter(adj))
        seen = {start}
        q = deque([start])
        while q:
            u = q.popleft()
            for w in adj[u]:
                if w not in seen:
                    seen.add(w)
                    q.append(w)
        return len(seen) == len(adj)

    def find_unknown(self) -> Optional[Tuple[str, int, int]]:
        if not self._unknown_edges:
            return None
        best = None
        best_score = -1
        for kind, r, c in self._unknown_edges:
            score = self._edge_priority(kind, r, c)
            if score > best_score:
                best_score = score
                best = (kind, r, c)
        return best

    def _top_unknown_edges(self, k: int = 30) -> List[Tuple[str, int, int]]:
        """返回优先级最高的 k 条未知边（用于 probing），O(N log K）。"""
        if not self._unknown_edges:
            return []
        # 使用堆维护 Top-K，避免全排序
        heap: List[Tuple[int, int, str, int, int]] = []
        tie = 0
        for kind, r, c in self._unknown_edges:
            score = self._edge_priority(kind, r, c)
            if len(heap) < k:
                heapq.heappush(heap, (score, tie, kind, r, c))
            elif score > heap[0][0]:
                heapq.heapreplace(heap, (score, tie, kind, r, c))
            tie += 1
        # 按分数降序返回
        result = sorted(heap, key=lambda x: x[0], reverse=True)
        return [(kind, r, c) for _, _, kind, r, c in result]

    def _edge_priority(self, kind: str, r: int, c: int) -> int:
        """改进的启发式：优先选择约束强的边进行分支。"""
        score = 0
        for rr, cc in self._edge_cells[(kind, r, c)]:
            num = self.grid[rr][cc]
            if num is not None:
                score += 20
                vals = [self.get(k, er, ec) for k, er, ec in self._cell_edges_cache[(rr, cc)]]
                known = sum(v != self.UNKNOWN for v in vals)
                line_count = vals.count(self.LINE)
                unknown = 4 - known
                if unknown > 0:
                    urgency = (num - line_count) / unknown
                    score += int(urgency * 15) + known * 5
                if num == 0 or num == 3:
                    score += 10
            else:
                score += 2

        for dot_set in self._edge_dots[(kind, r, c)]:
            line_deg = sum(1 for ek, er, ec in dot_set if self.get(ek, er, ec) == self.LINE)
            if line_deg == 1:
                score += 30
            elif line_deg == 2:
                score -= 50

        return score

    def solve(self) -> Optional["SlitherlinkState"]:
        key = self._state_key()
        if key in self._dead_cache:
            return None
        try:
            self.propagate()
        except Contradiction:
            self._dead_cache.add(key)
            return None
        if self.is_complete():
            result = self if self.is_valid_solution() else None
            if result is None:
                self._dead_cache.add(key)
            return result

        # ── Failed Literal Probing ──
        # 分支前对高优先级边进行探测：尝试每种赋值 + 轻量传播
        # 若某赋值立即导致矛盾 → 另一赋值必然正确，直接确定
        probed = False
        for kind, r, c in self._top_unknown_edges(k=20):
            if self.get(kind, r, c) != self.UNKNOWN:
                continue
            survived = {}
            for trial_val in (self.LINE, self.CROSS):
                cp = self.save_state()
                try:
                    self.set(kind, r, c, trial_val)
                    self.propagate()  # 完整传播：数字+点+模式，确保捕捉所有矛盾
                    survived[trial_val] = True
                except Contradiction:
                    survived[trial_val] = False
                self.restore_state(cp)
            if survived.get(self.LINE) and not survived.get(self.CROSS):
                self.set(kind, r, c, self.LINE)
                probed = True
                break
            if survived.get(self.CROSS) and not survived.get(self.LINE):
                self.set(kind, r, c, self.CROSS)
                probed = True
                break
        if probed:
            return self.solve()  # 状态已更新，重新求解

        edge = self.find_unknown()
        if edge is None:
            result = self if self.is_valid_solution() else None
            if result is None:
                self._dead_cache.add(key)
            return result
        kind, r, c = edge
        # 智能分支排序：根据相邻格子约束力选择先试 LINE 还是 CROSS
        prefer_line_first = True
        for rr, cc in self._edge_cells[(kind, r, c)]:
            num = self.grid[rr][cc]
            if num is not None:
                vals = [self.get(k, er, ec) for k, er, ec in self._cell_edges_cache[(rr, cc)]]
                line_count = vals.count(self.LINE)
                unknown = vals.count(self.UNKNOWN)
                if unknown > 0:
                    need = num - line_count
                    if need == unknown:  # 全部剩余边必须是 LINE
                        prefer_line_first = True
                        break
                    if need == 0:  # 全部剩余边必须是 CROSS
                        prefer_line_first = False
                        break
        order = (self.LINE, self.CROSS) if prefer_line_first else (self.CROSS, self.LINE)
        for val in order:
            cp = self.save_state()
            try:
                self.set(kind, r, c, val)
                result = self.solve()
                if result is not None:
                    return result
            except Contradiction:
                pass
            self.restore_state(cp)
        self._dead_cache.add(key)
        return None

    def render(self) -> str:
        lines = []
        for r in range(self.R + 1):
            row = []
            for c in range(self.C + 1):
                row.append("+")
                if c < self.C:
                    if self.h[r][c] == self.LINE:
                        row.append("---")
                    elif self.h[r][c] == self.CROSS:
                        row.append(" x ")
                    else:
                        row.append("   ")
            lines.append("".join(row))
            if r < self.R:
                mid = []
                for c in range(self.C + 1):
                    if self.v[r][c] == self.LINE:
                        mid.append("|")
                    elif self.v[r][c] == self.CROSS:
                        mid.append("x")
                    else:
                        mid.append(" ")
                    if c < self.C:
                        num = self.grid[r][c]
                        mid.append(f" {num if num is not None else ' '} ")
                lines.append("".join(mid))
        return "\n".join(lines)


def parse_puzzle(text: str) -> List[List[Optional[int]]]:
    grid: List[List[Optional[int]]] = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        row = []
        for token in line.split():
            if token in (".", "_", "", "-"):
                row.append(None)
            else:
                row.append(int(token))
        if row:
            grid.append(row)
    return grid


def solve_puzzle(grid: List[List[Optional[int]]]) -> Optional[SlitherlinkState]:
    return SlitherlinkState(grid).solve()
