"""Conflict graph analysis layer for scheduling intelligence.

Represents relationships between classes based on shared lecturers,
shared student groups, and classroom dependencies. Each class is a
node and edges represent scheduling conflicts that prevent simultaneous
placement. The graph enables:

  1. Graph-aware class ordering (highest-conflict classes first)
  2. Targeted LNS destroy of structurally connected clusters
  3. Focused neighbor-impact scoring for placement decisions

No external graph libraries — uses simple adjacency lists and BFS.
"""

from collections import deque

from scheduler_app.logic import student_targets_conflict
from scheduler_app.models import needs_physical_room, cls_key


# ══════════════════════════════════════════════════════════════════════════
#  CONFLICT GRAPH DATA STRUCTURE
# ══════════════════════════════════════════════════════════════════════════

class ConflictGraph:
    """Adjacency-list conflict graph with typed, weighted edges.

    Nodes are class dicts indexed by position. Edges carry a conflict
    type ('lecturer', 'group', 'room_constraint') and a weight.
    """

    def __init__(self, classes):
        """
        Args:
            classes: list of class dicts to use as graph nodes.
        """
        self.nodes = list(classes)
        self.index_map = {cls_key(cls): i for i, cls in enumerate(classes)}
        # adjacency[i] = [(neighbor_idx, conflict_type, weight), ...]
        self.adjacency = {i: [] for i in range(len(classes))}
        # Track existing edges to avoid duplicates
        self._edge_set = set()

    def add_edge(self, i, j, conflict_type, weight=1.0):
        """Add an undirected edge between class indices i and j.

        Skips if the exact (i, j, type) edge already exists.
        """
        if i == j:
            return
        key = (min(i, j), max(i, j), conflict_type)
        if key in self._edge_set:
            return
        self._edge_set.add(key)
        self.adjacency[i].append((j, conflict_type, weight))
        self.adjacency[j].append((i, conflict_type, weight))

    def degree(self, i):
        """Return the number of edges incident to node i."""
        return len(self.adjacency.get(i, []))

    def neighbors(self, i):
        """Return list of unique neighbor indices for node i."""
        seen = set()
        result = []
        for nbr, _, _ in self.adjacency.get(i, []):
            if nbr not in seen:
                seen.add(nbr)
                result.append(nbr)
        return result

    def neighbors_by_type(self, i, conflict_type):
        """Return neighbor indices connected by a specific conflict type."""
        return [nbr for nbr, ctype, _ in self.adjacency.get(i, [])
                if ctype == conflict_type]

    def edge_weight_sum(self, i):
        """Sum of weights of all edges incident to node i."""
        return sum(w for _, _, w in self.adjacency.get(i, []))

    def get_index(self, cls):
        """Map a class dict to its graph index via cls_key()."""
        return self.index_map.get(cls_key(cls))

    def subgraph_indices(self, seed, max_depth=2):
        """BFS from seed up to max_depth, return set of reachable indices."""
        visited = {seed}
        queue = deque([(seed, 0)])
        while queue:
            node, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for nbr in self.neighbors(node):
                if nbr not in visited:
                    visited.add(nbr)
                    queue.append((nbr, depth + 1))
        return visited

    def total_edges(self):
        """Return the total number of unique edges in the graph."""
        return len(self._edge_set)

    def __len__(self):
        return len(self.nodes)


# ══════════════════════════════════════════════════════════════════════════
#  CONFLICT GRAPH BUILDER
# ══════════════════════════════════════════════════════════════════════════

class ConflictGraphBuilder:
    """Constructs a conflict graph from schedule data.

    Edge creation rules:
      - Lecturer edge: two classes share the same lecturer (weight=1.0)
      - Group edge: active targets conflict for the class pair (weight=1.0)
      - Room constraint edge: both classes have required_classrooms
        and their intersection is small (≤2 rooms), indicating
        they compete for scarce rooms (weight=0.5)
    """

    def __init__(self, state, classes):
        """
        Args:
            state: Schedule state dict.
            classes: List of class dicts to include as nodes.
        """
        self.state = state
        self.classes = classes

    def build(self):
        """Construct and return a ConflictGraph."""
        graph = ConflictGraph(self.classes)
        self._build_lecturer_edges(graph)
        self._build_group_edges(graph)
        self._build_room_edges(graph)
        return graph

    def _build_lecturer_edges(self, graph):
        """Group classes by lecturer, add edges between all pairs."""
        lect_groups = {}
        for i, cls in enumerate(self.classes):
            lect = cls.get("lecturer")
            if lect:
                lect_groups.setdefault(lect, []).append(i)

        for lect, indices in lect_groups.items():
            for a in range(len(indices)):
                for b in range(a + 1, len(indices)):
                    graph.add_edge(indices[a], indices[b],
                                   "lecturer", weight=1.0)

    def _build_group_edges(self, graph):
        """Check all pairs for overlapping student groups."""
        n = len(self.classes)
        for i in range(n):
            targets_i = self.classes[i].get("targets", [])
            if not targets_i:
                continue
            for j in range(i + 1, n):
                targets_j = self.classes[j].get("targets", [])
                if targets_j and student_targets_conflict(
                        self.classes[i], targets_i,
                        self.classes[j], targets_j):
                    graph.add_edge(i, j, "group", weight=1.0)

    def _build_room_edges(self, graph):
        """Add edges for classes competing for scarce rooms."""
        n = len(self.classes)
        for i in range(n):
            if not needs_physical_room(self.classes[i]):
                continue
            req_i = self.classes[i].get("required_classrooms", [])
            if not req_i:
                continue
            set_i = set(req_i)
            for j in range(i + 1, n):
                if not needs_physical_room(self.classes[j]):
                    continue
                req_j = self.classes[j].get("required_classrooms", [])
                if not req_j:
                    continue
                overlap = set_i & set(req_j)
                if overlap and len(overlap) <= 2:
                    graph.add_edge(i, j, "room_constraint", weight=0.5)


