"""Translate the public generated package into native LS20 levels.

The source game supplies sprite templates; public JSON supplies map placements.
"""
import importlib.util, json, sys, hashlib
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TYCHO = ROOT / "game" / "ls20.py"
if hashlib.sha256(TYCHO.read_bytes()).hexdigest() != "67cc85888eeef68f125cbb5f5fb158e9ca455d6bca46b171790f556fcabf934e":
    raise ValueError("LS20 source fingerprint mismatch")
SPEC = importlib.util.spec_from_file_location("m3_tycho_ls20", TYCHO)
M = importlib.util.module_from_spec(SPEC); sys.modules[SPEC.name] = M; SPEC.loader.exec_module(M)
BASE_LEVELS=list(M.levels)

DROP = {"ihdgageizm", "gbvqrjtaqo", "xfmluydglp", "npxgalaybz", "ttfwljgohq", "soyhouuebz", "rhsxkxzdjz", "rjlbuycveu", "kvynsvxbpi"}

def template(tag):
    for lev in BASE_LEVELS:
        xs = lev.get_sprites_by_tag(tag)
        if xs: return xs[0]
    raise KeyError(tag)

def build_level(spec):
    lev = M.levels[0].clone()
    lev._sprites = [s for s in lev._sprites if not s.tags or not (set(s.tags) & DROP)]
    # Remove decorative original gameplay targets and rebuild exact JSON collision map.
    for s in list(lev._sprites):
        if s.tags and set(s.tags) & {"vjotnebuqo", "hoswmpiqkw"}: lev.remove_sprite(s)
    player = lev.get_sprites_by_tag("sfqyzhzkij")[0]
    player.set_position(*spec["start_position"])
    spawns=[]
    for w in spec["walls"]: spawns.append((w["sprite_order"],"ihdgageizm",w["position"],None))
    for t in spec["trigger_instances"]:
        spawns.append((t["sprite_order"],{"shape":"ttfwljgohq","color":"soyhouuebz","rotation":"rhsxkxzdjz"}[t["type"]],t["position"],None))
    for x in spec["refill_instances"]: spawns.append((x["sprite_order"],"npxgalaybz",x["position"],None))
    for x in spec["pusher_instances"]:
        # pusher sprite template names encode direction in suffix.
        dirs={"up":"t","down":"b","left":"l","right":"r"}
        tag=next(q.name for lev0 in BASE_LEVELS for q in lev0.get_sprites_by_tag("gbvqrjtaqo") if q.name.endswith("_"+dirs[x["direction"]]))
        spawns.append((x["sprite_order"],tag,x["position"],None))
    for path in spec["path_instances"]:
        xs=[n[0] for n in path["nodes"]]; ys=[n[1] for n in path["nodes"]]
        width=max(xs)-min(xs)+1; height=max(ys)-min(ys)+1
        # Exact public topology: active pixels only at declared node origins.
        # Reusing the stock 11x11 ring would add undeclared midpoint nodes.
        mask=template("xfmluydglp").clone()
        pixels=np.full((height,width),-1,dtype=np.int64)
        for nx,ny in path["nodes"]: pixels[ny-min(ys),nx-min(xs)]=14
        mask.pixels=pixels
        mask.set_position(min(xs),min(ys)); lev.add_sprite(mask)
    for i,g in enumerate(spec["goal_specs"]):
        spawns.append((g["sprite_order"],"rjlbuycveu",g["position"],None))
        spawns.append((g["sprite_order"]+0.1,"kvynsvxbpi",[g["position"][0]+1,g["position"][1]+1],None))
    for _,tag,xy,_ in sorted(spawns,key=lambda z:z[0]): add(lev,tag,xy)
    # Source palette order is [12, 9, 14, 8] for normalized M1 colors 0..3.
    palette=[12,9,14,8]
    data={"StartShape":spec["initial_player_state"]["shape"], "StartColor":palette[spec["initial_player_state"]["color"]], "StartRotation":spec["initial_player_state"]["rotation"]*90, "kvynsvxbpi":[g["attributes"][0] for g in spec["goal_specs"]], "GoalColor":[palette[g["attributes"][1]] for g in spec["goal_specs"]], "GoalRotation":[g["attributes"][2]*90 for g in spec["goal_specs"]], "StepCounter":spec["step_budget"], "StepsDecrement":spec["steps_decrement"], "Fog":spec["visibility_mode"]=="fog"}
    lev._data.update(data)
    return lev

