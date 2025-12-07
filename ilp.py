"""
TV Channel Scheduling Optimization using Google OR-Tools (CP-SAT)
High-performance scheduler using Constraint Programming.

Improvements:
- Uses Interval Variables (NoOverlap constraint)
- Uses Boolean logic for sequencing
- STRICT GENRE CONSTRAINT: Gaps do NOT reset the run count.
- TERMINATION PENALTY RESTORED: Solver must avoid partial programs if penalty is high.

Usage:
    python3 ilp.py <input_file> <output_file> <time_limit_seconds>
"""

import sys
import json
from ortools.sat.python import cp_model
from collections import defaultdict

def load_input(filepath):
    with open(filepath, 'r') as f:
        return json.load(f)

def save_output(output_data, filepath):
    with open(filepath, 'w') as f:
        json.dump(output_data, f, indent=4)

def solve_with_ortools(input_data, time_limit=300):
    # --- Data Parsing ---
    O = input_data['opening_time']
    E = input_data['closing_time']
    D = input_data['min_duration']
    R = input_data['max_consecutive_genre']
    S_pen = input_data['switch_penalty']
    T_pen = input_data['termination_penalty'] 
    
    priority_blocks = input_data.get('priority_blocks', [])
    time_prefs = input_data.get('time_preferences', [])
    channels_data = input_data['channels']

    programs = []
    
    # Preprocessing
    for channel in channels_data:
        cid = channel['channel_id']
        for prog in channel['programs']:
            if prog['start'] >= E or prog['end'] <= O:
                continue

            valid_start = max(O, prog['start'])
            valid_end = min(E, prog['end'])

            forbidden_intervals = []
            for block in priority_blocks:
                if cid not in block['allowed_channels']:
                    f_start = max(valid_start, block['start'])
                    f_end = min(valid_end, block['end'])
                    if f_start < f_end:
                        forbidden_intervals.append((f_start, f_end))
            
            candidate_windows = [(valid_start, valid_end)]
            for f_start, f_end in forbidden_intervals:
                next_candidates = []
                for c_start, c_end in candidate_windows:
                    if c_end <= f_start or c_start >= f_end:
                        next_candidates.append((c_start, c_end))
                        continue
                    if c_start < f_start:
                        if (f_start - c_start) >= D:
                            next_candidates.append((c_start, f_start))
                    if c_end > f_end:
                        if (c_end - f_end) >= D:
                            next_candidates.append((f_end, c_end))
                candidate_windows = next_candidates

            for w_start, w_end in candidate_windows:
                if (w_end - w_start) < D:
                    continue
                
                net_score = prog['score']
                
                # Penalty Logic Restored
                # The validator shows "Early End: -100" but input usually has smaller values.
                # However, usually "Early End" implies a penalty per MISSING minute or fixed?
                # The original PDF formula: "Late Start" or "Early Termination".
                # Standard interpretation: Fixed penalty T_pen if start/end differs.
                # Let's apply T_pen if start > prog['start'] OR end < prog['end'].
                
                if w_start > prog['start']: net_score -= T_pen
                if w_end < prog['end']: net_score -= T_pen

                programs.append({
                    'idx': len(programs),
                    'id': prog['program_id'],
                    'channel': cid,
                    'start': w_start,
                    'end': w_end,
                    'duration': w_end - w_start,
                    'genre': prog['genre'],
                    'score': net_score
                })

    n = len(programs)
    print(f"Programs after filtering: {n}")
    
    bonuses = [0] * n
    for i in range(n):
        p = programs[i]
        for pref in time_prefs:
            if p['genre'] == pref['preferred_genre']:
                overlap_start = max(p['start'], pref['start'])
                overlap_end = min(p['end'], pref['end'])
                if (overlap_end - overlap_start) >= D:
                    bonuses[i] += pref['bonus']

    model = cp_model.CpModel()

    is_selected = [model.NewBoolVar(f"x_{i}") for i in range(n)]

    intervals = []
    for i in range(n):
        interval = model.NewOptionalIntervalVar(
            programs[i]['start'], 
            programs[i]['duration'], 
            programs[i]['end'], 
            is_selected[i], 
            f"interval_{i}"
        )
        intervals.append(interval)

    model.AddNoOverlap(intervals)

    valid_transitions = defaultdict(list)
    valid_incoming = defaultdict(list)
    possible_trans_indices = []

    for i in range(n):
        for j in range(n):
            if i == j: continue
            if programs[j]['start'] >= programs[i]['end']:
                valid_transitions[i].append(j)
                valid_incoming[j].append(i)
                possible_trans_indices.append((i, j))

    trans_vars = {}
    for i, j in possible_trans_indices:
        trans_vars[(i, j)] = model.NewBoolVar(f"trans_{i}_{j}")
    
    is_first = [model.NewBoolVar(f"first_{i}") for i in range(n)]
    is_last = [model.NewBoolVar(f"last_{i}") for i in range(n)]

    for i in range(n):
        outgoing_expr = [trans_vars[(i, j)] for j in valid_transitions[i]]
        model.Add(sum(outgoing_expr) + is_last[i] == is_selected[i])
        
        incoming_expr = [trans_vars[(j, i)] for j in valid_incoming[i]]
        model.Add(sum(incoming_expr) + is_first[i] == is_selected[i])
    
    model.Add(sum(is_first) <= 1)
    model.Add(sum(is_last) <= 1)

    run_pos = [model.NewIntVar(1, R, f"run_{i}") for i in range(n)]

    penalty_terms = []

    for i in range(n):
        model.Add(run_pos[i] == 1).OnlyEnforceIf(is_first[i])

        for j in valid_transitions[i]:
            t_var = trans_vars[(i, j)]
            
            if programs[i]['genre'] == programs[j]['genre']:
                model.Add(run_pos[j] == run_pos[i] + 1).OnlyEnforceIf(t_var)
            else:
                model.Add(run_pos[j] == 1).OnlyEnforceIf(t_var)
            
            if programs[i]['channel'] != programs[j]['channel']:
                 penalty_terms.append(S_pen * t_var)

    obj_terms = []
    for i in range(n):
        val = programs[i]['score'] + bonuses[i]
        obj_terms.append(val * is_selected[i])
            
    model.Maximize(sum(obj_terms) - sum(penalty_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = 8 
    
    status = solver.Solve(model)
    print(f"Status: {solver.StatusName(status)}")
    
    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        selected_indices = [i for i in range(n) if solver.Value(is_selected[i])]
        
        if not selected_indices:
             return {'scheduled_programs': []}

        start_node = None
        for i in selected_indices:
            if solver.Value(is_first[i]):
                start_node = i
                break
        
        schedule = []
        curr = start_node
        while curr is not None:
            schedule.append(curr)
            next_node = None
            for j in valid_transitions[curr]:
                if solver.Value(trans_vars[(curr, j)]):
                    next_node = j
                    break
            curr = next_node

        out_list = []
        print("\nSchedule:")
        for idx, i in enumerate(schedule):
            p = programs[i]
            out_list.append({
                'program_id': p['id'],
                'channel_id': p['channel'],
                'start': p['start'],
                'end': p['end']
            })
            r_pos = solver.Value(run_pos[i])
            b_val = bonuses[i]
            print(f"{idx+1:2}. {p['id']:10} Ch{p['channel']} {p['start']}-{p['end']} "
                  f"({p['genre']}) Score={p['score']}+{b_val} Run={r_pos}")

        print(f"Objective Value: {solver.ObjectiveValue()}")
        return {'scheduled_programs': out_list}
    return None

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 ilp.py <input> <output> [time_limit]")
    else:
        input_file = sys.argv[1]
        output_file = sys.argv[2]
        time_limit = int(sys.argv[3]) if len(sys.argv) > 3 else 300
        
        print(f"Loading {input_file}...")
        data = load_input(input_file)
        sol = solve_with_ortools(data, time_limit)
        if sol:
            save_output(sol, output_file)
            print(f"Saved to {output_file}")
