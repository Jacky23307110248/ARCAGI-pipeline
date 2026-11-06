# Player validation

Validated on Windows with CPython 3.13.5, NumPy 2.4.6 and Pydantic 2.10.3:

- GUI smoke: loaded and rendered all three units, executed movement and reset, and exited successfully.
- Native replay: executed all 21 published level witnesses through three continuous campaigns. Each reached engine WIN with score 7; RESET restored level 1 and score 0.
- Keyboard test: checked movement, R reset, unit switching and campaign restart through Tk events. The renderer produced a 64×64 frame before scaling.
- Clean-copy check: repeated the GUI and replay tests using a directory containing the player components and sample outputs, with Python isolated mode.

The interactive player loads public packages. Replay tests read the published solution witnesses from `outputs/ls20/seed22/report.json`. That report also contains the sample's search and generation records.