def add(lev,tag,xy):
    if tag.endswith(("_t","_b","_l","_r")) and tag.startswith(tuple(q.name[:q.name.rfind("_")+1] for base in BASE_LEVELS for q in base.get_sprites_by_tag("gbvqrjtaqo"))):
        s=next(q for base in BASE_LEVELS for q in base.get_sprites_by_tag("gbvqrjtaqo") if q.name==tag).clone()
    else:s=template(tag).clone()
    s.set_position(*xy); lev.add_sprite(s); return s

def game_for(spec, slot=0):
    # Keep the actual campaign index: L1 alone enables the hint early-return.
    levels=[BASE_LEVELS[0].clone() for _ in range(slot)]+[build_level(spec)]
    M.levels=levels
    game=M.Ls20()
    if slot: game.set_level(slot)
    inject_path_phase(game,spec); return game

def campaign_for(pkg):
    M.levels=[build_level(x) for x in pkg["levels"]]
    game=M.Ls20(); inject_campaign_level_phase(game,pkg,expected_slot=0); return game

def inject_campaign_level_phase(game,pkg,expected_slot=None):
    """After Tycho installs a campaign level, seed its declared path heading.

    ARCBaseGame's native deferred transition calls on_set_level and creates
    fresh movers with constructor direction 0. This fixture adapter applies
    the public start_direction immediately after that native transition and
    before the next player action; subsequent movement remains native Tycho.
    """
    slot=game.level_index
    if expected_slot is not None and slot!=expected_slot:
        raise AssertionError(("campaign level index mismatch",slot,expected_slot))
    if not 0<=slot<len(pkg["levels"]):
        raise AssertionError(("campaign level out of range",slot))
    inject_path_phase(game,pkg["levels"][slot])
    return slot

def inject_path_phase(game,spec):
    """Fixture setup only: align Tycho mover heading with public path phase.

    Public/M1 encoding is up,right,down,left = 0,1,2,3. Tycho dboxixicic
    encoding is down,right,up,left = 0,1,2,3, so vertical directions are flipped.
    Movement thereafter is entirely performed by the unmodified Tycho engine.
    """
    conv=(2,1,0,3)
    if len(game.wsoslqeku)!=len(spec["path_instances"]):
        raise AssertionError(("Tycho/public path binding count mismatch",len(game.wsoslqeku),len(spec["path_instances"])))
    for manager,p in zip(game.wsoslqeku,spec["path_instances"]):
        manager._dir=conv[p["start_direction"]]

def configure_state(game,spec,state):
    """Restore an oracle state into a fresh Tycho level for one-edge auditing."""
    game.gudziatsk.set_position(state.x,state.y)
    game.fwckfzsyc=state.shape; game.hiaauhahz=state.color; game.cklxociuu=state.rotation
    game._step_counter_ui.current_steps=state.steps; game.aqygnziho=state.lives
    game.lvrnuajbl=[bool(state.goals&(1<<i)) for i in range(len(spec["goal_specs"]))]
    # Match the avatar display to the restored attributes; game logic reads the
    # numeric fields above, while the sprite remains part of actual rendering.
    game.htkmubhry.pixels=game.ijessuuig[state.shape].pixels.copy()
    game.htkmubhry.color_remap(None,game.tnkekoeuk[state.color])
    game.htkmubhry.set_rotation(game.dhksvilbb[state.rotation])
    # Restore picked refill objects exactly as Tycho's death/reset bookkeeping expects.
    refs=sorted(spec["refill_instances"],key=lambda x:x["sprite_order"])
    game.ofoahudlo=[]
    for i,ref in enumerate(refs):
        if state.picked&(1<<i):
            sprite=next((x for x in game.current_level.get_sprites_by_tag("npxgalaybz") if (x.x,x.y)==tuple(ref["position"])),None)
            if sprite is not None:
                game.current_level.remove_sprite(sprite);game.ofoahudlo.append(sprite)
    # Path sprites and real Tycho mover heading follow the explicit M1 state.
    tycho_dir=(2,1,0,3)
    for manager,path,index,direction in zip(game.wsoslqeku,spec["path_instances"],state.path_indices,state.path_directions):
        xy=tuple(path["nodes"][index]);manager._sprite.set_position(*xy);manager._dir=tycho_dir[direction]

