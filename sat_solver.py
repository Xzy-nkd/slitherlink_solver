"""
SAT-based Slitherlink solver using python-sat (Glucose3).
Supports both iterative loop elimination and direct single-loop encoding.
"""
from typing import List, Optional, Tuple, Dict, Set
from pysat.solvers import Glucose3
from pysat.card import CardEnc


def _pre_propagate(grid: List[List[Optional[int]]]):
    """Run constraint propagation to fix obvious edges.
    Returns (pre_fixed_h, pre_fixed_v) with known values, or None on contradiction."""
    from solver import SlitherlinkState
    state = SlitherlinkState(grid)
    try:
        state.propagate()
    except Exception:
        return None
    R, C = len(grid), len(grid[0])
    h = [[-1] * C for _ in range(R + 1)]
    v = [[-1] * (C + 1) for _ in range(R)]
    for r in range(R + 1):
        for c in range(C):
            h[r][c] = state.h[r][c]
    for r in range(R):
        for c in range(C + 1):
            v[r][c] = state.v[r][c]
    return h, v


def solve_sat(grid: List[List[Optional[int]]], max_iterations: int = 200,
              use_preprop: bool = True):
    """Solve Slitherlink using SAT with iterative loop elimination.
    
    Args:
        grid: Puzzle grid (0-4 or None for empty)
        max_iterations: Max loop elimination iterations
        use_preprop: Run constraint propagation before SAT (recommended)
    """
    R, C = len(grid), len(grid[0])

    # ── Step 1: Pre-propagation ──
    if use_preprop:
        pre = _pre_propagate(grid)
        if pre is None:
            return None
        pre_h, pre_v = pre
    else:
        pre_h = [[-1] * C for _ in range(R + 1)]
        pre_v = [[-1] * (C + 1) for _ in range(R)]

    # ── Step 2: Build variable mapping ONLY for unknown edges ──
    var_map: Dict[Tuple[str, int, int], int] = {}
    unknown_edges: List[Tuple[str, int, int]] = []
    vid = 1

    for r in range(R + 1):
        for c in range(C):
            if pre_h[r][c] == -1:
                var_map[("h", r, c)] = vid
                unknown_edges.append(("h", r, c))
                vid += 1
    for r in range(R):
        for c in range(C + 1):
            if pre_v[r][c] == -1:
                var_map[("v", r, c)] = vid
                unknown_edges.append(("v", r, c))
                vid += 1

    solver = Glucose3()

    # ── Step 3: Number constraints ──
    for r in range(R):
        for c in range(C):
            n = grid[r][c]
            if n is None:
                continue
            edges = [("h", r, c), ("v", r, c + 1), ("h", r + 1, c), ("v", r, c)]

            # Count pre-fixed LINEs
            pre_lines = 0
            unknown_vars = []
            for e in edges:
                val = pre_h[e[1]][e[2]] if e[0] == "h" else pre_v[e[1]][e[2]]
                if val == 1:
                    pre_lines += 1
                elif val == -1:
                    unknown_vars.append(var_map[e])

            remaining = n - pre_lines
            if remaining < 0 or remaining > len(unknown_vars):
                return None  # contradiction

            if unknown_vars:
                cnf = CardEnc.equals(unknown_vars, remaining, top_id=vid)
                for clause in cnf:
                    solver.add_clause(clause)
                for clause in cnf:
                    for lit in clause:
                        vid = max(vid, abs(lit) + 1)

    # ── Step 4: Vertex degree constraints (compact encoding) ──
    for r in range(R + 1):
        for c in range(C + 1):
            edge_info = []  # (var_id, is_pre_line)
            if r > 0:
                e = ("v", r - 1, c)
                val = pre_v[r - 1][c]
                edge_info.append((var_map.get(e), val))
            if r < R:
                e = ("v", r, c)
                val = pre_v[r][c]
                edge_info.append((var_map.get(e), val))
            if c > 0:
                e = ("h", r, c - 1)
                val = pre_h[r][c - 1]
                edge_info.append((var_map.get(e), val))
            if c < C:
                e = ("h", r, c)
                val = pre_h[r][c]
                edge_info.append((var_map.get(e), val))

            deg = len(edge_info)
            pre_lines = sum(1 for _, v in edge_info if v == 1)
            pre_cross = sum(1 for _, v in edge_info if v == 0)

            # Collect unknown edge vars for this vertex
            unk_vars = [vid for vid, v in edge_info if v == -1 and vid is not None]
            # Pre-fixed LINE vars (already determined)
            fixed_lines = [vid for vid, v in edge_info if v == 1 and vid is not None]

            if deg < 2:
                if pre_lines > 0:
                    return None  # can't satisfy degree 0 or 2
                continue

            # Total LINEs at vertex = pre_lines + lines from unk_vars
            # Must be 0 or 2

            if pre_lines > 2:
                return None  # contradiction

            if pre_lines == 2:
                # All unknown edges must be CROSS
                for uv in unk_vars:
                    solver.add_clause([-uv])
                continue

            if pre_lines == 1:
                # Exactly one more LINE needed from unk_vars
                if len(unk_vars) == 0:
                    return None
                # At least one unk_var is LINE
                solver.add_clause(unk_vars)
                # At most one unk_var is LINE: pairwise clauses
                for i in range(len(unk_vars)):
                    for j in range(i + 1, len(unk_vars)):
                        solver.add_clause([-unk_vars[i], -unk_vars[j]])
                continue

            # pre_lines == 0: need 0 or 2 LINEs from unk_vars
            if len(unk_vars) == 0:
                continue  # satisfied (0 lines)

            # "degree ≠ 1": if any unknown edge is true,
            # at least one other (unknown or pre-fixed) must be true
            all_vars = unk_vars + fixed_lines
            for v1 in unk_vars:
                others = [v for v in all_vars if v != v1]
                if others:
                    solver.add_clause([-v1] + others)

            # "degree ≤ 2": at most 2 LINEs total
            max_additional = 2 - pre_lines
            if max_additional <= 0:
                for uv in unk_vars:
                    solver.add_clause([-uv])
            elif max_additional == 1 and len(unk_vars) >= 2:
                # At most 1 of unk_vars: pairwise
                for i in range(len(unk_vars)):
                    for j in range(i + 1, len(unk_vars)):
                        solver.add_clause([-unk_vars[i], -unk_vars[j]])
            elif len(unk_vars) >= 3:
                # At most 2 of unk_vars: forbid any 3 being true simultaneously
                # For 3 vars, 1 clause. For 4 vars, 4 clauses.
                if len(unk_vars) == 3:
                    solver.add_clause([-v for v in unk_vars])
                elif len(unk_vars) == 4 and max_additional == 2:
                    # Need at most 2 → forbid any triple
                    for i in range(len(unk_vars)):
                        for j in range(i + 1, len(unk_vars)):
                            for k in range(j + 1, len(unk_vars)):
                                solver.add_clause([-unk_vars[i], -unk_vars[j], -unk_vars[k]])

    # ── Step 5: Iterative loop elimination ──
    for iteration in range(max_iterations):
        if not solver.solve():
            return None

        model = solver.get_model()
        # Extract solution
        h = [row[:] for row in pre_h]
        v = [row[:] for row in pre_v]
        for (kind, r, c), var_id in var_map.items():
            val = 1 if model[var_id - 1] > 0 else 0
            if kind == "h":
                h[r][c] = val
            else:
                v[r][c] = val

        # Check for single loop
        if _is_single_loop(h, v, R, C):
            return _make_state(grid, R, C, h, v)

        # Find connected components and block each
        loops = _find_components(h, v, R, C)
        for comp_edges in loops:
            if not comp_edges:
                continue
            # Block: not all edges in this component can be LINE
            clause = [-var_map[e] for e in comp_edges if e in var_map]
            if clause:
                solver.add_clause(clause)

    return None


