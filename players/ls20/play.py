"""Local human player; imports no pipeline machine, provider or supervisor files."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'vendor' / 'arcengine'))
sys.path.insert(0, str(HERE))
os.environ['ONLY_RESET_LEVELS'] = 'true'
from arcengine import ActionInput, GameAction, GameState
import adapter

# Display palette only; all collisions, state and goals are native engine logic.
PALETTE = ('#ffffff','#cccccc','#999999','#666666','#333333','#000000',
           '#e53aa3','#ff4136','#ff851b','#0074d9','#33cccc','#2ecc40',
           '#ffdc00','#800000','#8f42cc','#b28654')
ACTIONS = {'up': GameAction.ACTION1, 'down': GameAction.ACTION2,
           'left': GameAction.ACTION3, 'right': GameAction.ACTION4}

class Session:
    def __init__(self, package: Path):
        self.package = package.resolve()
        self.data = json.loads(self.package.read_text(encoding='utf-8'))
        if self.data.get('schema_version') != 'pseudo-ls20-public-campaign-v1' or len(self.data.get('levels', [])) != 7:
            raise ValueError('Select a seven-level public LS20 package, not a supervisor file.')
        self.restart()

    def restart(self):
        self.game = adapter.campaign_for(self.data)
        self.moves = 0
        self.last = 'Start'

    def pixels(self):
        # Rendering must never consume an action or silently reset the game.
        return self.game.camera.render(self.game.current_level.get_sprites())

    def action(self, name):
        if name != 'reset' and self.game._state in (GameState.WIN, GameState.GAME_OVER):
            return
        managers = tuple(id(x) for x in self.game.wsoslqeku)
        slot = self.game.level_index
        frame = self.game.perform_action(ActionInput(id=GameAction.RESET if name == 'reset' else ACTIONS[name]), raw=True)
        if name == 'reset' or slot != self.game.level_index or managers != tuple(id(x) for x in self.game.wsoslqeku):
            adapter.inject_campaign_level_phase(self.game, self.data)
        self.moves += 1
        self.last = name
        return frame

class Player:
    def __init__(self, root, packages: Path, unit=1, scale=8):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.root, self.packages, self.scale = tk, root, packages, scale
        root.title('Pseudo-LS20 — local player')
        root.resizable(False, False)
        bar = ttk.Frame(root, padding=8); bar.pack(fill='x')
        ttk.Label(bar, text='Unit / 游戏单元').pack(side='left')
        self.choice = tk.StringVar(value=str(unit))
        selector = ttk.Combobox(bar, width=3, state='readonly', textvariable=self.choice, values=('1','2','3'))
        selector.pack(side='left', padx=8); selector.bind('<<ComboboxSelected>>', lambda _: self.load())
        ttk.Button(bar, text='R — 重置当前关', command=lambda: self.act('reset')).pack(side='left', padx=4)
        ttk.Button(bar, text='整套重新开始', command=self.restart).pack(side='left', padx=4)
        self.canvas = tk.Canvas(root, highlightthickness=0, bg='#000000'); self.canvas.pack()
        self.canvas.bind('<Button-1>', lambda _: self.canvas.focus_set())
        self.status = tk.StringVar(); ttk.Label(root, textvariable=self.status, padding=8).pack()
        ttk.Label(root, text='方向键 / WASD 移动 · R 重置 · 1/2/3 换单元 · Esc 退出\n无自动解题；过关后自动进入下一关。', padding=8).pack()
        for key, action in {'Up':'up','Down':'down','Left':'left','Right':'right','w':'up','s':'down','a':'left','d':'right','r':'reset'}.items():
            root.bind('<'+key+'>', lambda event, a=action: self.act(a))
            if len(key)==1:root.bind('<'+key.upper()+'>', lambda event, a=action: self.act(a))
        for i in range(1,4):root.bind(str(i), lambda event, value=i:self.select(value))
        root.bind('<Escape>', lambda _: root.destroy())
        self.load()

    def select(self, unit):
        self.choice.set(str(unit)); self.load()

    def load(self):
        self.session = Session(self.packages / ('unit'+self.choice.get()+'_public.json'))
        self.draw(); self.canvas.focus_set()

    def restart(self):
        self.session.restart(); self.draw(); self.canvas.focus_set()

    def act(self, action):
        try:
            self.session.action(action); self.draw()
        except Exception as exc:
            from tkinter import messagebox
            messagebox.showerror('运行错误 / runtime error', str(exc))
            raise
        return 'break'

    def draw(self):
        pixels = self.session.pixels(); height, width = pixels.shape
        self.image = self.tk.PhotoImage(width=width, height=height)
        self.image.put(' '.join('{'+' '.join(PALETTE[int(v)] if 0 <= int(v) < 16 else '#000000' for v in row)+'}' for row in pixels))
        self.scaled = self.image.zoom(self.scale, self.scale)
        self.canvas.configure(width=width*self.scale, height=height*self.scale)
        self.canvas.delete('all'); self.canvas.create_image(0,0,anchor='nw',image=self.scaled)
        game = self.session.game
        state = '整套通关 WIN' if game._state == GameState.WIN else '失败 GAME OVER，按 R 重试' if game._state == GameState.GAME_OVER else '进行中'
        self.status.set(f'单元 {self.choice.get()} · 关卡 {game.level_index+1}/7 · {state} · 已输入 {self.session.moves} 次')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packages', type=Path, default=HERE.parents[1]/'outputs/ls20/seed22/packages')
    parser.add_argument('--unit', type=int, choices=(1,2,3), default=1)
    parser.add_argument('--scale', type=int, choices=range(3,17), default=8)
    parser.add_argument('--smoke-test', action='store_true', help='Offline GUI reset/switch/render smoke, no solution replay')
    parser.add_argument('--screenshot', type=Path, help='Save a PNG during --smoke-test')
    args = parser.parse_args()
    for i in range(1,4):
        if not (args.packages/f'unit{i}_public.json').is_file():parser.error('Missing public package; download the whole repository or pass --packages DIRECTORY.')
    import tkinter as tk
    root = tk.Tk(); app = Player(root, args.packages, args.unit, args.scale)
    if args.smoke_test:
        root.update()
        for unit in (1,2,3):
            app.select(unit); root.update(); app.act('up'); app.act('reset'); root.update()
            assert app.session.game.level_index == 0
        app.select(args.unit); root.update()
        if args.screenshot:app.scaled.write(str(args.screenshot), format='png')
        root.destroy(); print('PASS: three units loaded, rendered, moved and reset. Not a completion claim.')
    else:root.mainloop()

if __name__ == '__main__':main()
