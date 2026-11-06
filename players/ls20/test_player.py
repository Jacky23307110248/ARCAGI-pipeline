"""Player validation using the published solution witnesses."""
import json
from pathlib import Path
import unittest
import play

PACKAGES = play.HERE.parents[1]/'outputs/ls20/seed22/packages'

class PlayerTests(unittest.TestCase):
    def test_native_campaigns_accept_all_published_witnesses(self):
        report = json.loads((PACKAGES.parent/'report.json').read_text(encoding='utf-8'))
        for unit in (1,2,3):
            with self.subTest(unit=unit):
                session = play.Session(PACKAGES/f'unit{unit}_public.json')
                rows = sorted((r for r in report['level_results'] if r['unit']==unit), key=lambda r:r['slot'])
                self.assertEqual(len(rows),7)
                for slot,row in enumerate(rows):
                    self.assertEqual(session.game.level_index,slot)
                    for action in row['witness']:
                        session.action(action)
                    session.pixels()
                self.assertEqual(session.game._state,play.GameState.WIN)
                self.assertEqual(session.game._score,7)
                session.action('reset')
                self.assertEqual(session.game.level_index,0)
                self.assertEqual(session.game._score,0)
                self.assertEqual(session.game._state,play.GameState.NOT_FINISHED)

    def test_tk_keyboard_buttons_and_switch_reset(self):
        import tkinter as tk
        root = tk.Tk()
        try:
            app = play.Player(root,PACKAGES,scale=3)
            root.update();root.focus_force();root.update()
            root.event_generate('<KeyPress-Up>');root.update()
            self.assertEqual(app.session.moves,1)
            root.event_generate('<KeyPress-r>');root.update()
            self.assertEqual(app.session.moves,2)
            self.assertEqual(app.session.game.level_index,0)
            root.event_generate('<KeyPress-2>');root.update()
            self.assertEqual(app.choice.get(),'2')
            self.assertEqual(app.session.moves,0)
            app.act('right');app.restart();root.update()
            self.assertEqual(app.session.moves,0)
            self.assertEqual(app.session.game.level_index,0)
            self.assertEqual(app.image.width(),64)
        finally:root.destroy()

if __name__=='__main__':unittest.main()
