# Player validation

Local validation on Windows, CPython 3.13.5, NumPy 2.4.6, Pydantic 2.10.3:

- GUI smoke loaded/rendered all three public units, executed movement and reset, and exited successfully.
- Native replay test passed all 21 published per-level witnesses through three continuous seven-level sessions. Each reached engine WIN with score 7, then native RESET restored level 1 and score 0.
- Tk keyboard-event test passed movement, R reset, unit switching, and complete restart; the renderer produced a 64×64 frame before scaling.

Tests use the player itself and the bundled original engine/game. The optional answer replay reads outputs/ls20/seed22/report.json; normal human play does not. These checks establish the player wiring for this sample, not generalization of the main machine or a new TR87 result. Historical search and generation evidence remain in the outputs report.
