from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import List, Optional, Tuple
import copy


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
        self._dead_cache: set[bytes] = set()

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
            edges = self.cell_edges(r, c)
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
        changed = False
        for r in range(self.R):
            for c in range(self.C - 1):
                if self.grid[r][c] == 3 and self.grid[r][c + 1] == 3:
                    if self.v[r][c] == self.UNKNOWN:
                        self.set("v", r, c, self.LINE)
                        changed = True
                    if self.v[r][c + 2] == self.UNKNOWN:
                        self.set("v", r, c + 2, self.LINE)
                        changed = True
        for r in range(self.R - 1):
            for c in range(self.C):
                if self.grid[r][c] == 3 and self.grid[r + 1][c] == 3:
                    if self.h[r][c] == self.UNKNOWN:
                        self.set("h", r, c, self.LINE)
                        changed = True
                    if self.h[r + 2][c] == self.UNKNOWN:
                        self.set("h", r + 2, c, self.LINE)
                        changed = True
        return changed

    def _check_cycle(self) -> None:
        if not self._cycle_detected:
            return
        total_line = sum(v == self.LINE for row in self.h for v in row) + sum(v == self.LINE for row in self.v for v in row)
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

    def _state_key(self) -> bytes:
        data = bytearray()
        for row in self.h:
            for v in row:
                data.append(v + 1)
        data.append(255)
        for row in self.v:
            for v in row:
                data.append(v + 1)
        return bytes(data)

    def propagate(self) -> None:
        self._queue_all()
        while self._dirty_cells or self._dirty_dots:
            changed = False
            changed |= self._propagate_numbers()
            changed |= self._propagate_dots()
            changed |= self._propagate_basic_patterns()
            if changed:
                continue
        self._check_cycle()

    def is_complete(self) -> bool:
        return all(self.UNKNOWN not in row for row in self.h) and all(self.UNKNOWN not in row for row in self.v)

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
        best = None
        best_score = -1
        for r in range(self.R + 1):
            for c in range(self.C):
                if self.h[r][c] == self.UNKNOWN:
                    score = self._edge_priority("h", r, c)
                    if score > best_score:
                        best_score = score
                        best = ("h", r, c)
        for r in range(self.R):
            for c in range(self.C + 1):
                if self.v[r][c] == self.UNKNOWN:
                    score = self._edge_priority("v", r, c)
                    if score > best_score:
                        best_score = score
                        best = ("v", r, c)
        return best

    def _edge_priority(self, kind: str, r: int, c: int) -> int:
        score = 0
        cells = []
        if kind == "h":
            if r > 0:
                cells.append((r - 1, c))
            if r < self.R:
                cells.append((r, c))
        else:
            if c > 0:
                cells.append((r, c - 1))
            if c < self.C:
                cells.append((r, c))
        for rr, cc in cells:
            if self.grid[rr][cc] is not None:
                score += 10
                vals = [self.get(k, er, ec) for k, er, ec in self.cell_edges(rr, cc)]
                known = sum(v != self.UNKNOWN for v in vals)
                unknown = 4 - known
                score += (4 - unknown) * 3 + known * 2
            else:
                score += 1
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
        edge = self.find_unknown()
        if edge is None:
            result = self if self.is_valid_solution() else None
            if result is None:
                self._dead_cache.add(key)
            return result
        kind, r, c = edge
        for val in (self.LINE, self.CROSS):
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
