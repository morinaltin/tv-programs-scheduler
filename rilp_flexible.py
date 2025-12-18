"""
Relaxed TV Channel Scheduling Optimization using Google OR-Tools (CP-SAT) - FLEXIBLE VERSION
Allows the solver to choose start and end times dynamically within valid windows.
Constraints Removed (Relaxed):
- Channel Switch Penalties
- Max Consecutive Genre Constraints
- Termination Penalties
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

def solve_with_flexible_ortools_relaxed(input_data, time_limit=300):
    start_time = time.time()
    
    # --- Data Parsing ---
    O = input_data['opening_time']
    E = input_data['closing_time']
    # If parameters missing, default to safe values or 0
    D = input_data.get('min_duration', 1) 
    
    # We ignore R, S_pen, T_pen
    
    priority_blocks = input_data.get('priority_blocks', [])
    time_prefs = input_data.get('time_preferences', [])
    channels_data = input_data['channels']

    MAX_SPLITS = 2

    # Preprocessing: Identify Maximal Valid Windows
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
    print(f"Flexible Relaxed Model: {n} candidate segments generated (Splits={MAX_SPLITS}).")
    
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
        
        # Enforce size constraints
        model.Add(d_var == e_var - s_var)
        
        # If selected -> Duration >= D
        model.Add(d_var >= D).OnlyEnforceIf(is_selected[i])
        # If not selected -> Duration == 0
        model.Add(d_var == 0).OnlyEnforceIf(is_selected[i].Not())
        
        # Interval for NoOverlap
        interval = model.NewOptionalIntervalVar(
            s_var, d_var, e_var, is_selected[i], f"interval_{i}"
        )
        intervals.append(interval)

    # 1. No Overlap
    model.AddNoOverlap(intervals)
    
    # Relaxed Model ignores sequences, transitions, switch penalties, and genre constraints.
    # Therefore, we do NOT need transition variables or flow constraints.
    # We only care that selected intervals do not overlap (handled above).

    # --- OBJECTIVE ---
    obj_terms = []
    
    # 1. Base Score
    # Earned MAX once per program ID if ANY segment is selected
    for pid, info in prog_id_map.items():
        indices = info['segment_indices']
        if not indices: continue
        
        is_present = model.NewBoolVar(f"present_{pid}")
        model.AddMaxEquality(is_present, [is_selected[i] for i in indices])
        
        obj_terms.append(info['score'] * is_present)

    # 2. Time Preference Bonus
    # Dynamic logic same as ilp_flexible.py
    for i in range(n):
        seg = candidate_segments[i]
        
        for pref in time_prefs:
            if seg['genre'] == pref['preferred_genre']:
                # overlap = max(0, min(ends[i], pref_end) - max(starts[i], pref_start))
                
                p_end = model.NewIntVar(0, E, f"pend_{i}_{pref['start']}")
                model.AddMinEquality(p_end, [ends[i], pref['end']])
                
                p_start = model.NewIntVar(0, E, f"pstart_{i}_{pref['start']}")
                model.AddMaxEquality(p_start, [starts[i], pref['start']])
                
                # Check if overlap >= D
                bonus_applies = model.NewBoolVar(f"bonus_{i}_{pref['start']}")
                model.Add(p_end - p_start >= D).OnlyEnforceIf(bonus_applies)
                model.Add(p_end - p_start < D).OnlyEnforceIf(bonus_applies.Not())
                
                # Add to objective (BUT ONLY IF SELECTED)
                fin_bonus = model.NewBoolVar(f"fb_{i}_{pref['start']}")
                model.AddBoolAnd([bonus_applies, is_selected[i]]).OnlyEnforceIf(fin_bonus)
                model.AddBoolOr([bonus_applies.Not(), is_selected[i].Not()]).OnlyEnforceIf(fin_bonus.Not())
                
                obj_terms.append(pref['bonus'] * fin_bonus)

    # Solve
    model.Maximize(sum(obj_terms))

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
        # Since we have no sequence info, we just collect selected items and sort by start time.
        
        selected_indices = [i for i in range(n) if solver.Value(is_selected[i])]
        
        # Sort by start time
        selected_indices.sort(key=lambda i: solver.Value(starts[i]))
        
        out_list = []
        print("\nSchedule:")
        for idx_order, i in enumerate(selected_indices):
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
            base_score = prog_id_map[seg['id']]['score']
            print(f"{idx_order+1:2}. {seg['id']:15} {s_val}-{e_val} (Ch{seg['channel']}) Base={base_score}")

        return {'scheduled_programs': out_list}

    return None

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 rilp_flexible.py <input> <output> [time_limit]")
    else:
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 300
        sol = solve_with_flexible_ortools_relaxed(load_input(sys.argv[1]), limit)
        if sol:
            save_output(sol, sys.argv[2])
