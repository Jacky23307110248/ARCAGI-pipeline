"""Independent finite-state BFS for generated LS20 public JSON (M3 only).

This file intentionally contains no import/reference to pseudo_ls20.runtime.
Each transition is computed from the public collider list and frozen M1 contract.
"""
from collections import deque
from dataclasses import dataclass

DIRS=((0,-5,"up"),(0,5,"down"),(-5,0,"left"),(5,0,"right"))
MOD={"shape":6,"color":4,"rotation":4}
PLAYER_SIZE=(5,5); PUSHER_SIZE=(5,5)

def aabb_intersects(a,b,asize=PLAYER_SIZE,bsize=PUSHER_SIZE):
    """Tycho Sprite.collides_with BOUNDING_BOX overlap; edge-touch is not collision."""
    ax,ay=a; bx,by=b; aw,ah=asize; bw,bh=bsize
    return ax < bx+bw and ax+aw > bx and ay < by+bh and ay+ah > by

@dataclass(frozen=True)
class S:
    x:int; y:int; shape:int; color:int; rotation:int; steps:int; lives:int
    goals:int; picked:int=0; path_indices:tuple=(); path_directions:tuple=()

def search(level, cap=1_000_000, slot=0, collect_graph=False):
    walls={tuple(w["position"]) for w in level["walls"]}
    # Sprite order is the transition order, even for colliders sharing a cell.
    coll=[]
    for t in level["trigger_instances"]: coll.append((t["sprite_order"],t["type"],tuple(t["position"])))
    for t in level["refill_instances"]: coll.append((t["sprite_order"],"refill",tuple(t["position"])))
    for i,g in enumerate(level["goal_specs"]): coll.append((g["sprite_order"],"goal",tuple(g["position"]),i,tuple(g["attributes"])))
    for w in level["walls"]: coll.append((w["sprite_order"],"wall",tuple(w["position"])))
    coll.sort(key=lambda z:z[0])
    paths=level["path_instances"]
    initial_path=tuple(p["start_index"] for p in paths)
    initial_dirs=tuple(p["start_direction"] for p in paths)
    start=level["initial_player_state"]; pos=level["start_position"]
    init=S(*pos,start["shape"],start["color"],start["rotation"],level["step_budget"],level["life_budget"],0,0,initial_path,initial_dirs)
    q=deque([(init,(),())]); seen={init}; n=0; edges=[]; parents={init:None}; first_win=None
    while q and n<cap:
        s,path,trace=q.popleft(); n+=1
        for dx,dy,action in DIRS:
            dest=(s.x+dx,s.y+dy)
            if not (0<=dest[0]<=55 and 0<=dest[1]<=55): continue
            sh,co,ro=s.shape,s.color,s.rotation; steps=s.steps; goals=s.goals; picked=s.picked
            goals_before_action=goals
            path_indices=list(s.path_indices)
            path_directions=list(s.path_directions)
            dynamic=list(coll)
            # Advance linked path overlays before checking the requested destination.
            for pi,p in enumerate(paths):
                nodes=[tuple(n) for n in p["nodes"]]; idx=path_indices[pi]
                if len(nodes)>1:
                    # Tycho dboxixicic tries heading, heading-1, heading+1,
                    # heading+2 in its native down/right/up/left encoding.
                    conv=(2,1,0,3); native=conv[path_directions[pi]]
                    vectors=((0,1),(1,0),(0,-1),(-1,0))
                    for nd in (native,(native-1)%4,(native+1)%4,(native+2)%4):
                        vx,vy=vectors[nd]; target=(nodes[idx][0]+5*vx,nodes[idx][1]+5*vy)
                        if target in nodes:
                            nxt=nodes.index(target);path_indices[pi]=nxt
                            path_directions[pi]=(2,1,0,3)[nd]
                            break
                    ti=p["linked_trigger"]
                    trig=level["trigger_instances"][ti]
                    linked_order=trig["sprite_order"]
                    dynamic=[(o,k,(nodes[nxt] if o==linked_order else xy),*extra) for item in dynamic for o,k,xy,*extra in [item]]
            dynamic.sort(key=lambda z:z[0])
            blocked=False; mismatch=False; refill=False; hint_return=False; matched_goals=[]
            for c in dynamic:
                if c[2]!=dest: continue
                if c[1]=="wall": blocked=True; break
                if c[1]=="goal":
                    i,attrs=c[3],c[4]
                    if not (goals>>i)&1:
                        if (sh,co,ro)!=attrs: blocked=True; mismatch=True
                        else: matched_goals.append(i)
                elif c[1] in MOD:
                    if c[1]=="shape": sh=(sh+1)%6
                    elif c[1]=="color": co=(co+1)%4
                    else: ro=(ro+1)%4
                    if slot==0 and any(not (goals>>gi)&1 and (sh,co,ro)==tuple(g["attributes"]) for gi,g in enumerate(level["goal_specs"])): hint_return=True
                elif c[1]=="refill":
                    bit=1 << level["refill_instances"].index(next(x for x in level["refill_instances"] if x["sprite_order"]==c[0]))
                    if not picked&bit: picked|=bit; steps=level["step_budget"]; refill=True
            x,y=(s.x,s.y) if blocked else dest
            if not blocked: 
                for gi in matched_goals: goals|=1<<gi
            if hint_return:
                if blocked: path_indices=list(s.path_indices);path_directions=list(s.path_directions)
                ns=S(x,y,sh,co,ro,steps,s.lives,goals,picked,tuple(path_indices),tuple(path_directions))
                if collect_graph: edges.append((s,action,ns,"playing"))
                np=path+(action,); nt=trace+(ns,)
                if ns not in seen: seen.add(ns); parents[ns]=(s,action); q.append((ns,np,nt))
                continue
            if not refill and not mismatch: steps-=level["steps_decrement"]
            # A contacted pusher carries the player to the nearest wall/goal stop.
            if steps>=0:
                # Tycho checks pusher AABB against the avatar's post-collision
                # position, which is the prior cell on a blocked movement.
                hit=next((p for p in sorted(level["pusher_instances"],key=lambda z:z["sprite_order"])
                          if aabb_intersects((x,y),tuple(p["position"]))),None)
                if hit:
                    dx,dy={"up":(0,-1),"down":(0,1),"left":(-1,0),"right":(1,0)}[hit["direction"]]
                    blockers=walls|{tuple(g["position"]) for g in level["goal_specs"]}
                    # Tycho probes the one-pixel leading edge, then 5px sprite widths.
                    sampled=next((k for k in range(1,12) if (hit["position"][0]+dx+5*k*dx,hit["position"][1]+dy+5*k*dy) in blockers),None)
                    count=max(0,sampled-1) if sampled is not None else 0
                    if count>0:
                        x=x+count*5*dx; y=y+count*5*dy
                        if (x,y)!=(dest[0],dest[1]): goals=goals_before_action
                        # Tycho performs a second ordered collider scan at carry endpoint.
                        for c in dynamic:
                            if c[2]!=(x,y): continue
                            if c[1]=="wall": break
                            if c[1]=="goal":
                                i,attrs=c[3],c[4]
                                if not (goals>>i)&1 and (sh,co,ro)==attrs: goals|=1<<i
                            elif c[1] in MOD:
                                if c[1]=="shape": sh=(sh+1)%6
                                elif c[1]=="color": co=(co+1)%4
                                else: ro=(ro+1)%4
                            elif c[1]=="refill":
                                bit=1 << level["refill_instances"].index(next(z for z in level["refill_instances"] if z["sprite_order"]==c[0]))
                                if not picked&bit: picked|=bit; steps=level["step_budget"]
            if blocked: path_indices=list(s.path_indices);path_directions=list(s.path_directions)
            ns=S(x,y,sh,co,ro,steps,s.lives,goals,picked,tuple(path_indices),tuple(path_directions))
            np=path+(action,)
            if goals==(1<<len(level["goal_specs"]))-1:
                if collect_graph:
                    edges.append((s,action,ns,"win"))
                    if first_win is None: first_win=(np,trace+(ns,))
                    continue
                return {"status":"solvable","actions":np,"trace":trace+(ns,),"expanded":n,"states":len(seen)}
            if steps<0:
                if s.lives<=1:
                    terminal_state=S(x,y,sh,co,ro,steps,0,goals,picked,tuple(path_indices),tuple(path_directions))
                    if collect_graph: edges.append((s,action,terminal_state,"game_over"))
                    continue
                # Tycho's dboxixicic.bkuguqrpvq() resets _dir=0 (down).
                # Public/M1 encoding maps that native reset direction to 2.
                reset_dirs=tuple(2 for _ in paths)
                st=level["initial_player_state"]; ns=S(*level["start_position"],st["shape"],st["color"],st["rotation"],level["step_budget"],s.lives-1,0,0,initial_path,reset_dirs)
            if collect_graph: edges.append((s,action,ns,"playing"))
            nt=trace+(ns,)
            if ns not in seen: seen.add(ns); parents[ns]=(s,action); q.append((ns,np,nt))
    status="solvable" if first_win is not None else ("unknown" if q else "unsolved")
    result={"status":status,"actions":first_win[0] if first_win else None,"trace":first_win[1] if first_win else None,"expanded":n,"states":len(seen),"cap":cap,"graph_complete":not q}
    if collect_graph: result["edges"]=edges; result["state_set"]=seen; result["parents"]=parents
    return result
