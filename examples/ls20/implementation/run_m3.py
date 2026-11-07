"""Prepared M3 runner. Do not invoke before explicit supervisor release.

Runs independent BFS for all 21 public levels, isolated Tycho witness replay,
campaign-session replay with native level transitions, and counterfactual
negative controls. It never imports pseudo_ls20.runtime and writes only under
this versioned M3 directory.
"""
import argparse, copy, hashlib, json, sys, time
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
sys.path.insert(0,str(HERE))
from . import oracle
from . import tycho_adapter as A
from arcengine import ActionInput,GameAction,GameState

DEFAULT_CAP=100_000
UNIT_PATHS=[ROOT/f"packages/unit{i}_public.json" for i in (1,2,3)]
ACTION_IDS={"up":GameAction.ACTION1,"down":GameAction.ACTION2,"left":GameAction.ACTION3,"right":GameAction.ACTION4}
TYCHO_TO_PUBLIC=(2,1,0,3)

def digest_bytes(data): return hashlib.sha256(data).hexdigest()
def canonical_hash(value): return digest_bytes(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode("utf-8"))
def file_hash(path): return digest_bytes(Path(path).read_bytes())

def actual_paths(game,spec):
    values=[]
    for mover,path in zip(game.wsoslqeku,spec["path_instances"]):
        idx=path["nodes"].index([mover._sprite.x,mover._sprite.y])
        values.append((idx,TYCHO_TO_PUBLIC[mover._dir]))
    return tuple(i for i,d in values),tuple(d for i,d in values)

def actual_discrete_state(game,spec):
    pi,pd=actual_paths(game,spec)
    refs=sorted(spec["refill_instances"],key=lambda q:q["sprite_order"])
    removed={(s.x,s.y) for s in game.ofoahudlo}
    picked=sum(1<<i for i,r in enumerate(refs) if tuple(r["position"]) in removed)
    goals=sum(1<<i for i,v in enumerate(game.lvrnuajbl) if v)
    pushers=tuple((m.sprite.x,m.sprite.y,m.is_pushing) for m in game.hasivfwip)
    return (game.gudziatsk.x,game.gudziatsk.y,game.fwckfzsyc,game.hiaauhahz,game.cklxociuu,
            game._step_counter_ui.current_steps,game.aqygnziho,goals,picked,pi,pd,pushers)

def expected_discrete_state(state,spec):
    homes=tuple((p["home_position"][0],p["home_position"][1],False) for p in spec["pusher_instances"])
    return (state.x,state.y,state.shape,state.color,state.rotation,state.steps,state.lives,
            state.goals,state.picked,state.path_indices,state.path_directions,homes)

def slot_initial_assertions(game,spec,slot,context):
    assert game.level_index==slot,(context,"slot",game.level_index,slot)
    assert (game.gudziatsk.x,game.gudziatsk.y)==tuple(spec["start_position"]),(context,"player start")
    st=spec["initial_player_state"]
    assert (game.fwckfzsyc,game.hiaauhahz,game.cklxociuu)==(st["shape"],st["color"],st["rotation"]),(context,"attributes")
    assert game._step_counter_ui.current_steps==spec["step_budget"],(context,"step budget")
    assert game.aqygnziho==spec["life_budget"],(context,"life budget")
    assert not any(game.lvrnuajbl),(context,"goal flags")
    assert len(game.wsoslqeku)==len(spec["path_instances"]),(context,"mover count")
    conv=(2,1,0,3)
    for mover,path in zip(game.wsoslqeku,spec["path_instances"]):
        assert (mover._sprite.x,mover._sprite.y)==tuple(path["nodes"][path["start_index"]]),(context,"path start position")
        assert mover._dir==conv[path["start_direction"]],(context,"path heading",mover._dir,path["start_direction"])

