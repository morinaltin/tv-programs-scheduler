"""
TV Channel Scheduling Optimization using Google OR-Tools (CP-SAT)
High-performance scheduler using Constraint Programming.

Improvements:
- Uses Interval Variables (NoOverlap constraint)
- Uses Boolean logic for sequencing
- STRICT GENRE CONSTRAINT: Gaps do NOT reset the run count.
- TERMINATION PENALTY RESTORED: Dynamic calculation based on original boundaries.
- SCORE CORRECTION: Prevents double-counting of base scores for split programs.
- MEMORY OPTIMIZATION: Prunes transition graph to nearest 100 neighbors.

Usage:
    python3 ilp_memory-optimized.py <input_file> <output_file> <time_limit_seconds>
    ./.venv/bin/python3.11 ilp_memory-optimized.py inputs/toy_tv_input.json outputs/ilp_toy.json 7200
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

                programs.append({
                    'id': prog['program_id'],
                    'channel': cid,
                    'start': w_start,
                    'end': w_end,
                    'duration': w_end - w_start,
                    'genre': prog['genre'],
                    'base_score_ref': prog['score'],
                    'original_start': prog['start'],
                    'original_end': prog['end'],
                    'is_start_node': (w_start == prog['start']),
                    'is_end_node': (w_end == prog['end'])
                })

    n = len(programs)
    print(f"Programs after filtering: {n}")

    programs.sort(key=lambda x: x['start'])

    prog_id_map = {}

    for i in range(n):
        p = programs[i]
        p['idx'] = i

        bonus = 0
        for pref in time_prefs:
            if p['genre'] == pref['preferred_genre']:
                overlap_start = max(p['start'], pref['start'])
                overlap_end = min(p['end'], pref['end'])
                if (overlap_end - overlap_start) >= D:
                    bonus += pref['bonus']
        p['bonus'] = bonus

        if p['id'] not in prog_id_map:
            prog_id_map[p['id']] = {
                'base_score': p['base_score_ref'],
                'indices': []
            }
        prog_id_map[p['id']]['indices'].append(i)

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

    K_NEIGHBORS = 100

    for i in range(n):
        count = 0
        for j in range(i + 1, n):
            if programs[j]['start'] >= programs[i]['end']:
                valid_transitions[i].append(j)
                valid_incoming[j].append(i)
                possible_trans_indices.append((i, j))
                count += 1
                if count >= K_NEIGHBORS:
                    break

    print(f"Transition variables created: {len(possible_trans_indices)}")

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

    for pid, info in prog_id_map.items():
        indices = info['indices']
        if not indices:
            continue

        is_present = model.NewBoolVar(f"present_{pid}")
        model.AddMaxEquality(is_present, [is_selected[i] for i in indices])

        obj_terms.append(info['base_score'] * is_present)

        start_indices = [i for i in indices if programs[i]['is_start_node']]
        if start_indices:
            has_start = model.NewBoolVar(f"has_start_{pid}")
            model.AddMaxEquality(has_start, [is_selected[i] for i in start_indices])
            penalty_terms.append(T_pen * (is_present - has_start))
        else:
            penalty_terms.append(T_pen * is_present)

        end_indices = [i for i in indices if programs[i]['is_end_node']]
        if end_indices:
            has_end = model.NewBoolVar(f"has_end_{pid}")
            model.AddMaxEquality(has_end, [is_selected[i] for i in end_indices])
            penalty_terms.append(T_pen * (is_present - has_end))
        else:
            penalty_terms.append(T_pen * is_present)

    for i in range(n):
        if programs[i]['bonus'] > 0:
            obj_terms.append(programs[i]['bonus'] * is_selected[i])

    model.Maximize(sum(obj_terms) - sum(penalty_terms))

    print("Building model complete. Solving...")
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = 8

    status = solver.Solve(model)
    print(f"Status: {solver.StatusName(status)}")

    end_time = time.time()
    elapsed = end_time - start_time
    print(f"Total Execution Time: {elapsed:.2f} seconds")

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
            b_val = p['bonus']
            print(f"{idx+1:2}. {p['id']:10} Ch{p['channel']} {p['start']}-{p['end']} "
                  f"({p['genre']}) Base={prog_id_map[p['id']]['base_score']} Bonus={b_val} Run={r_pos}")

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
        try:
            data = load_input(input_file)
            sol = solve_with_ortools(data, time_limit)
            if sol:
                save_output(sol, output_file)
                print(f"Saved to {output_file}")
        except Exception as e:
            print(f"\nERROR: {e}")
            import traceback
            traceback.print_exc()