def _find_components(h, v, R, C):
    """Find all connected components of LINE edges.
    Returns list of lists of edge keys."""
    # Build adjacency
    adj = {}
    for r in range(R + 1):
        for c in range(C):
            if h[r][c] == 1:
                a = r * (C + 1) + c
                b = r * (C + 1) + c + 1
                adj.setdefault(a, set()).add(b)
                adj.setdefault(b, set()).add(a)
    for r in range(R):
        for c in range(C + 1):
            if v[r][c] == 1:
                a = r * (C + 1) + c
                b = (r + 1) * (C + 1) + c
                adj.setdefault(a, set()).add(b)
                adj.setdefault(b, set()).add(a)

    if not adj:
        return []

    visited = set()
    components = []

    for start in adj:
        if start in visited:
            continue
        stack = [start]
        comp = set()
        while stack:
            vtx = stack.pop()
            if vtx in comp:
                continue
            comp.add(vtx)
            visited.add(vtx)
            for nb in adj.get(vtx, ()):
                if nb not in comp:
                    stack.append(nb)

        # Collect edges in this component
        edges = set()
        for vtx in comp:
            r, c = divmod(vtx, C + 1)
            if r > 0 and v[r - 1][c] == 1:
                edges.add(("v", r - 1, c))
            if r < R and v[r][c] == 1:
                edges.add(("v", r, c))
            if c > 0 and h[r][c - 1] == 1:
                edges.add(("h", r, c - 1))
            if c < C and h[r][c] == 1:
                edges.add(("h", r, c))
        components.append(list(edges))

    return components


def _is_single_loop(h, v, R, C):
    """Check if the solution forms a single closed loop."""
    components = _find_components(h, v, R, C)
    if len(components) != 1:
        return False
    # All LINE edges must be in the single component
    total_lines = sum(1 for row in h for x in row if x == 1)
    total_lines += sum(1 for row in v for x in row if x == 1)
    return total_lines == len(components[0])


def _make_state(grid, R, C, h, v):
    """Create a SlitherlinkState from grid and edge values."""
    from solver import SlitherlinkState
    state = SlitherlinkState(grid)
    for r in range(R + 1):
        for c in range(C):
            if h[r][c] != state.UNKNOWN:
                state.h[r][c] = h[r][c]
    for r in range(R):
        for c in range(C + 1):
            if v[r][c] != state.UNKNOWN:
                state.v[r][c] = v[r][c]
    return state
