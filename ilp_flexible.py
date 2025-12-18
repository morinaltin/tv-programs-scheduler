"""
TV Channel Scheduling Optimization using Google OR-Tools (CP-SAT) - FLEXIBLE VERSION
Allows the solver to choose start and end times dynamically within valid windows.
"""

import sys
import json
import time
from ortools.sat.python import cp_model
from collections import defaultdict

def load_input(filepath):
    with open(filepath, 'r') as f:
        return json.load(f)

def save_output(output_data, filepath):
    with open(filepath, 'w') as f:
        json.dump(output_data, f, indent=4)

def solve_with_flexible_ortools(input_data, time_limit=300):
    start_time = time.time()
    
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

    # MAX_SPLITS = 2  # Experimental: Allow 2 segments per valid window?
    # For now, let's stick to 1 segment per maximal window to keep complexity manageable 
    # but allow it to be flexible. This covers the "trimming" case.
    # To cover A-B-A re-entry, we would need MAX_SPLITS > 1. 
    # Let's try MAX_SPLITS = 2 to see if we can beat the greedy re-entry.
    MAX_SPLITS = 2

    # Preprocessing: Identify Maximal Valid Windows
    # A "Window" is a contiguous block of time on a channel where a program is available
    # and NOT blocked by priority blocks.
    
    candidate_segments = []
    prog_id_map = {} # Store original info
    
    for channel in channels_data:
        cid = channel['channel_id']
        for prog in channel['programs']:
            if prog['start'] >= E or prog['end'] <= O:
                continue

            # Original bounds clipped to day
            valid_start = max(O, prog['start'])
            valid_end = min(E, prog['end'])

            # Subtract forbidden intervals
            forbidden_intervals = []
            for block in priority_blocks:
                if cid not in block['allowed_channels']:
                    f_start = max(valid_start, block['start'])
                    f_end = min(valid_end, block['end'])
                    if f_start < f_end:
                        forbidden_intervals.append((f_start, f_end))
            
            # Generate available windows
            windows = [(valid_start, valid_end)]
            for f_start, f_end in forbidden_intervals:
                next_windows = []
                for c_start, c_end in windows:
                    # Clip out the forbidden part
                    # Case 1: No overlap
                    if c_end <= f_start or c_start >= f_end:
                        next_windows.append((c_start, c_end))
                        continue
                    
                    # Case 2: Start remains
                    if c_start < f_start:
                        if (f_start - c_start) >= D:
                            next_windows.append((c_start, f_start))
                    
                    # Case 3: End remains
                    if c_end > f_end:
                        if (c_end - f_end) >= D:
                            next_windows.append((f_end, c_end))
                windows = next_windows

            # Ensure global map exists
            if prog['program_id'] not in prog_id_map:
                prog_id_map[prog['program_id']] = {
                    'score': prog['score'],
                    'genre': prog['genre'],
                    'orig_start': prog['start'],
                    'orig_end': prog['end'],
                    'segment_indices': []
                }

            # Create Segments
            for w_start, w_end in windows:
                if (w_end - w_start) < D:
                    continue
                
                # For each maximal window, we allow MAX_SPLITS segments
                for _ in range(MAX_SPLITS):
                    candidate_segments.append({
                        'idx': len(candidate_segments),
                        'id': prog['program_id'],
                        'channel': cid,
                        'genre': prog['genre'],
                        'window_start': w_start,
                        'window_end': w_end,
                        'orig_start': prog['start'],
                        'orig_end': prog['end']
                    })
                    prog_id_map[prog['program_id']]['segment_indices'].append(candidate_segments[-1]['idx'])

    n = len(candidate_segments)
    print(f"Flexible Model: {n} candidate segments generated (Splits={MAX_SPLITS}).")
    
    model = cp_model.CpModel()

    # --- VARIABLES ---
    
    # Main selection bool
    is_selected = [model.NewBoolVar(f"sel_{i}") for i in range(n)]
    
    # Time variables (Start, End, Duration)
    starts = []
    ends = []
    durations = []
    intervals = []
    
    for i in range(n):
        seg = candidate_segments[i]
        
        # Start/End are constrained by window bounds
        s_var = model.NewIntVar(seg['window_start'], seg['window_end'], f"start_{i}")
        e_var = model.NewIntVar(seg['window_start'], seg['window_end'], f"end_{i}")
        d_var = model.NewIntVar(0, seg['window_end'] - seg['window_start'], f"dur_{i}")
        
        starts.append(s_var)
        ends.append(e_var)
        durations.append(d_var)
        
        # Create Interval Variable
        # Note: If is_selected is false, duration is 0, start/end are irrelevant (but must stay in domain)
        # We enforce: if selected, size >= D.
        
        # Enforce size constraints
        model.Add(d_var == e_var - s_var)
        
        # If selected -> Duration >= D
        model.Add(d_var >= D).OnlyEnforceIf(is_selected[i])
        # If not selected -> Duration == 0
        model.Add(d_var == 0).OnlyEnforceIf(is_selected[i].Not())
        
        # Optional Interval for NoOverlap
        # We use strict enforcement manually or use NewOptionalIntervalVar?
        # NewOptionalIntervalVar needs `is_present` literal.
        # If `is_selected` is False, the interval is "absent" and doesn't participate in NoOverlap.
        
        # CAUTION: For NewOptionalIntervalVar, 'start' and 'end' vars act as "value if present".
        # If absent, they can be anything. 
        # But we need them to be consistent with d_var.
        
        interval = model.NewOptionalIntervalVar(
            s_var, d_var, e_var, is_selected[i], f"interval_{i}"
        )
        intervals.append(interval)

    # 1. No Overlap
    model.AddNoOverlap(intervals)
    
    # 2. Prevent Overlap of Splits for SAME program (Sanity check)
    # The solver *could* pick two splits of the same program window that overlap.
    # NoOverlap handles this globally anyway! Because all intervals are in the same NoOverlap constraint.
    
    # --- TRANSITIONS & SEQUENCE ---
    # Because times are variable, we cannot simply check "start[j] >= end[i]" statically.
    # We must model the sequence dynamically.
    # Approach:
    # We can model this as a "circuit" of visited nodes, but time order matters.
    #
    # standard ILP approach for variable time intervals:
    # "Next" variable or Boolean matrix for transitions.
    # Since N might be large (~300 with splits?), N^2 bools is 90,000. Acceptable for CP-SAT.
    
    # Let's filter N^2. Only allow i -> j if window_start[j] >= window_end[i] - (theoretical max overlap?).
    # Actually, simpler: only allow i->j if possible.
    # possible if window_end[i] <= window_end[j] ??
    # Strict order: end_i <= start_j.
    
    # Valid Transition Matrix
    possible_trans = []
    trans_vars = {}
    
    # To optimize: valid transitions are those where windows allow sequentiality
    # (i.e. window_start[j] + slack? No, just max(end_i) <= min(start_j)? No.)
    # Condition: overlap is NOT forced.
    # i.e. max(start_i, start_j) < min(end_i, end_j) is NOT forced to be huge.
    # Actually, if we use circuit, we just need 'can i come before j'.
    # i comes before j if window_start[i] + D <= window_end[j]. (Roughly)
    
    for i in range(n):
        for j in range(n):
            if i == j: continue
            
            # Pruning: Is it possible for i to precede j?
            # earliest end of i = window_start[i] + D
            # latest start of j = window_end[j] - D
            # if earliest_end_i > latest_start_j, impossible.
            if (candidate_segments[i]['window_start'] + D) > (candidate_segments[j]['window_end'] - D):
                continue
            
            # Also if i ends strictly after j starts based on windows
            if candidate_segments[i]['window_start'] >= candidate_segments[j]['window_end']:
                continue 
                
            possible_trans.append((i, j))

    print(f"Generating {len(possible_trans)} transition variables...")
    for i, j in possible_trans:
        trans_vars[(i, j)] = model.NewBoolVar(f"t_{i}_{j}")

    # Flow Constraints
    is_first = [model.NewBoolVar(f"first_{i}") for i in range(n)]
    is_last = [model.NewBoolVar(f"last_{i}") for i in range(n)]

    # Outgoing flow
    for i in range(n):
        outgoing = [trans_vars[(i, j)] for j in range(n) if (i, j) in trans_vars]
        model.Add(sum(outgoing) + is_last[i] == is_selected[i])
        
    # Incoming flow
    for j in range(n):
        incoming = [trans_vars[(i, j)] for i in range(n) if (i, j) in trans_vars]
        model.Add(sum(incoming) + is_first[j] == is_selected[j])
        
    model.Add(sum(is_first) <= 1)
    model.Add(sum(is_last) <= 1)
    
    # Time Precedence constraints for transitions
    # If t_i_j is true, then end_i <= start_j
    for i, j in trans_vars:
        model.Add(ends[i] <= starts[j]).OnlyEnforceIf(trans_vars[(i, j)])

    # --- GENRE CONSTRAINTS (Max Consecutive) ---
    run_pos = [model.NewIntVar(0, R, f"run_{i}") for i in range(n)]
    
    for i in range(n):
        # Base case: if first, run=1
        model.Add(run_pos[i] == 1).OnlyEnforceIf(is_first[i])
        model.Add(run_pos[i] == 0).OnlyEnforceIf(is_selected[i].Not()) # Cleanliness
        
        # Recursive step
        for j in range(n):
            if (i, j) in trans_vars:
                t_var = trans_vars[(i, j)]
                same_genre = (candidate_segments[i]['genre'] == candidate_segments[j]['genre'])
                
                if same_genre:
                    model.Add(run_pos[j] == run_pos[i] + 1).OnlyEnforceIf(t_var)
                else:
                    model.Add(run_pos[j] == 1).OnlyEnforceIf(t_var)

    # --- OBJECTIVE ---
    obj_terms = []
    penalty_terms = []
    
    # 1. Base Score
    # Earned MAX once per program ID if ANY segment is selected
    for pid, info in prog_id_map.items():
        indices = info['segment_indices']
        if not indices: continue
        
        is_present = model.NewBoolVar(f"present_{pid}")
        model.AddMaxEquality(is_present, [is_selected[i] for i in indices])
        
        obj_terms.append(info['score'] * is_present)
        
        # 2. Termination Penalty
        # Logic: If PROG present, check if we covered the Original Start and Original End.
        # But now we might have splits.
        # We need to find the "Earliest Start" and "Latest End" of the selected segments for this PID.
        
        # This is tricky with multiple segments.
        # Simplification:
        # A program incurs start_penalty if NO selected segment starts at `orig_start`.
        # A program incurs end_penalty if NO selected segment ends at `orig_end`.
        
        # Start Condition
        # valid_start_point = max(O, orig_start)
        # We check if ANY selected segment has start_i == valid_start_point
        
        valid_start = max(O, info['orig_start'])
        valid_end = min(E, info['orig_end'])
        
        program_start_ok = model.NewBoolVar(f"start_ok_{pid}")
        program_end_ok = model.NewBoolVar(f"end_ok_{pid}")
        
        # Create bools for each segment: seg_start_ok <=> (is_selected[i] AND starts[i] == valid_start)
        seg_start_oks = []
        seg_end_oks = []
        
        for idx in indices:
            # start == valid_start?
            # Reify: b_s <-> starts[idx] == valid_start
            s_match = model.NewBoolVar(f"s_match_{idx}")
            model.Add(starts[idx] == valid_start).OnlyEnforceIf(s_match)
            model.Add(starts[idx] != valid_start).OnlyEnforceIf(s_match.Not())
            
            # AND with is_selected
            s_final = model.NewBoolVar(f"s_fin_{idx}")
            model.AddBoolAnd([s_match, is_selected[idx]]).OnlyEnforceIf(s_final)
            model.AddBoolOr([s_match.Not(), is_selected[idx].Not()]).OnlyEnforceIf(s_final.Not())
            seg_start_oks.append(s_final)
            
            # end == valid_end?
            e_match = model.NewBoolVar(f"e_match_{idx}")
            model.Add(ends[idx] == valid_end).OnlyEnforceIf(e_match)
            model.Add(ends[idx] != valid_end).OnlyEnforceIf(e_match.Not())
            
            e_final = model.NewBoolVar(f"e_fin_{idx}")
            model.AddBoolAnd([e_match, is_selected[idx]]).OnlyEnforceIf(e_final)
            model.AddBoolOr([e_match.Not(), is_selected[idx].Not()]).OnlyEnforceIf(e_final.Not())
            seg_end_oks.append(e_final)
            
        # Does any segment satisfy start?
        model.AddBoolOr(seg_start_oks).OnlyEnforceIf(program_start_ok)
        model.AddBoolAnd([x.Not() for x in seg_start_oks]).OnlyEnforceIf(program_start_ok.Not())
        
        # Does any segment satisfy end?
        model.AddBoolOr(seg_end_oks).OnlyEnforceIf(program_end_ok)
        model.AddBoolAnd([x.Not() for x in seg_end_oks]).OnlyEnforceIf(program_end_ok.Not())
        
        # Penalty applied if present but NOT ok
        # T_pen * (is_present - program_start_ok) -> 1 if present and not ok, 0 otherwise
        penalty_terms.append(T_pen * (is_present - program_start_ok))
        penalty_terms.append(T_pen * (is_present - program_end_ok))

    # 3. Time Preference Bonus
    # Dynamic!
    # For each segment, calculate overlap with preferred window
    # Bonus = 1 if overlap_duration >= D
    
    for i in range(n):
        seg = candidate_segments[i]
        
        for pref in time_prefs:
            if seg['genre'] == pref['preferred_genre']:
                # Calculate overlap between [starts[i], ends[i]] and [pref_start, pref_end]
                # overlap = max(0, min(ends[i], pref_end) - max(starts[i], pref_start))
                # Min/Max with variables needs helper variables
                
                # Check min(ends[i], pref_end)
                # p_end = min(e, P)
                p_end = model.NewIntVar(0, E, f"pend_{i}_{pref['start']}")
                model.AddMinEquality(p_end, [ends[i], pref['end']])
                
                # Check max(starts[i], pref_start)
                # p_start = max(s, P)
                p_start = model.NewIntVar(0, E, f"pstart_{i}_{pref['start']}")
                model.AddMaxEquality(p_start, [starts[i], pref['start']])
                
                # Overlap duration
                overlap = model.NewIntVar(0, E - O, f"ov_{i}_{pref['start']}")
                # overlap = p_end - p_start (if >0)
                # But since we only care if overlap >= D, we can just check inequality
                # Condition: p_end - p_start >= D
                
                bonus_applies = model.NewBoolVar(f"bonus_{i}_{pref['start']}")
                model.Add(p_end - p_start >= D).OnlyEnforceIf(bonus_applies)
                model.Add(p_end - p_start < D).OnlyEnforceIf(bonus_applies.Not())
                
                # Add to objective (BUT ONLY IF SELECTED)
                # exact_bonus = pref['bonus'] * (bonus_applies AND is_selected[i])
                fin_bonus = model.NewBoolVar(f"fb_{i}_{pref['start']}")
                model.AddBoolAnd([bonus_applies, is_selected[i]]).OnlyEnforceIf(fin_bonus)
                model.AddBoolOr([bonus_applies.Not(), is_selected[i].Not()]).OnlyEnforceIf(fin_bonus.Not())
                
                obj_terms.append(pref['bonus'] * fin_bonus)

    # 4. Switch Penalty
    for i, j in trans_vars:
        if candidate_segments[i]['channel'] != candidate_segments[j]['channel']:
            penalty_terms.append(S_pen * trans_vars[(i, j)])

    # Solve
    model.Maximize(sum(obj_terms) - sum(penalty_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = 8 
    
    status = solver.Solve(model)
    end_time = time.time()
    elapsed = end_time - start_time
    print(f"Total Execution Time: {elapsed:.2f} seconds")
    
    print(f"Status: {solver.StatusName(status)}")
    print(f"Objective: {solver.ObjectiveValue()}")
    
    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        # Reconstruct Schedule
        schedule = []
        
        # Find start
        curr = None
        for i in range(n):
            if solver.Value(is_first[i]):
                curr = i
                break
        
        while curr is not None:
            schedule.append(curr)
            next_node = None
            for j in range(n):
                if (curr, j) in trans_vars and solver.Value(trans_vars[(curr, j)]):
                    next_node = j
                    break
            curr = next_node
            
        out_list = []
        print("\nSchedule:")
        for idx_order, i in enumerate(schedule):
            seg = candidate_segments[i]
            s_val = solver.Value(starts[i])
            e_val = solver.Value(ends[i])
            
            p_out = {
                'program_id': seg['id'],
                'channel_id': seg['channel'],
                'start': s_val,
                'end': e_val
            }
            out_list.append(p_out)
            print(f"{idx_order+1}. {seg['id']} {s_val}-{e_val} (Ch{seg['channel']})")

        return {'scheduled_programs': out_list}

    return None

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 ilp_flexible.py <input> <output> [time_limit]")
    else:
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 300
        sol = solve_with_flexible_ortools(load_input(sys.argv[1]), limit)
        if sol:
            save_output(sol, sys.argv[2])
