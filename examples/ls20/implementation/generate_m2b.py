"""Frozen-profile M2b data generator. Structural consistency only; no solver."""
import hashlib, json
from pathlib import Path
ROOT=Path(__file__).resolve().parent; OUT=ROOT/'m2b'; BLUE='M0-v0.4'; RULE='M1'
ROLES=['rotation_introduction','rotation_practice','rotation_plus_color','shape_color_contrast','full_attribute_combination','two_goal_coordination','fog_partial_visibility']
MATRIX={1:['rotation'],2:['rotation','refill'],3:['rotation','color','pusher','refill'],4:['shape','color','pusher','refill'],5:['shape','color','rotation','pusher','path','refill'],6:['shape','color','rotation','pusher','path','refill'],7:['shape','color','rotation','pusher','path','refill','fog']}
RULE_ID={'rotation':'rotation_cycle','color':'color_cycle','shape':'shape_cycle','refill':'step_refill','path':'path_link','fog':'fog_visibility','pusher':'pusher'}
PROFILES=[('A','open_corridor',20261121,1,10),('B','bent_corridor',20261122,2,11),('C','split_route',20261123,3,12)]
L5_REPAIR_OVERRIDES={
    1:{'nodes':[[20,20],[25,20],[25,25],[20,25]],'direction':1,'color':[30,20],'rotation':[15,20],'refill':[40,20]},
    2:{'nodes':[[20,25],[15,25],[15,30],[20,30]],'direction':3,'color':[25,35],'rotation':[25,30],'refill':[25,25]},
    3:{'nodes':[[20,30],[15,30],[15,35],[20,35]],'direction':3,'color':[30,25],'rotation':[25,30],'refill':[25,25]},
}
ATTR={'shape':0,'color':1,'rotation':2}
def canon(x): return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def digest(x): return hashlib.sha256(canon(x)).hexdigest()
def pos(c): return [c[0]*5,c[1]*5]
def route_cells(topology,slot):
    # Each route is an explicit connected centerline from start to goal.
    if slot==6:
        y=4 if topology=='open_corridor' else 8 if topology=='bent_corridor' else 9
        # Shared stem splits to independent upper/lower goal approaches.
        return [(2,y),(3,y),(4,y),(5,y),(5,y-1),(6,y-1),(7,y-1),(8,y-1),(9,y-1),(10,y-1),(5,y+1),(6,y+1),(7,y+1),(8,y+1),(9,y+1),(10,y+1)]
    if topology=='open_corridor': return [(2+i,3+(slot%2)) for i in range(8)]
    if topology=='bent_corridor':
        y=4+(slot%2); return [(2+i,y) for i in range(4)]+[(5,y+j) for j in range(1,4)]
    y=5+(slot%2)
    # A forked route: shared stem, then upper/lower branches rejoin at goal.
    return [(2,y),(3,y),(4,y),(5,y),(5,y-1),(6,y-1),(7,y-1),(8,y-1),(8,y),(8,y+1),(7,y+1),(6,y+1),(5,y+1)]
