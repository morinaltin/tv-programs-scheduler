"""
TV Channel Scheduling Optimization using Google OR-Tools (CP-SAT)
High-performance scheduler using Constraint Programming with Flexible Trimming.

Improvements:
- Uses Flexible Intervals: Allows trimming start/end of programs to fit.
- STRICT GENRE CONSTRAINT: Respects max consecutive genre.
- SWITCH PENALTY: Respects channel switching penalties.
- MEMORY OPTIMIZATION: Prunes transition graph to nearest 100 neighbors (K-Nearest).
- DYNAMIC BONUSES: Correctly handles preference bonuses without double counting.

Usage:
    python3 ilp.py <input_file> <output_file> <time_limit_seconds>
    ./.venv/bin/python3.11 ilp.py inputs/toy_tv_input.json outputs/ilp_toy.json 7200
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


def solve_with_ortools(input_data, time_limit=300):
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

    # MAX_SPLITS determines if we allow a program to be split into multiple non-contiguous segments
    # or just trimmed.
    # MAX_SPLITS = 1: Trimming only (Start/End can move, but one continuous block).
    # MAX_SPLITS = 2: Allows A-B-A re-entry (Start/End can be covered with a gap in between).
    MAX_SPLITS = 2

    # Preprocessing: Identify Maximal Valid Windows
    candidate_segments = []
    prog_id_map = {}  # Store original info

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
                    if c_end <= f_start or c_start >= f_end:
                        next_windows.append((c_start, c_end))
                        continue
                    if c_start < f_start:
                        if (f_start - c_start) >= D:
                            next_windows.append((c_start, f_start))
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
                    # Note: indices will be updated after sorting

    n = len(candidate_segments)
    print(f"Flexible Model: {n} candidate segments generated (Splits={MAX_SPLITS}).")

    # Sort segments by start time to enable efficient neighbor pruning
    candidate_segments.sort(key=lambda x: x['window_start'])

    # Re-map indices after sort
    for pid in prog_id_map:
        prog_id_map[pid]['segment_indices'] = []

    for idx, seg in enumerate(candidate_segments):
        prog_id_map[seg['id']]['segment_indices'].append(idx)

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
        # Enforce size constraints
        model.Add(d_var == e_var - s_var)

        # If selected -> Duration >= D
        model.Add(d_var >= D).OnlyEnforceIf(is_selected[i])
        # If not selected -> Duration == 0
        model.Add(d_var == 0).OnlyEnforceIf(is_selected[i].Not())

        interval = model.NewOptionalIntervalVar(
            s_var, d_var, e_var, is_selected[i], f"interval_{i}"
        )
        intervals.append(interval)

    # 1. No Overlap
    model.AddNoOverlap(intervals)

    # --- TRANSITIONS & SEQUENCE ---
    # Memory Optimized Transition Generation with Safety Nets
    possible_trans = []
    trans_vars = {}

    K_NEIGHBORS = 100
    print(f"Generating transitions (K_NEIGHBORS={K_NEIGHBORS} + channel safety)...")

    # Pre-group segments by channel for safety net lookup
    segments_by_channel = defaultdict(list)
    for idx, seg in enumerate(candidate_segments):
        segments_by_channel[seg['channel']].append(idx)

    for i in range(n):
        seg_i = candidate_segments[i]

        # 1. Add K-Nearest Neighbors (proximity-based)
        count = 0
        for j in range(i + 1, n):
            seg_j = candidate_segments[j]

            # Validation: i must be able to precede j
            if (seg_i['window_start'] + D) > (seg_j['window_end'] - D):
                continue
            if seg_i['window_start'] >= seg_j['window_end']:
                continue

            if (i, j) not in trans_vars:
                possible_trans.append((i, j))
                trans_vars[(i, j)] = model.NewBoolVar(f"t_{i}_{j}")

            count += 1
            if count >= K_NEIGHBORS:
                break

        # 2. Safety Net: Ensure we can transition to the NEXT reachable program
        # on EVERY channel. This prevents missing a high-score program far
        # down the timeline if the gap is filled with many low-score segments.
        for cid, indices in segments_by_channel.items():
            for j in indices:
                if j <= i: continue

                seg_j = candidate_segments[j]
                # Precedence validation
                if (seg_i['window_start'] + D) > (seg_j['window_end'] - D):
                    continue
                if seg_i['window_start'] >= seg_j['window_end']:
                    continue

                if (i, j) not in trans_vars:
                    possible_trans.append((i, j))
                    trans_vars[(i, j)] = model.NewBoolVar(f"t_{i}_{j}")

                # Just the first reachable one per channel is enough to bridge the gap
                break

    print(f"Generated {len(possible_trans)} transition variables.")

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
        model.Add(run_pos[i] == 0).OnlyEnforceIf(is_selected[i].Not())  # Cleanliness

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
        # Start Condition
        valid_start = max(O, info['orig_start'])
        valid_end = min(E, info['orig_end'])

        program_start_ok = model.NewBoolVar(f"start_ok_{pid}")
        program_end_ok = model.NewBoolVar(f"end_ok_{pid}")

        # Create bools for each segment
        seg_start_oks = []
        seg_end_oks = []

        for idx in indices:
            # start == valid_start?
            s_match = model.NewBoolVar(f"s_match_{idx}")
            model.Add(starts[idx] == valid_start).OnlyEnforceIf(s_match)
            model.Add(starts[idx] != valid_start).OnlyEnforceIf(s_match.Not())

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
        penalty_terms.append(T_pen * (is_present - program_start_ok))
        penalty_terms.append(T_pen * (is_present - program_end_ok))

    # 3. Time Preference Bonus (Aggregated to prevent double counting)
    bonus_opportunities = defaultdict(list)

    for i in range(n):
        seg = candidate_segments[i]
        for p_idx, pref in enumerate(time_prefs):
            if seg['genre'] == pref['preferred_genre']:
                p_end = model.NewIntVar(0, E, f"pend_{i}_{p_idx}")
                model.AddMinEquality(p_end, [ends[i], pref['end']])

                p_start = model.NewIntVar(0, E, f"pstart_{i}_{p_idx}")
                model.AddMaxEquality(p_start, [starts[i], pref['start']])

                bonus_applies = model.NewBoolVar(f"bonus_cond_{i}_{p_idx}")
                # Check if overlap >= D
                model.Add(p_end - p_start >= D).OnlyEnforceIf(bonus_applies)
                model.Add(p_end - p_start < D).OnlyEnforceIf(bonus_applies.Not())

                fin_bonus = model.NewBoolVar(f"fb_{i}_{p_idx}")
                model.AddBoolAnd([bonus_applies, is_selected[i]]).OnlyEnforceIf(fin_bonus)
                model.AddBoolOr([bonus_applies.Not(), is_selected[i].Not()]).OnlyEnforceIf(fin_bonus.Not())

                bonus_opportunities[(seg['id'], p_idx)].append(fin_bonus)

    for (pid, p_idx), bool_list in bonus_opportunities.items():
        if not bool_list: continue
        # If ANY segment of this program satisfies the bonus condition, award the bonus ONCE.
        award_bonus = model.NewBoolVar(f"award_bonus_{pid}_{p_idx}")
        model.AddBoolOr(bool_list).OnlyEnforceIf(award_bonus)
        model.AddBoolAnd([b.Not() for b in bool_list]).OnlyEnforceIf(award_bonus.Not())

        pref_val = time_prefs[p_idx]['bonus']
        obj_terms.append(pref_val * award_bonus)

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

            # Print details
            b_val = 0  # Difficult to back-trace exact bonus without querying vars
            r_pos = solver.Value(run_pos[i])
            base = prog_id_map[seg['id']]['score']
            print(f"{idx_order + 1}. {seg['id']:10} {s_val}-{e_val} (Ch{seg['channel']}) Base={base} Run={r_pos}")

        return {'scheduled_programs': out_list}

    return None


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 ilp.py <input> <output> [time_limit]")
    else:
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 300
        sol = solve_with_ortools(load_input(sys.argv[1]), limit)
        if sol:
            save_output(sol, sys.argv[2])
            print(f"Saved to {sys.argv[2]}")