def isolate_replay(spec,slot,search_result):
    """Replay a positive witness in an actual slot-indexed Tycho level."""
    actions=search_result["actions"];trace=search_result["trace"]
    game=A.game_for(spec,slot=slot)
    slot_initial_assertions(game,spec,slot,"isolated witness start")
    rows=[]
    for i,(action,expected) in enumerate(zip(actions,trace),1):
        frame=game.perform_action(ActionInput(id=ACTION_IDS[action]),raw=True)
        actual=actual_discrete_state(game,spec)
        expected_state=expected_discrete_state(expected,spec)
        expected_status="win" if i==len(actions) else "playing"
        actual_status="win" if frame.state==GameState.WIN else "game_over" if frame.state==GameState.GAME_OVER else "playing"
        row={"i":i,"action":action,"actual":actual,"expected":expected_state,
             "actual_status":actual_status,"expected_status":expected_status,
             "pusher_animation":[{"elapsed":p._anim_elapsed,"duration":p._anim_duration,
                                  "carry":[p._carry_x,p._carry_y]} for p in game.hasivfwip]}
        rows.append(row)
        if actual!=expected_state or actual_status!=expected_status:
            return {"pass":False,"mismatch_step":i,"trace":rows}
    return {"pass":bool(rows and rows[-1]["actual_status"]=="win"),"trace":rows,
            "terminal":"WIN" if rows and rows[-1]["actual_status"]=="win" else rows[-1]["actual_status"]}

def sequence_campaign(pkg,witnesses,expected_traces):
    """Replay all seven witnesses through one native Tycho campaign session."""
    game=A.campaign_for(pkg);game_id=game._game_id;events=[];phase_events=[]
    slot_initial_assertions(game,pkg["levels"][0],0,"campaign start")
    for slot,(spec,actions,trace) in enumerate(zip(pkg["levels"],witnesses,expected_traces)):
        assert game.level_index==slot,("campaign order before play",slot,game.level_index)
        if slot:
            slot_initial_assertions(game,spec,slot,f"campaign L{slot+1} after injected path phase")
        assert len(actions)==len(trace),("campaign witness/trace length",slot,len(actions),len(trace))
        for ai,(action,expected_state) in enumerate(zip(actions,trace),1):
            before=game.level_index
            frame=game.perform_action(ActionInput(id=ACTION_IDS[action]),raw=True)
            after=game.level_index
            state_match=None
            # Tycho has already installed the next level by the time the
            # terminal action returns. Its new initial state is checked at the
            # transition boundary below; compare in-level states only while
            # the same slot is still installed (including final L7 WIN).
            if after==before:
                actual=actual_discrete_state(game,spec)
                expected=expected_discrete_state(expected_state,spec)
                state_match=(actual==expected)
                if not state_match:
                    raise AssertionError(("campaign action state mismatch",slot,ai,action,actual,expected))
            item={"slot_before":before,"action_index_in_level":ai,"action":action,
                  "level_index_after_engine_action":after,"score":game._score,
                  "state":str(frame.state),"session_id":game._game_id,
                  "oracle_state_match":state_match}
            if game._game_id!=game_id: raise AssertionError("Tycho game session id changed")
            if slot<6 and after==slot+1:
                if ai!=len(actions): raise AssertionError(("premature level transition",slot,ai,len(actions)))
                if game._score!=slot+1: raise AssertionError(("campaign progress mismatch",slot,game._score))
                installed=A.inject_campaign_level_phase(game,pkg,expected_slot=slot+1)
                slot_initial_assertions(game,pkg["levels"][installed],installed,"post-transition phase injection")
                phase_events.append({"from_slot":slot,"to_slot":installed,
                                     "declared_start_directions":[p["start_direction"] for p in pkg["levels"][installed]["path_instances"]],
                                     "actual_native_directions":[m._dir for m in game.wsoslqeku],
                                     "actual_path_indices":list(actual_paths(game,pkg["levels"][installed])[0])})
                item["transition_verified"]=True;events.append(item);break
            if slot==6 and ai==len(actions):
                if frame.state!=GameState.WIN or game.level_index!=6 or game._score!=7:
                    raise AssertionError(("campaign terminal mismatch",frame.state,game.level_index,game._score))
                item["campaign_terminal"]=True;events.append(item);break
            else:
                if frame.state in (GameState.WIN,GameState.GAME_OVER):
                    raise AssertionError(("unexpected terminal before expected campaign end",slot,ai,frame.state))
                item["transition_verified"]=False;events.append(item)
        else:
            raise AssertionError(("level witness did not trigger transition/terminal",slot))
    if game._game_id!=game_id or game.level_index!=6 or game._score!=7:
        raise AssertionError(("final campaign identity/progress",game._game_id,game.level_index,game._score))
    # The single final RESET must restore the same campaign session to L1.
    reset_frame=game.perform_action(ActionInput(id=GameAction.RESET),raw=True)
    if reset_frame.state!=GameState.NOT_FINISHED or not reset_frame.full_reset:
        raise AssertionError(("full campaign RESET frame mismatch",reset_frame.state,reset_frame.full_reset))
    if game._game_id!=game_id or game.level_index!=0 or game._score!=0:
        raise AssertionError(("full campaign reset mismatch",game._game_id,game.level_index,game._score))
    A.inject_campaign_level_phase(game,pkg,expected_slot=0)
    slot_initial_assertions(game,pkg["levels"][0],0,"post-campaign reset")
    return {"pass":True,"same_session":True,"events":events,"path_phase_events":phase_events,
            "terminal_level_index":6,"terminal_score":7,"terminal_state":"GameState.WIN",
            "reset":{"state":str(reset_frame.state),"level_index":game.level_index,"score":game._score,
                     "post_reset_paths":[[m._sprite.x,m._sprite.y,m._dir] for m in game.wsoslqeku],
                     "same_session":game._game_id==game_id}}