def make(unit,letter,topology,seed,step_base,slot):
    cells=route_cells(topology,slot); start=cells[0]; goal=cells[-1]
    mech=MATRIX[slot]; supported=[m for m in ('shape','color','rotation') if m in mech]
    initial=[(unit+slot)%6,(unit+slot)%4,(unit+slot)%4]
    # Place each required trigger on a distinct traversed cell before the goal.
    trigger_cells=cells[1:1+len(supported)]
    triggers=[{'type':t,'position':pos(c)} for t,c in zip(supported,trigger_cells)]
    if slot==6:
        # Branch-specific rotation counts support distinct exact requirements in either direction.
        triggers=[{'type':'shape','position':pos(cells[1])},{'type':'color','position':pos(cells[2])}]
        for c in cells[4:8]: triggers.append({'type':'rotation','position':pos(c)})
        for c in cells[10:12]: triggers.append({'type':'rotation','position':pos(c)})
    deltas=[0,0,0]
    for t in supported: deltas[ATTR[t]]=1
    goalattrs=[(initial[i]+deltas[i])%([6,4,4][i]) for i in range(3)]
    goals=[{'position':pos(goal),'attributes':goalattrs}]
    if slot==6:
        gy=4 if topology=='open_corridor' else 8 if topology=='bent_corridor' else 9
        first=list(goalattrs); first[2]=initial[2]
        second=list(first); second[2]=(initial[2]+2)%4
        goals=[{'position':pos((10,gy-1)),'attributes':first},{'position':pos((10,gy+1)),'attributes':second}]
    refills=[{'position':pos(cells[min(2,len(cells)-2)])}] if 'refill' in mech else []
    pushers=[]; pusher_contacts=[]
    if 'pusher' in mech:
        # Sprite is offset opposite travel by 1px to match Tycho's leading-edge sample.
        gy=4 if topology=='open_corridor' else 8 if topology=='bent_corridor' else 9
        if unit==3 and slot==3: contact=(4,6); direction='right'
        elif unit==3 and slot==4: contact=(4,5); direction='right'
        elif slot==6: contact=(7,gy-1); direction='right'
        elif topology=='open_corridor': contact=(7,3+(slot%2)); direction='right'
        elif topology=='bent_corridor': contact=(5,4+(slot%2)+1); direction='down'
        else: contact=(6,5+(slot%2)-1); direction='right'
        assert contact in cells
        pusher_contacts=[pos(contact)]
        sprite=(contact[0]*5-1,contact[1]*5) if direction=='right' else (contact[0]*5,contact[1]*5-1)
        pushers=[{'position':list(sprite),'direction':direction,'home_position':list(sprite)}]
    paths=[]
    if 'path' in mech:
        # Four-node closed loop. Linked trigger index is a path trigger instance index.
        anchor=cells[1]
        nodes=[pos(anchor),pos((anchor[0]+1,anchor[1])),pos((anchor[0]+1,anchor[1]+1)),pos((anchor[0],anchor[1]+1))]
        paths=[{'id':'path0','nodes':nodes,'linked_trigger':0,'start_index':0,'start_direction':1}]
        if triggers: triggers[0]['path_id']='path0'
        active={tuple(n) for n in nodes}
        forbidden={tuple(pos(start)),*(tuple(g['position']) for g in goals),*(tuple(r['position']) for r in refills),*(tuple(x) for x in pusher_contacts),*(tuple(x['position']) for x in pushers)}
        occupied={tuple(t['position']) for t in triggers}
        candidates=[pos(c) for c in cells if tuple(pos(c)) not in forbidden and tuple(pos(c)) not in active and tuple(pos(c)) not in occupied]
        for trigger in triggers[1:]:
            if tuple(trigger['position']) in active:
                old=tuple(trigger['position']); occupied.remove(old)
                choice=next((c for c in candidates if tuple(c) not in occupied),None)
                assert choice is not None
                trigger['position']=choice; occupied.add(tuple(choice))
                trigger.pop('path_id',None)
                candidates.remove(choice)
        # Tycho binds every trigger whose sprite pixels intersect an active
        # path-mask pixel. Keep only the declared linked trigger on those
        # active node origins; move all other trigger instances to clear route
        # cells so engine initialization creates exactly one mover.
    if slot==5:
        # Deterministic reviewed repair for each unit's L5 path-intercept map.
        override=L5_REPAIR_OVERRIDES[unit]
        path=paths[0]
        path['nodes']=[list(n) for n in override['nodes']]
        path['start_index']=0
        path['start_direction']=override['direction']
        linked=path['linked_trigger']
        triggers[linked]['position']=path['nodes'][0]
        triggers[linked]['path_id']='path0'
        for trigger in triggers:
            if trigger['type']=='color': trigger['position']=list(override['color'])
            elif trigger['type']=='rotation': trigger['position']=list(override['rotation'])
        refills[0]['position']=list(override['refill'])
    elif slot in (6,7):
        # Start the linked shape mover one step before it crosses the player's
        # first route cell; strict-oracle and Tycho probes validate this phase.
        path=paths[0]
        path['start_index']=3
        path['start_direction']=0
        triggers[path['linked_trigger']]['position']=list(path['nodes'][3])
    # Public collision walls constrain play to the frozen corridor/branch graph.
    free_cells=set(cells)
    if paths: free_cells.update((n[0]//5,n[1]//5) for n in paths[0]['nodes'])
    if slot!=5:
        for pt in [x['position'] for x in goals+triggers+refills]: free_cells.add((pt[0]//5,pt[1]//5))
    walls=[pos((x,y)) for x in range(12) for y in range(12) if (x,y) not in free_cells]
    wall_instances=[{'position':w} for w in walls]
    ordered=[]
    for kind,items in (('goal',goals),('wall',wall_instances),('trigger',triggers),('refill',refills),('pusher',pushers)):
        ordered.extend((kind,item) for item in items)
    for order,(kind,item) in enumerate(ordered): item['sprite_order']=order
    steps=step_base+slot+(6 if slot==6 else 0)
    public={'level_id':f'L{slot}','start_position':pos(start),'initial_player_state':{'position':pos(start),'shape':initial[0],'color':initial[1],'rotation':initial[2]},'goal_count':len(goals),'goal_specs':goals,'step_budget':steps,'steps_decrement':1,'life_budget':3,'reset_policy':'tycho_same_level_unless_win','grid_step_pixels':5,'visibility_mode':'fog' if slot==7 else 'full','hint_mode':'match_hint' if slot==1 else 'off','board_layout':{'bounds_pixels':[0,0,64,64]},'walls':wall_instances,'trigger_instances':triggers,'pusher_instances':pushers,'refill_instances':refills,'path_instances':paths}
    dep=(['rotation_cycle'] if slot in (1,2) else ['color_cycle','rotation_cycle'] if slot==3 else ['shape_cycle','color_cycle'] if slot==4 else ['shape_cycle','color_cycle','rotation_cycle'] if slot>=5 else [])
    if slot==6: dep+=['all_goals_required']
    tags={'introduces':[], 'reinforces':[], 'contrasts':[], 'combines':[], 'transfers':[]}
    if slot==1: tags['introduces']=['rotation_cycle','target_match']
    elif slot==2: tags['reinforces']=['rotation_cycle']; tags['introduces']=['step_refill']
    elif slot==3: tags['introduces']=['color_cycle','pusher']; tags['combines']=['color_cycle','rotation_cycle']
    elif slot==4: tags['introduces']=['shape_cycle']; tags['reinforces']=['color_cycle','pusher']; tags['contrasts']=['rotation_cycle_absent']
    elif slot==5: tags['combines']=['shape_cycle','color_cycle','rotation_cycle']; tags['introduces']=['path_link']
    elif slot==6: tags['combines']=['shape_cycle','color_cycle','rotation_cycle']; tags['introduces']=['multi_goal']; tags['transfers']=['path_link','pusher']
    else: tags['reinforces']=['shape_cycle','color_cycle','rotation_cycle','pusher','path_link']; tags['introduces']=['fog_visibility']; tags['transfers']=['multi_goal_to_single_goal']
    coverage=['grid_movement','wall_collision','target_match','step_budget','life_and_exhaustion','level_reset','campaign_progression']+[RULE_ID[x] for x in mech]
    if slot==1: coverage.append('first_level_match_hint')
    pusher_sample=None
    if pushers:
        dx,dy={'right':(1,0),'left':(-1,0),'up':(0,-1),'down':(0,1)}[pushers[0]['direction']]; px,py=pushers[0]['position']
        pusher_sample={'contact_position':pusher_contacts[0],'sampled_positions':[ [px+dx+5*k*dx,py+dy+5*k*dy] for k in range(1,13)]}
        if unit==3 and slot in (3,4):
            pusher_sample['player_collision_origin']=[px-4,py]
    sup={'level_id':f'L{slot}','campaign_id':f'CP1-U{unit}','slot_index':slot,'course_role':ROLES[slot-1],'learning_tags':tags,'topology_family':topology,'route_cells':[pos(c) for c in cells],'pusher_contact_positions':pusher_contacts,'expected_pusher_sampling':pusher_sample,'mechanics_coverage_required':sorted(coverage),'mechanics_win_dependency':sorted(dep),'mechanics_optional':[],'task_contract':{'goal_count':len(goals),'step_budget':steps,'steps_decrement':1,'life_budget':3,'reset_policy':'tycho_same_level_unless_win','exhaustion_semantics':'below_zero consumes life; nonterminal exhaustion restarts level preserving remaining lives; third exhaustion GAME_OVER','explicit_reset_semantics':'non-WIN RESET restarts current level with 3 lives; WIN RESET returns campaign to L1','level_transition_semantics':'WIN advances to next ordered level; L7 WIN completes campaign'},'source_blueprint_slot':slot,'generation_attempt':1,'level_seed':seed+slot,'variation_dimensions':{'topology':topology,'budget':steps,'attribute_offset':unit},'expected_mechanic_coverage':sorted(coverage),'counterfactual_cases':[],'oracle_artifact_ref':None,'validation_report_ref':'M2b structural report; interactions pending M3/M4','canonical_level_hash':digest(public)}
    return public,sup
def generate():
    OUT.mkdir(exist_ok=True); result=[]
    for unit,(letter,topology,seed,offset,budget) in enumerate(PROFILES,1):
        pairs=[make(unit,letter,topology,seed,budget,s) for s in range(1,8)]; levels=[p[0] for p in pairs]; decl=[p[1] for p in pairs]
        public={'schema_version':'pseudo-ls20-public-campaign-v1','rule_contract_version':RULE,'course_blueprint_version':BLUE,'levels':levels}; ph=digest(public)
        manifest={'schema_version':'pseudo-ls20-supervisor-manifest-v1','rule_contract_version':RULE,'course_blueprint_version':BLUE,'campaign_id':f'CP1-U{unit}','unit_index':unit,'seed':seed,'generator_version':'m2b-generator-v5-pusher-correction','ruleset_id':'pseudo-LS20-Tycho-ls20-9607627b','source_provenance':{'source_game_id':'ls20-9607627b','source_sha256':'67cc85888eeef68f125cbb5f5fb158e9ca455d6bca46b171790f556fcabf934e','reference':'deliverables/CP0_FINAL_AUDIT.md'},'ordered_level_ids':[f'L{i}' for i in range(1,8)],'variation_profile_id':f'PSEUDO_LS20_PROFILE_{letter}','variation_parameters':{'topology_family':topology,'attribute_offset':offset,'step_budget_base':budget,'step_budget_by_slot':{f'L{i}':budget+i+(6 if i==6 else 0) for i in range(1,8)}},'public_campaign_hash':ph,'private_oracle_bundle_hash':None,'oracle_artifact_ref':None,'solvability_status':'pending_M3','interaction_audit_status':'pending_M3_M4','continuity_contract':{'level_advance':'fixed L1 through L7 order after WIN','campaign_terminal':'L7 WIN reaches terminal campaign state','reset_policy':'Tycho ONLY_RESET_LEVELS=true; non-WIN RESET restarts current level; WIN RESET returns to L1','life_initialization':'3 lives on each level initialization and explicit RESET','exhaustion':'below-zero step count loses one life; nonterminal exhaustion resets current level preserving remaining lives; third exhaustion GAME_OVER'},'artifact_visibility_map':{'public_campaign':{'consumer_roles':['environment_runtime','supervisor'],'contains':'raw playable state/configuration; never directly exposed to game_agent'},'observations_and_actions':{'consumer_roles':['game_agent'],'contains':'normal observations, legal actions, and permitted feedback only'},'supervisor_manifest':{'consumer_roles':['supervisor','reviewer'],'contains':'course labels, mechanic declarations, seeds, provenance and validation status'},'oracle_bundle':{'consumer_roles':['supervisor_only'],'status':'not_created; M3 pending'}},'canonical_level_hashes':[digest(l) for l in levels],'level_declarations':decl}
        (OUT/f'unit{unit}_public.json').write_bytes(canon(public)+b'\n'); (OUT/f'unit{unit}_supervisor.json').write_bytes(canon(manifest)+b'\n')
        result.append((unit,seed,ph,public,manifest))
    return result
def validate(rows):
    assert len(rows)==3; profiles=set(); hashes=[]; layouts=set()
    for unit,seed,h,p,m in rows:
        assert m['public_campaign_hash']==digest(p); assert len(p['levels'])==7 and len(m['level_declarations'])==7
        assert all(p['levels'][i-1]['step_budget']==m['variation_parameters']['step_budget_by_slot'][f'L{i}'] for i in range(1,8))
        profiles.add(m['variation_profile_id'])
        layouts.add(tuple(tuple(c) for c in m['level_declarations'][0]['route_cells']))
        for slot,(l,d) in enumerate(zip(p['levels'],m['level_declarations']),1):
            assert l['board_layout']['bounds_pixels']==[0,0,64,64]
            assert d['slot_index']==slot and d['course_role']==ROLES[slot-1]
            required=['grid_movement','wall_collision','target_match','step_budget','life_and_exhaustion','level_reset','campaign_progression']+[RULE_ID[x] for x in MATRIX[slot]]
            if slot==1: required+=['first_level_match_hint']
            assert set(required)<=set(d['mechanics_coverage_required'])
            assert {t['type'] for t in l['trigger_instances']}==set(MATRIX[slot])&{'shape','color','rotation'} | ({'shape'} if slot==6 else set())
            assert l['goal_count']==(2 if slot==6 else 1) and len(l['goal_specs'])==l['goal_count']
            init=l['initial_player_state']; supported=set(MATRIX[slot])&{'shape','color','rotation'}
            for g in l['goal_specs']:
                assert 0<=g['attributes'][0]<6 and 0<=g['attributes'][1]<4 and 0<=g['attributes'][2]<4
                for a,key,mod in ((0,'shape',6),(1,'color',4),(2,'rotation',4)):
                    if key not in supported: assert g['attributes'][a]==init[key]
            assert l['step_budget']>=len(d['route_cells'])-1
            assert (l['visibility_mode']=='fog')==(slot==7) and (l['hint_mode']=='match_hint')==(slot==1)
            assert (bool(l['refill_instances']))==(slot>=2); assert (bool(l['pusher_instances']))==(slot>=3); assert (bool(l['path_instances']))==(slot>=5)
            aligned=[l['start_position'],*[w['position'] for w in l['walls']],*[x['position'] for x in l['goal_specs']],*[x['position'] for x in l['trigger_instances']],*[x['position'] for x in l['refill_instances']],*[n for x in l['path_instances'] for n in x['nodes']]]
            allorigins=aligned+[x['position'] for x in l['pusher_instances']]
            assert all(len(q)==2 and all(isinstance(v,int) and 0<=v and v+5<=64 for v in q) for q in allorigins)
            assert all(v%5==0 for q in aligned for v in q)
            route={tuple(q) for q in d['route_cells']}
            wallset={tuple(w['position']) for w in l['walls']}
            assert not route & wallset
            pathcells={tuple(n) for path in l['path_instances'] for n in path['nodes']}
            free={(x*5,y*5) for x in range(12) for y in range(12)}-wallset
            assert free==route|pathcells
            # Connectivity check on route graph (not game-state search).
            seen={next(iter(route))}; changed=True
            while changed:
                changed=False
                for q in route-seen:
                    if any(abs(q[0]-r[0])+abs(q[1]-r[1])==5 for r in seen): seen.add(q); changed=True
            assert seen==route
            assert tuple(l['start_position']) in route and all(tuple(g['position']) in route for g in l['goal_specs'])
            if slot==6:
                assert l['goal_specs'][0]['position']!=l['goal_specs'][1]['position']
                assert l['goal_specs'][0]['position'][1]<l['start_position'][1]<l['goal_specs'][1]['position'][1]
                assert l['goal_specs'][0]['attributes']!=l['goal_specs'][1]['attributes']
                assert l['goal_specs'][0]['attributes'][:2]==l['goal_specs'][1]['attributes'][:2]
                assert (l['goal_specs'][1]['attributes'][2]-l['goal_specs'][0]['attributes'][2])%4==2
                gy=l['start_position'][1]//5
                upper=[t for t in l['trigger_instances'] if t['type']=='rotation' and t['position'][1]//5<gy]
                lower=[t for t in l['trigger_instances'] if t['type']=='rotation' and t['position'][1]//5>gy]
                assert len(upper)==4 and len(lower)==2
                assert all((2*n)%4==0 for n in (len(upper),len(lower))) and len(upper)%4!=len(lower)%4
                assert l['step_budget']>=18
            linked_indices={p['linked_trigger'] for p in l['path_instances']}
            assert all(tuple(t['position']) in route for i,t in enumerate(l['trigger_instances']) if i not in linked_indices)
            assert all(tuple(x['position']) in route for x in l['refill_instances'])
            trigger_positions=[tuple(t['position']) for t in l['trigger_instances']]
            assert len(trigger_positions)==len(set(trigger_positions))
            assert tuple(l['start_position']) not in trigger_positions
            if l['path_instances']:
                assert not set(trigger_positions)&{tuple(x['position']) for x in l['refill_instances']}
            for path in l['path_instances']:
                nodes=path['nodes']; assert len(nodes)==4 and all(sum(abs(nodes[i][j]-nodes[(i+1)%4][j]) for j in (0,1))==5 for i in range(4))
                assert path['id']=='path0' and not {tuple(n) for n in nodes}&{tuple(w['position']) for w in l['walls']}
                assert 0<=path['linked_trigger']<len(l['trigger_instances']) and l['trigger_instances'][path['linked_trigger']].get('path_id')=='path0'
                ix=path['start_index']; assert tuple(l['trigger_instances'][path['linked_trigger']]['position'])==tuple(nodes[ix])
                a,b=nodes[ix],nodes[(ix+1)%len(nodes)]
                heading={(0,-5):0,(5,0):1,(0,5):2,(-5,0):3}[(b[0]-a[0],b[1]-a[1])]
                assert path['start_direction']==heading
                linked=path['linked_trigger']
                active={tuple(n) for n in nodes}
                assert tuple(l['start_position']) not in active
                # Tycho's source uses collides_with(ignoreMode=True) between
                # the mask's active pixels and trigger sprites. Generated
                # origins are 5px aligned; a one-pixel active mask sample
                # collides only with the trigger sprite covering that sample.
                # The sole intersecting trigger origin must be the declared link.
                bound=[i for i,t in enumerate(l['trigger_instances']) if tuple(t['position']) in active]
                assert bound==[linked]
            refill_positions={tuple(x['position']) for x in l['refill_instances']}; pusher_positions={tuple(x['position']) for x in l['pusher_instances']}
            assert not refill_positions & pusher_positions
            colliders=[*l['goal_specs'],*l['walls'],*l['trigger_instances'],*l['refill_instances'],*l['pusher_instances']]
            orders=[x['sprite_order'] for x in colliders]; assert len(orders)==len(set(orders))
            for pu in l['pusher_instances']:
                px,py=pu['position']; dx,dy={'right':(1,0),'left':(-1,0),'up':(0,-1),'down':(0,1)}[pu['direction']]
                contact=tuple(d['pusher_contact_positions'][0]); assert contact in route
                if pu['direction']=='right': assert (px+1,py)==contact
                elif pu['direction']=='left': assert (px-1,py)==contact
                elif pu['direction']=='down': assert (px,py+1)==contact
                else: assert (px,py-1)==contact
                samples=[(px+dx+5*k*dx,py+dy+5*k*dy) for k in range(1,13)]
                assert d['expected_pusher_sampling']['sampled_positions']==[list(q) for q in samples]
                stops={tuple(g['position']) for g in l['goal_specs']}|wallset
                first=next((i for i,q in enumerate(samples,1) if q in stops),None)
                assert first is not None and first>=2 and samples[0] in route
            assert not any(k in l for k in ('course_role','mechanics_coverage_required','mechanics_win_dependency','seed','level_seed','source_blueprint_slot'))
            assert 'fog_visibility' not in d['mechanics_win_dependency'] if slot==7 else True
        hashes.append(h)
    assert profiles=={'PSEUDO_LS20_PROFILE_A','PSEUDO_LS20_PROFILE_B','PSEUDO_LS20_PROFILE_C'} and len(layouts)==3 and len(set(hashes))==3
    rerun=generate(); assert [x[2] for x in rerun]==[x[2] for x in rows]
    return hashes
def main():
    rows=generate(); validate(rows)
    print(json.dumps({'campaigns':[{'unit':r[0],'seed':r[1],'public_campaign_hash':r[2]} for r in rows],'structural':'PASS','deterministic_rerun':'PASS','M3':'NOT_RUN','M4':'NOT_RUN'},indent=2))
if __name__=='__main__': main()
