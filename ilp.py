"""
TV Channel Scheduling Optimization using Google OR-Tools (CP-SAT)
High-performance scheduler using Constraint Programming.

Improvements over ILP:
- Uses Interval Variables (NoOverlap constraint) -> O(n) constraints instead of O(n^2)
- Uses Circuit/Path constraints for sequencing
- Efficient Boolean logic for genre diversity

Usage:
    python3 ilp_ortools.py <input_file> <output_file> <time_limit_seconds>
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

    # Flatten programs and preprocessing (similar to original to ensure same candidates)
    # We duplicate the preprocessing here to ensure we work on the exact same set of potential intervals
    programs = []

    # We assign a global index to each potential program interval
    for channel in channels_data:
        cid = channel['channel_id']
        for prog in channel['programs']:
             # Skip programs strictly outside venue hours
            if prog['start'] >= E or prog['end'] <= O:
                continue

            valid_start = max(O, prog['start'])
            valid_end = min(E, prog['end'])

            # Priority blocks handling (filtering forbidden times)
            forbidden_intervals = []
            for block in priority_blocks:
                if cid not in block['allowed_channels']:
                    forbidden_intervals.append((block['start'], block['end']))

            candidate_windows = [(valid_start, valid_end)]
            if forbidden_intervals:
                current_candidates = []
                for start, end in candidate_windows:
                    # Check against every forbidden block (naive subtraction for simplicity as in original)
                    # Note: The original logic was a bit specific, we replicate it for consistency
                    temp_starts = [start]

                    found_collision = False
                    for f_start, f_end in forbidden_intervals:
                        # Overlap check
                        if not (end <= f_start or start >= f_end):
                            found_collision = True
                            # Split?
                            # Before block
                            if start < f_start and (f_start - start) >= D:
                                current_candidates.append((start, f_start))
                            # After block
                            if end > f_end and (end - f_end) >= D:
                                current_candidates.append((f_end, end))
                            break # Only handle one collision per segment loop for safety/simplicity or match original logic

                    if not found_collision:
                        current_candidates.append((start, end))
                candidate_windows = current_candidates

            for w_start, w_end in candidate_windows:
                if (w_end - w_start) < D:
                    continue

                # Pre-calculate penalties in score
                net_score = prog['score']
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

    # Pre-calculate bonuses
    # bonus[i] matches the bonus logic
    bonuses = [0] * n
    for i in range(n):
        p = programs[i]
        for pref in time_prefs:
            if p['genre'] == pref['preferred_genre']:
                overlap_start = max(p['start'], pref['start'])
                overlap_end = min(p['end'], pref['end'])
                if (overlap_end - overlap_start) >= D:
                    bonuses[i] += pref['bonus']

    # --- CP-SAT Model ---
    model = cp_model.CpModel()

    # 1. Variables
    # is_selected[i]: bool
    is_selected = [model.NewBoolVar(f"x_{i}") for i in range(n)]

    # Interval variables for NoOverlap
    # We perform "optional" intervals. If is_selected[i] is false, the interval is not active.
    intervals = []
    for i in range(n):
        # Start and End are constants in this problem version (we select fixed intervals),
        # but the PRESENCE is variable.
        # Fixed duration and start means size=duration, start=start, end=end.
        # But we make it optional based on is_selected[i].

        # NOTE: NewOptionalIntervalVar(start, size, end, is_present, name)
        # All arguments must be vars or integers.
        # Since start/end are fixed if selected, we can use integer constants.

        interval = model.NewOptionalIntervalVar(
            programs[i]['start'],
            programs[i]['duration'],
            programs[i]['end'],
            is_selected[i],
            f"interval_{i}"
        )
        intervals.append(interval)

    # 2. Overlap Constraints
    # NoOverlap requires a list of intervals.
    # We can refine this: only programs that *can* overlap need to be in the same NoOverlap constraint?
    # Global NoOverlap is the easiest: "No two selected intervals in the entire set can overlap."
    # Wait, the original problem says "No Overlapping Programs".
    # Usually this means on ANY COMPETING channel/resource or just globally?
    # Re-reading PDF/MD: "x_i + x_j <= 1" for CONFLICTS.
    # CONFLICTS(i) = programs that overlap with i.
    # So yes, a global NoOverlap or checking all pairs.
    # The most efficient way in CP-SAT is AddNoOverlap(all_intervals).
    # This ensures that if x_i and x_j are both true, their time intervals must not intersect.
    model.AddNoOverlap(intervals)

    # 3. Sequencing and Flow
    # The problem implies a single linear sequence of programs.
    # "seq_{i,j} = 1 if j follows i".
    # We can model this with a "Circuit" constraint or simply "Next" variables.
    # However, because we have OPTIONAL nodes (not all programs are selected),
    # a standard Circuit is tricky on a subset.
    # Approach:
    # We add a dummy "Start" and "End" node (or just one dummy node 0 for proper circuit).
    # But let's stick to the boolean logic which handles the "subset" case naturally.

    # trans[i, j] is true if i -> j is the immediate sequence.
    # Only valid if j can follow i.

    # Precompute valid transitions
    # j can follow i if j['start'] >= i['end']
    valid_transitions = defaultdict(list) # i -> list of j
    valid_incoming = defaultdict(list)    # j -> list of i

    possible_trans_indices = [] # Stores (i, j) tuples

    for i in range(n):
        for j in range(n):
            if i == j: continue
            if programs[j]['start'] >= programs[i]['end']:
                valid_transitions[i].append(j)
                valid_incoming[j].append(i)
                possible_trans_indices.append((i, j))

    # Create transition variables
    trans_vars = {}
    for i, j in possible_trans_indices:
        trans_vars[(i, j)] = model.NewBoolVar(f"trans_{i}_{j}")

    # is_first[i], is_last[i]
    is_first = [model.NewBoolVar(f"first_{i}") for i in range(n)]
    is_last = [model.NewBoolVar(f"last_{i}") for i in range(n)]

    # Constraints for flow:
    # If i is selected:
    #   sum(trans[i, j] for j) + is_last[i] == 1
    #   sum(trans[j, i] for j) + is_first[i] == 1
    # If not selected:
    #   sum(...) == 0
    #   is_first/last == 0

    for i in range(n):
        # Outgoing
        outgoing_expr = [trans_vars[(i, j)] for j in valid_transitions[i]]
        model.Add(sum(outgoing_expr) + is_last[i] == is_selected[i])

        # Incoming
        incoming_expr = [trans_vars[(j, i)] for j in valid_incoming[i]]
        model.Add(sum(incoming_expr) + is_first[i] == is_selected[i])

    # At most one first, at most one last
    model.Add(sum(is_first) <= 1)
    model.Add(sum(is_last) <= 1)

    # Ensure graph connectivity (only one connected component).
    # This is often the hardest part in custom flow models.
    # If we maximize score, the solver "wants" to pick many programs.
    # Without connectivity, it could pick [A->B] and [C->D] as two separate disjoint independent chains
    # if they don't overlap in time.
    # The ILP formulation in the PDF enforces a single chains via "one first" and "one last".
    # Wait, does the ILP enforce connectivity?
    # "Flow conservation" usually allows disjoint cycles if not careful, but here we have:
    #   sum(first) <= 1.
    #   If we have two chains, we would need two "firsts" (unless cycles exist).
    #   Cycles? i -> j -> i.
    #   Since time is strictly increasing (end > start) and j starts >= i ends,
    #   cycles are impossible (DAG).
    #   Therefore, simple flow constraints + "one first" is sufficient to guarantee a single chain (or empty).
    #   Checking DAG property:
    #      prog['start'] < prog['end'] (duration > 0).
    #      j follows i means start_j >= end_i > start_i.
    #      So start time strictly increases along the chain. No cycles.
    #   Conclusion: The flow constraints above are sufficient. No specialized Circuit needed.

    # 4. Genre Diversity
    # "Max R consecutive programs of same genre".
    # RunPosition[i] in [1, R].
    # If trans[i, j] and genre[i] == genre[j] => pos[j] = pos[i] + 1
    # If trans[i, j] and genre[i] != genre[j] => pos[j] = 1
    # If is_first[i] => pos[i] = 1 (implied actually? No, needs enforcement)

    run_pos = [model.NewIntVar(1, R, f"run_{i}") for i in range(n)]

    # If not selected, run_pos doesn't matter, but lets keep it clean.
    # We enforce constraints only if transition happens.

    for i in range(n):
        # Case: First program -> Run 1
        # model.Add(run_pos[i] == 1).OnlyEnforceIf(is_first[i])
        # Actually, simpler:
        model.Add(run_pos[i] == 1).OnlyEnforceIf(is_first[i])

        for j in valid_transitions[i]:
            t_var = trans_vars[(i, j)]
            if programs[i]['genre'] == programs[j]['genre']:
                # Same genre: increment
                # Check if we can increment? R is max.
                # If pos[i] was R, then pos[j] would be R+1 which is invalid.
                # The domain of run_pos is [1, R]. So if pos[i]=R, this constraint
                # run_pos[j] = R+1 would imply the model is infeasible for this transition.
                # Exactly what we want (forbids the transition).
                model.Add(run_pos[j] == run_pos[i] + 1).OnlyEnforceIf(t_var)
            else:
                # Diff genre: reset
                model.Add(run_pos[j] == 1).OnlyEnforceIf(t_var)

    # 5. Objective
    # Maximize sum(score) - sum(penalties)
    # Score comes from x[i]
    # Penalty comes from transitions (switches)

    # Base scores + Bonuses
    obj_terms = []
    for i in range(n):
        total_val = programs[i]['score'] + bonuses[i]
        obj_terms.append(total_val * is_selected[i])

    # Penalties
    penalty_terms = []
    for (i, j), t_var in trans_vars.items():
        if programs[i]['channel'] != programs[j]['channel']:
            penalty_terms.append(S_pen * t_var)

    model.Maximize(sum(obj_terms) - sum(penalty_terms))

    # --- Solve ---
    print(f"Solving with CP-SAT (Time limit: {time_limit}s)...")
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit

    # Optional: Parallel workers
    solver.parameters.num_search_workers = 8 # Utilization of cores

    status = solver.Solve(model)
    print(f"Status: {solver.StatusName(status)}")

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        # Reconstruct
        print("\nSolution Found!")

        # Find path
        selected_indices = [i for i in range(n) if solver.Value(is_selected[i])]

        if not selected_indices:
            print("No programs selected.")
            return {'scheduled_programs': []}

        # Find first
        start_node = None
        for i in selected_indices:
            if solver.Value(is_first[i]):
                start_node = i
                break

        schedule = []
        curr = start_node
        # Follow transitions
        while curr is not None:
            schedule.append(curr)

            # Find next
            next_node = None
            for j in valid_transitions[curr]:
                if solver.Value(trans_vars[(curr, j)]):
                    next_node = j
                    break
            curr = next_node

        # Build Output
        out_list = []
        total_score_val = 0
        total_penalty_val = 0

        print("\nSchedule:")
        for idx, i in enumerate(schedule):
            p = programs[i]
            out_list.append({
                'program_id': p['id'],
                'channel_id': p['channel'],
                'start': p['start'],
                'end': p['end']
            })

            # Debug info
            r_pos = solver.Value(run_pos[i])
            b_val = bonuses[i]
            print(f"{idx+1:2}. {p['id']:10} Ch{p['channel']} {p['start']}-{p['end']} "
                  f"({p['genre']}) Score={p['score']}+{b_val} Run={r_pos}")

        print(f"Objective Value: {solver.ObjectiveValue()}")
        return {'scheduled_programs': out_list}

    else:
        print("No solution found.")
        return None

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 ilp_ortools.py <input> <output> [time_limit]")
        # Default for testing
        input_file = "inputs/toy_tv_input.json"
        output_file = "outputs/ortools_toy.json"
        time_limit = 60
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
