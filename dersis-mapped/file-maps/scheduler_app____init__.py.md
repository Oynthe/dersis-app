# File: `scheduler_app/__init__.py`

## 1. File Role
Top-level package initialiser. Also installs a meta-path finder/loader that transparently maps old flat imports (`scheduler_app.models`, `scheduler_app.logic`, …) to their new sub-package locations (`scheduler_app.core.models`, `scheduler_app.core.logic`, …).

## 2. Why this file matters
**Critical.** Many call sites (and tests) still use flat-style imports. Breaking this shim would force a code-wide audit.

## 3. Imports and Dependencies
- stdlib: `importlib`, `sys`, `importlib.abc.{MetaPathFinder, Loader}`, `importlib.machinery.ModuleSpec`.
- No third-party.
- No internal imports at module-init time.

## 4. Main Symbols
| Symbol | Lines | Purpose |
|--------|-------|---------|
| `_SHIM_MAP` | 21–56 | Dict: old flat path → new sub-package path. Covers `models`, `logic`, `constants`, all `core/*`, all `learning/*`, all `ui/*` shortcuts used by external callers. |
| `_ShimFinder` | 59–70 | `MetaPathFinder` subclass. Implements both `find_module` (legacy, ignored on 3.12+) and `find_spec`. |
| `_ShimLoader` | 73–85 | `Loader` subclass. `exec_module` imports the real module and copies its `__dict__` onto the alias. |
| Install hook | 88–90 | `if not any(isinstance(f, _ShimFinder)…): sys.meta_path.insert(0, _ShimFinder())`. |

## 5. Block-by-block code map
| Lines | Block | What | Why | Side effects |
|-------|-------|------|-----|--------------|
| 1–14 | docstring | Documents the package layout and the shim. | Onboarding aid. | None. |
| 16–19 | imports | Standard import machinery types. | Needed for hook. | None. |
| 21–56 | `_SHIM_MAP` | The mapping table. | The truth source for all redirected imports. | None. |
| 59–70 | `_ShimFinder` | Custom MetaPathFinder. | Hook into Python's import system. | None at definition. |
| 73–85 | `_ShimLoader` | Custom Loader. | Performs the redirect + attribute copy. | At exec time: imports the real module, mutates `sys.modules`, copies attrs. |
| 88–90 | hook install | Avoids double install. | Idempotent. | Mutates `sys.meta_path`. |

## 6. Runtime Behavior
Executed once on first `import scheduler_app`. Installs the finder. From then on, every `import scheduler_app.foo` where `foo` is in `_SHIM_MAP` resolves transparently to the new location.

## 7. Data Flow
None — pure metaclass-level redirection.

## 8. UI Flow
Not applicable.

## 9. Error Handling and Edge Cases
- If `_ShimMap[name]` does not exist, `find_spec` returns `None` so normal import continues.
- If the real module's import fails, the original `ImportError` propagates from `importlib.import_module`.
- `find_module` is a legacy API; harmless on 3.12+ where it's never called.

## 10. Integration Points
Used by every caller writing `from scheduler_app.models import …`, etc. New code should prefer the explicit sub-package path, but the shim guarantees backward compat.

## 11. Risks and Maintenance Notes
- Adding new modules: update `_SHIM_MAP` if you want old-style imports to work.
- Removing modules: leave the shim entry so legacy callers still get an `ImportError` from `importlib.import_module` (clear signal) rather than `KeyError` on the dict.
- Do not introduce a circular dependency by having a sub-package import via the shim path.

## 12. Mini Summary
Installs an import hook so `scheduler_app.X` resolves to `scheduler_app.{core,learning,ui}.X` when X is in the shim map. The mapping is in `_SHIM_MAP`. Touch this only when reorganising the package layout.