def _reindex_paths_after_trigger_removal(level,removed_indices):
    keep=[i for i in range(len(level["trigger_instances"])) if i not in removed_indices]
    remap={old:new for new,old in enumerate(keep)}
    level["trigger_instances"]=[level["trigger_instances"][i] for i in keep]
    new_paths=[]
    for path in level["path_instances"]:
        old=path["linked_trigger"]
        if old in removed_indices: continue
        path["linked_trigger"]=remap[old];new_paths.append(path)
    level["path_instances"]=new_paths

def negative_controls(packages,cap,unit_ids):
    """Check rotation-removal and L5 path-linked-shape/goal counterfactuals."""
    controls=[]
    for unit,pkg in zip(unit_ids,packages):
        # L1: remove all rotation triggers and any path mover that was bound to one.
        bad=copy.deepcopy(pkg["levels"][0])
        removed={i for i,t in enumerate(bad["trigger_instances"]) if t["type"]=="rotation"}
        if removed:
            _reindex_paths_after_trigger_removal(bad,removed)
            r=oracle.search(bad,cap=cap,slot=0)
            controls.append({"unit":unit,"control":"L1_remove_rotation_trigger","status":r["status"],
                             "states":r["states"],"expanded":r["expanded"],"cap":cap,
                             "did_not_false_positive":r["status"]!="solvable"})
            if r["status"]=="solvable": raise AssertionError((unit,"L1 rotation-removal false positive",r["actions"]))
        else:
            controls.append({"unit":unit,"control":"L1_remove_rotation_trigger","status":"invalid_control_no_rotation_trigger","did_not_false_positive":None})
        # L5: move the linked shape/path unit off-board, preserving its link, then
        # independently poison the target shape outside the schema domain.
        bad=copy.deepcopy(pkg["levels"][4]);moved=False
        for path in bad["path_instances"]:
            ti=path["linked_trigger"]
            if bad["trigger_instances"][ti]["type"]!="shape": continue
            origin=path["nodes"][path["start_index"]]
            delta=(1000-origin[0],1000-origin[1])
            path["nodes"]=[[x+delta[0],y+delta[1]] for x,y in path["nodes"]]
            bad["trigger_instances"][ti]["position"]=[1000,1000]
            moved=True
        if moved:
            r=oracle.search(bad,cap=cap,slot=4)
            controls.append({"unit":unit,"control":"L5_linked_shape_path_offboard","status":r["status"],
                             "states":r["states"],"expanded":r["expanded"],"cap":cap,
                             "did_not_false_positive":r["status"]!="solvable"})
            if r["status"]=="solvable": raise AssertionError((unit,"L5 linked shape/path removal false positive",r["actions"]))
        else:
            controls.append({"unit":unit,"control":"L5_linked_shape_path_offboard","status":"invalid_control_no_linked_shape_trigger","did_not_false_positive":None})
        bad_goal=copy.deepcopy(pkg["levels"][4])
        if bad_goal["goal_specs"]:
            bad_goal["goal_specs"][0]["attributes"][0]=99
            r=oracle.search(bad_goal,cap=cap,slot=4)
            controls.append({"unit":unit,"control":"L5_goal_shape_outside_domain","status":r["status"],
                             "states":r["states"],"expanded":r["expanded"],"cap":cap,
                             "did_not_false_positive":r["status"]!="solvable","invalid_level_control":True})
            if r["status"]=="solvable": raise AssertionError((unit,"invalid goal attribute false positive",r["actions"]))
    return controls

