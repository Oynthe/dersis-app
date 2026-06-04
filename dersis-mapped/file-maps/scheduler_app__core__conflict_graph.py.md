# File: `scheduler_app/core/conflict_graph.py`

## 1. File Role
Adjacency-list graph of inter-class conflicts (shared lecturer, shared group, room constraints). Drives graph-aware ordering, targeted LNS destroy of structurally connected clusters, and focused neighbor-impact scoring.

## 2. Why this file matters
Supporting. Used by the optimizer to make smarter decisions; not strictly required for correctness.

## 3. Imports and Dependencies
- stdlib: `collections.deque`.
- Internal: `logic.targets_overlap`, `models.{needs_physical_room, cls_key}`.

## 4. Main Symbols
| Symbol | Purpose |
|--------|---------|
| `ConflictGraph(classes)` | Adjacency-list with typed weighted edges. `add_edge(i, j, conflict_type, weight=1.0)`, `degree(i)`, `neighbors(i)`, `total_edges()`. |
| `ConflictGraphBuilder(state, classes)` | Walks all pairs and emits edges based on lecturer/group/room overlap. `.build()` → `ConflictGraph`. |
| `ConflictAnalyzer(graph, validator)` | Analyses the graph: `connected_components()` (BFS), centrality measures, cluster sizes. |

## 5. Block-by-block code map
| Lines | Block | What |
|-------|-------|------|
| 1–13 | docstring | |
| 16 | import deque | for BFS. |
| 20–25 | imports from logic + models | |
| 90–135 | `ConflictGraph.__init__`, `add_edge`, `degree`, neighbors | Core data structure. |
| 135–~220 | `ConflictGraphBuilder` | Pair iteration + edge insertion. |
| ~220–306 | `ConflictAnalyzer` | BFS, cluster cohesion metrics. |

## 6. Runtime Behavior
Built once at the start of an optimization. Cheap to query.

## 7. Data Flow
- In: state + list of classes.
- Out: graph + analysis dicts.

## 8. UI Flow
Not applicable directly; metrics surface via `analyze_conflict_graph` and `analyze_schedule`.

## 9. Error Handling and Edge Cases
- `add_edge` deduplicates based on `(min(i,j), max(i,j), type)`.
- Empty `classes` list → empty graph; analyzer returns 0 components and 0.0 averages.

## 10. Integration Points
- Consumed by `ScheduleOptimizer` (greedy ordering, ConflictClusterDestroy), `PlacementScorer.score` (neighbor impact penalty), `logic.analyze_conflict_graph`.

## 11. Risks and Maintenance Notes
- Edges are typed (`lecturer` / `group` / `room_constraint`). If you add a new constraint type, update the builder.
- BFS uses iterative traversal — safe for large graphs.

## 12. Mini Summary
Conflict-graph helper. Powers smarter ordering, cluster-aware destroy, and neighbor-impact scoring.