# ══════════════════════════════════════════════════════════════════════════
#  CONFLICT ANALYZER
# ══════════════════════════════════════════════════════════════════════════

# Weight for conflict degree in combined difficulty ranking.
# Each conflict edge is equivalent to ~150 fewer valid placements
# relative to the validator's valid_count * 1000 base.
DEGREE_WEIGHT = 150


class ConflictAnalyzer:
    """Computes graph analytics for scheduling decisions.

    Uses the conflict graph to produce difficulty rankings,
    identify connected components, and measure local density.
    """

    def __init__(self, graph, validator=None):
        """
        Args:
            graph: ConflictGraph instance.
            validator: Optional ConstraintValidator for computing
                       base scheduling difficulty.
        """
        self.graph = graph
        self.validator = validator

    def difficulty_ranking(self, classes):
        """Produce class ordering combining graph degree with validator
        difficulty.

        Classes with more conflict edges and fewer valid placements
        are scheduled first (lower combined score = harder).

        Returns:
            List of class dicts sorted hardest-first.
        """
        scored = []
        for cls in classes:
            idx = self.graph.get_index(cls)
            base = (self.validator.scheduling_difficulty(cls)
                    if self.validator else 0)
            degree = self.graph.degree(idx) if idx is not None else 0
            weighted_degree = self.graph.edge_weight_sum(idx) if idx is not None else 0
            combined = base - DEGREE_WEIGHT * weighted_degree
            scored.append((cls, combined))
        scored.sort(key=lambda x: x[1])
        return [cls for cls, _ in scored]

    def connected_components(self):
        """Find connected components using iterative BFS.

        Returns:
            List of lists of class indices. Each inner list is a
            connected component.
        """
        visited = set()
        components = []
        for i in range(len(self.graph)):
            if i in visited:
                continue
            component = []
            queue = deque([i])
            visited.add(i)
            while queue:
                node = queue.popleft()
                component.append(node)
                for nbr in self.graph.neighbors(node):
                    if nbr not in visited:
                        visited.add(nbr)
                        queue.append(nbr)
            components.append(component)
        return components

    def node_density(self, i):
        """Local edge density: edges among neighbors / max possible edges.

        Measures how tightly clustered a node's neighborhood is.
        Returns float in [0, 1]. Returns 0.0 if fewer than 2 neighbors.
        """
        nbrs = self.graph.neighbors(i)
        n = len(nbrs)
        if n < 2:
            return 0.0
        nbr_set = set(nbrs)
        edges_among = 0
        for nbr in nbrs:
            for nn, _, _ in self.graph.adjacency.get(nbr, []):
                if nn in nbr_set and nn > nbr:
                    edges_among += 1
        max_edges = n * (n - 1) / 2
        return edges_among / max_edges

    def densest_subgraph_seed(self, eligible_indices):
        """Find the node in eligible_indices whose neighborhood has
        the highest density. Used by ConflictGraphDestroy to pick
        destroy seeds.

        Returns:
            Index with highest density, or None if empty.
        """
        if not eligible_indices:
            return None
        best_idx = None
        best_score = -1.0
        for idx in eligible_indices:
            # Combine degree with density for seed selection
            degree = self.graph.degree(idx)
            density = self.node_density(idx)
            score = degree * (1.0 + density)
            if score > best_score:
                best_score = score
                best_idx = idx
        return best_idx

    def neighbor_classes(self, cls):
        """Return list of class dicts that are graph neighbors of cls."""
        idx = self.graph.get_index(cls)
        if idx is None:
            return []
        return [self.graph.nodes[nbr] for nbr in self.graph.neighbors(idx)]