def run(cap=DEFAULT_CAP,unit_paths=UNIT_PATHS):
    packages=[];input_meta=[];unit_ids=[]
    for path in unit_paths:
        unit_ids.append(int(Path(path).stem.removeprefix("unit").removesuffix("_public")))
        pkg=json.loads(path.read_text(encoding="utf-8"));packages.append(pkg)
        input_meta.append({"path":Path(path).name,"sha256":file_hash(path),"campaign_canonical_sha256":canonical_hash(pkg)})
    all_levels=[];campaign_runs=[]
    for package_index,(unit,pkg) in enumerate(zip(unit_ids,packages)):
        witnesses=[];witness_traces=[];unit_records=[]
        for slot,level in enumerate(pkg["levels"]):
            started=time.perf_counter();result=oracle.search(level,cap=cap,slot=slot);elapsed=time.perf_counter()-started
            record={"unit":unit,"slot":slot,"level_id":level.get("level_id",f"L{slot+1}"),
                    "level_canonical_sha256":canonical_hash(level),"status":result["status"],
                    "states":result["states"],"expanded":result["expanded"],"cap":cap,
                    "search_exhausted":result["status"]=="unsolved","elapsed_seconds":round(elapsed,6)}
            if result["status"]=="solvable":
                record["witness"]=list(result["actions"])
                replay=isolate_replay(level,slot,result);record["tycho_replay"]=replay
                if not replay["pass"]: raise AssertionError((unit,slot,"isolated witness replay mismatch",replay.get("mismatch_step")))
                witnesses.append(list(result["actions"]))
                witness_traces.append(result["trace"])
            else:
                witnesses.append(None)
                witness_traces.append(None)
            unit_records.append(record);all_levels.append(record)
        if all(x is not None for x in witnesses):
            campaign_runs.append({"unit":unit,"campaign_hash":input_meta[package_index]["campaign_canonical_sha256"],
                                  **sequence_campaign(pkg,witnesses,witness_traces)})
        else:
            campaign_runs.append({"unit":unit,"pass":False,"blocked_by_levels":[i for i,x in enumerate(witnesses) if x is None],
                                  "reason":"At least one level is unknown or exhaustively unsolved; no full-campaign witness concatenation.",
                                  "level_records":unit_records})
    controls=negative_controls(packages,cap,unit_ids)
    all_witnessed=all(r["status"]=="solvable" and r.get("tycho_replay",{}).get("pass") for r in all_levels)
    all_campaigns=all(r.get("pass") is True for r in campaign_runs)
    controls_safe=all(r.get("did_not_false_positive") is True for r in controls)
    selected_all_three=(len(unit_ids)==3 and set(unit_ids)=={1,2,3})
    checks_pass=all_witnessed and all_campaigns and controls_safe
    result=("PASS_ALL_THREE" if selected_all_three else "PASS_SUBSET") if checks_pass else "INCOMPLETE"
    return {"scope":f"{len(packages)} selected canonical seven-level package(s)",
            "selected_unit_ids":unit_ids,"complete_three_unit_scope":selected_all_three,
            "cap_per_search":cap,"input_packages":input_meta,"level_results":all_levels,
            "campaign_results":campaign_runs,"negative_controls":controls,
            "result":result}

