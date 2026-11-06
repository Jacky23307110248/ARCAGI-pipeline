# Pseudo-LS20 Player

A local desktop player for three pseudo-LS20 campaigns, each containing seven levels. The bundled LS20 game and ARCEngine execute movement, collisions, level transitions and victory conditions.

## Download and install

Download the repository with **Code → Download ZIP** and extract it, or clone it with Git. Keep `players/ls20/` and `outputs/ls20/seed22/` in their repository layout.

### Minimal download

For interactive play, download the entire [`players/ls20/`](../../players/ls20/) directory and these three files from [`outputs/ls20/seed22/packages/`](../../outputs/ls20/seed22/packages/):

- `unit1_public.json`
- `unit2_public.json`
- `unit3_public.json`

Arrange them as follows:

```text
your-folder/
├── players/
│   └── ls20/                 # Complete player directory, including game/ and vendor/
└── outputs/
    └── ls20/
        └── seed22/
            └── packages/
                ├── unit1_public.json
                ├── unit2_public.json
                └── unit3_public.json
```

Run the installation and launch commands below from `your-folder/`. This selection supplies the player and all three campaigns. Supervisor packages and the validation report are additional downloads for studying solutions and running the replay tests. The full repository ZIP provides a convenient way to obtain everything together.

### Python environment

Use **Python 3.13** with Tcl/Tk support and a desktop session. Validation used Python 3.13.5. The standard Windows Python installer includes a Tcl/Tk option. On Linux, install the Tk package matching your Python version. Run `python3.13 -m tkinter` to check the windowing setup.

From the **repository root**, run:

### Windows PowerShell

```powershell
py -3.13 -m venv .venv-ls20
.\.venv-ls20\Scripts\python.exe -m pip install -r players/ls20/requirements.txt
.\.venv-ls20\Scripts\python.exe players/ls20/play.py
```

### macOS and Linux

```sh
python3.13 -m venv .venv-ls20
.venv-ls20/bin/python -m pip install -r players/ls20/requirements.txt
.venv-ls20/bin/python players/ls20/play.py
```

Dependency installation uses an internet connection. Gameplay runs locally after installation. The dedicated virtual environment keeps the player's pinned dependencies together.

## Controls

| Control | Action |
|---|---|
| Arrow keys or W/A/S/D | Move up, left, down or right; each input executes one engine action |
| R / **Reset level** | Reset the current level while retaining completed-level progress; after campaign WIN, return to level 1 |
| **Restart campaign** | Start a fresh session for the selected unit at level 1 and clear the input counter |
| 1 / 2 / 3 or the unit selector | Start the selected unit at level 1 |
| Esc / close window | Exit; the next launch starts a fresh session |

Complete a level's objective to advance automatically. Completing all seven levels displays **Campaign complete — WIN**. After **GAME OVER**, press R to retry or select **Restart campaign**.

The game image displays the in-level state and resource indicators. **Inputs** counts the actions received by the player during the session. The engine determines each action's effects and resource costs.

Gameplay advances with your inputs. Tap keys individually for precise movement; holding a key can trigger keyboard repeat. Click the game image to restore keyboard focus.

The sample contains three fixed map profiles with their configured visibility settings. Explore the mechanics by playing the public packages, `unit1_public.json` through `unit3_public.json`. The accompanying supervisor files, validation report and replay tests provide solution witnesses for later study.

## Options

```powershell
.\.venv-ls20\Scripts\python.exe players/ls20/play.py --unit 2 --scale 10
```

- `--unit`: start unit 1, 2 or 3; default 1.
- `--scale`: pixel enlargement factor, 3–16; default 8.
- `--packages DIRECTORY`: load the three `unit*_public.json` files from another directory.

The default package directory is resolved relative to the player file.

## Troubleshooting

| Symptom | Action |
|---|---|
| Missing `numpy` or `pydantic` | Install requirements with the same virtual-environment Python used to launch the player |
| Missing `_tkinter` | Install Python with Tcl/Tk support or the matching system Tk package |
| Missing package files | Check `outputs/ls20/seed22/packages/` or supply `--packages DIRECTORY` |
| Game source fingerprint mismatch | Restore `game/ls20.py` from this player release |

## Checks

Check loading, rendering, movement and reset for all three units:

```powershell
.\.venv-ls20\Scripts\python.exe players/ls20/play.py --smoke-test
```

Add `--screenshot preview.png` to save the initial image. Run the solution-replay and keyboard tests with:

```powershell
.\.venv-ls20\Scripts\python.exe players/ls20/test_player.py
```

The replay test reads the solution witnesses in `outputs/ls20/seed22/report.json` and checks continuous seven-level campaigns, engine WIN, resets and Tk keyboard events. Validation results are recorded in [VALIDATION.md](VALIDATION.md).

## Implementation and licenses

Public JSON defines each generated map. The adapter initializes path headings from that configuration when levels are installed or reset; subsequent actions run through the bundled LS20 game and ARCEngine. The renderer displays engine pixels with a fixed 16-color palette and integer scaling.

File hashes and component provenance are recorded in [PROVENANCE.json](PROVENANCE.json). Player code uses the MIT [LICENSE](LICENSE). The LS20 source retains the ARC Prize Foundation MIT notice, and ARCEngine 0.9.3 includes [its MIT license](vendor/arcengine/LICENSE-MIT.txt). Installed dependencies retain their respective licenses.
