# File map template (reference only)

The file maps in this folder follow this structure (sections renamed for brevity in some maps when a section is `Not applicable`):

1. **File Role** — what the file does in the project.
2. **Why this file matters** — critical / supporting / optional / legacy / generated / test-only / unclear.
3. **Imports and Dependencies** — stdlib, third-party, internal, with any cycle notes.
4. **Main Symbols** — every notable class, function, constant; line ranges; purpose; in/out; side effects; deps; assumptions; usage.
5. **Block-by-block code map** — a table covering every meaningful range of lines.
6. **Runtime Behavior** — when loaded/executed; what happens at runtime.
7. **Data Flow** — what data enters/leaves.
8. **UI Flow** — user-facing flows or "Not applicable".
9. **Error Handling and Edge Cases**.
10. **Integration Points** — what calls into this file; what this file calls.
11. **Risks and Maintenance Notes**.
12. **Mini Summary for Future Claude Instances**.

For very large files (translations.py, app.py, dialogs.py, schedule_optimizer.py, etc.) the "block-by-block" table is at the level of **logical sections** rather than literal lines, because line-by-line tables for 5,000+ line files would obscure rather than clarify.
