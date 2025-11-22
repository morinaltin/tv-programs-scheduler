"""
TV Channel Scheduling Optimization using Integer Linear Programming
Fixed version with proper genre diversity constraint.

Install: pip install pulp
Run: python solution1.py input.json output.json 300
"""

import json
from pulp import *
from collections import defaultdict
import sys

def load_input(filepath):
    with open(filepath, 'r') as f:
        return json.load(f)

def solve_tv_scheduling(input_data, time_limit=300):
    # Extract parameters
    O = input_data['opening_time']
    E = input_data['closing_time']
    D = input_data['min_duration']
    R = input_data['max_consecutive_genre']
    C = input_data['channels_count']
    S_pen = input_data['switch_penalty']
    T_pen = input_data['termination_penalty']
    priority_blocks = input_data.get('priority_blocks', [])
    time_prefs = input_data.get('time_preferences', [])
    channels = input_data['channels']

    # Build program list - use full original times only (no partial scheduling)
    programs = []

    for channel in channels:
        for prog in channel['programs']:
            # Skip programs outside venue hours
            if prog['start'] < O or prog['end'] > E:
                continue
            if prog['start'] >= E or prog['end'] <= O:
                continue

            duration = prog['end'] - prog['start']

            # Check minimum duration
            if duration < D:
                continue  # Skip programs shorter than minimum

            # Check priority block restrictions
            blocked = False
            for block in priority_blocks:
                # If program overlaps with block
                if prog['start'] < block['end'] and prog['end'] > block['start']:
                    if channel['channel_id'] not in block['allowed_channels']:
                        blocked = True
                        break

            if blocked:
                continue

            programs.append({
                'idx': len(programs),
                'id': prog['program_id'],
                'channel': channel['channel_id'],
                'start': prog['start'],
                'end': prog['end'],
                'genre': prog['genre'],
                'score': prog['score'],
                'duration': duration
            })

    n = len(programs)
    print(f"Programs after filtering: {n}")

    # Sort programs by start time for easier constraint building
    programs.sort(key=lambda p: (p['start'], p['end']))
    for i, p in enumerate(programs):
        p['idx'] = i

    # Build conflict graph (overlapping programs)
    conflicts = defaultdict(set)
    for i in range(n):
        for j in range(i+1, n):
            pi, pj = programs[i], programs[j]
            # Check overlap
            if not (pi['end'] <= pj['start'] or pj['end'] <= pi['start']):
                conflicts[i].add(j)
                conflicts[j].add(i)

    # Build "can follow" relationship
    # j can follow i if j starts at or after i ends
    can_follow = defaultdict(list)
    for i in range(n):
        for j in range(n):
            if i != j and programs[j]['start'] >= programs[i]['end']:
                can_follow[i].append(j)

    # Get all genres
    all_genres = list(set(p['genre'] for p in programs))
    genre_to_idx = {g: i for i, g in enumerate(all_genres)}

    # Create ILP problem
    prob = LpProblem("TV_Scheduling", LpMaximize)

    # === DECISION VARIABLES ===

    # x[i] = 1 if program i is selected
    x = LpVariable.dicts("x", range(n), cat='Binary')

    # seq[i,j] = 1 if program j immediately follows program i in the schedule
    seq = {}
    for i in range(n):
        for j in can_follow[i]:
            seq[(i,j)] = LpVariable(f"seq_{i}_{j}", cat='Binary')

    # is_first[i] = 1 if program i is the first in schedule
    is_first = LpVariable.dicts("first", range(n), cat='Binary')

    # is_last[i] = 1 if program i is the last in schedule
    is_last = LpVariable.dicts("last", range(n), cat='Binary')

    # For genre constraint: genre_run[i,g,k] = 1 if program i is the k-th consecutive program of genre g
    # k ranges from 1 to R (we don't allow R+1)
    genre_run = {}
    for i in range(n):
        g = programs[i]['genre']
        for k in range(1, R + 1):
            genre_run[(i, k)] = LpVariable(f"run_{i}_{k}", cat='Binary')

    # === OBJECTIVE FUNCTION ===
    obj = lpSum(programs[i]['score'] * x[i] for i in range(n))

    # Add bonuses for genre-time preferences
    for i, prog in enumerate(programs):
        for pref in time_prefs:
            if prog['genre'] == pref['preferred_genre']:
                # Check overlap with preference window
                overlap_start = max(prog['start'], pref['start'])
                overlap_end = min(prog['end'], pref['end'])
                if overlap_end - overlap_start >= D:
                    obj += pref['bonus'] * x[i]

    # Subtract switch penalties
    for (i, j), var in seq.items():
        if programs[i]['channel'] != programs[j]['channel']:
            obj -= S_pen * var

    prob += obj, "Total_Score"

    # === CONSTRAINTS ===

    # 1. No overlapping programs
    for i in range(n):
        for j in conflicts[i]:
            if i < j:
                prob += x[i] + x[j] <= 1, f"no_overlap_{i}_{j}"

    # 2. Flow conservation for sequencing
    # Each selected program (except last) has exactly one successor
    # Each selected program (except first) has exactly one predecessor

    for i in range(n):
        # Outgoing flow
        successors = [seq[(i,j)] for j in can_follow[i] if (i,j) in seq]
        if successors:
            prob += lpSum(successors) + is_last[i] == x[i], f"out_flow_{i}"
        else:
            prob += is_last[i] == x[i], f"out_flow_no_succ_{i}"

        # Incoming flow
        predecessors = [seq[(j,i)] for j in range(n) if (j,i) in seq]
        if predecessors:
            prob += lpSum(predecessors) + is_first[i] == x[i], f"in_flow_{i}"
        else:
            prob += is_first[i] == x[i], f"in_flow_no_pred_{i}"

    # At most one first program
    prob += lpSum(is_first[i] for i in range(n)) <= 1, "one_first"

    # At most one last program
    prob += lpSum(is_last[i] for i in range(n)) <= 1, "one_last"

    # 3. Genre diversity constraint using run counting
    for i in range(n):
        g = programs[i]['genre']

        # If selected, program must have exactly one run position
        prob += lpSum(genre_run[(i, k)] for k in range(1, R + 1)) == x[i], f"one_run_pos_{i}"

        # If program is first OR preceded by different genre, run position = 1
        # Find predecessors with same genre
        same_genre_preds = [j for j in range(n) if (j,i) in seq and programs[j]['genre'] == g]
        diff_genre_preds = [j for j in range(n) if (j,i) in seq and programs[j]['genre'] != g]

        # genre_run[i,1] = 1 if is_first[i] OR any diff_genre_pred leads to i
        if same_genre_preds:
            # If preceded by same genre at run k, then i is at run k+1
            for k in range(1, R):
                for j in same_genre_preds:
                    prob += genre_run[(i, k+1)] >= seq[(j,i)] + genre_run[(j, k)] - 1, f"run_cont_{j}_{i}_{k}"

            # If preceded by different genre or is first, run = 1
            prob += genre_run[(i, 1)] >= is_first[i], f"run_first_{i}"
            for j in diff_genre_preds:
                prob += genre_run[(i, 1)] >= seq[(j,i)], f"run_reset_{j}_{i}"
        else:
            # No same-genre predecessors possible, so if selected and has predecessor, must be run 1
            prob += genre_run[(i, 1)] >= x[i] - is_first[i] - lpSum(seq[(j,i)] for j in same_genre_preds if (j,i) in seq), f"run_default_{i}"

    # 4. Prevent run position R from having same-genre successor
    # If program i is at run position R, it cannot be followed by same genre
    for i in range(n):
        g = programs[i]['genre']
        same_genre_succs = [j for j in can_follow[i] if (i,j) in seq and programs[j]['genre'] == g]
        for j in same_genre_succs:
            prob += seq[(i,j)] + genre_run[(i, R)] <= 1, f"no_exceed_R_{i}_{j}"

    # Solve
    print(f"Solving ILP (time limit: {time_limit}s)...")
    print(f"Variables: {len(prob.variables())}, Constraints: {len(prob.constraints)}")

    solver = PULP_CBC_CMD(msg=1, timeLimit=time_limit)
    prob.solve(solver)

    print(f"Status: {LpStatus[prob.status]}")

    if prob.status not in [LpStatusOptimal, LpStatusNotSolved]:
        print("No feasible solution found")
        return None

    # Extract solution
    selected = [i for i in range(n) if value(x[i]) is not None and value(x[i]) > 0.5]

    # Build ordered schedule using seq variables
    schedule = []
    if selected:
        # Find first program
        first_prog = None
        for i in selected:
            if value(is_first[i]) > 0.5:
                first_prog = i
                break

        if first_prog is None and selected:
            # Fallback: sort by start time
            selected.sort(key=lambda i: programs[i]['start'])
            schedule = selected
        else:
            # Follow the chain
            current = first_prog
            while current is not None:
                schedule.append(current)
                next_prog = None
                for j in can_follow[current]:
                    if (current, j) in seq and value(seq[(current, j)]) > 0.5:
                        next_prog = j
                        break
                current = next_prog

    # Build output
    scheduled_programs = []
    for i in schedule:
        prog = programs[i]
        scheduled_programs.append({
            'program_id': prog['id'],
            'channel_id': prog['channel'],
            'start': prog['start'],
            'end': prog['end']
        })

    # Calculate and verify score
    print("\n" + "="*60)
    print("SOLUTION SUMMARY")
    print("="*60)

    total_base = sum(programs[i]['score'] for i in schedule)
    print(f"Base score: {total_base}")

    total_bonus = 0
    for i in schedule:
        prog = programs[i]
        for pref in time_prefs:
            if prog['genre'] == pref['preferred_genre']:
                overlap_start = max(prog['start'], pref['start'])
                overlap_end = min(prog['end'], pref['end'])
                if overlap_end - overlap_start >= D:
                    total_bonus += pref['bonus']
    print(f"Bonus score: {total_bonus}")

    switches = 0
    for k in range(len(schedule) - 1):
        if programs[schedule[k]]['channel'] != programs[schedule[k+1]]['channel']:
            switches += 1
    print(f"Channel switches: {switches} (penalty: -{switches * S_pen})")

    # Verify genre constraint
    genre_run_check = 1
    last_genre = None
    genre_violation = False
    for i in schedule:
        g = programs[i]['genre']
        if g == last_genre:
            genre_run_check += 1
            if genre_run_check > R:
                print(f"WARNING: Genre violation at {programs[i]['id']} ({g})")
                genre_violation = True
        else:
            genre_run_check = 1
        last_genre = g

    if genre_violation:
        print("GENRE CONSTRAINT VIOLATED!")
    else:
        print(f"Genre constraint: OK (max {R} consecutive)")

    total_score = total_base + total_bonus - switches * S_pen
    print(f"\nTOTAL SCORE: {total_score}")

    print(f"\nSchedule ({len(schedule)} programs):")
    for idx, i in enumerate(schedule):
        prog = programs[i]
        st, et = prog['start'], prog['end']
        print(f"  {idx+1:2}. {prog['id']:25} Ch{prog['channel']} "
              f"{st//60:02d}:{st%60:02d}-{et//60:02d}:{et%60:02d} "
              f"{prog['genre']:15} score={prog['score']}")

    return {'scheduled_programs': scheduled_programs}

def save_output(output_data, filepath):
    with open(filepath, 'w') as f:
        json.dump(output_data, f, indent=4)

if __name__ == "__main__":
    input_file = sys.argv[1] if len(sys.argv) > 1 else "germany_tv_input.json"
    output_file = sys.argv[2] if len(sys.argv) > 2 else "ilp_solution.json"
    time_limit = int(sys.argv[3]) if len(sys.argv) > 3 else 300

    print(f"Loading input from {input_file}...")
    input_data = load_input(input_file)

    solution = solve_tv_scheduling(input_data, time_limit)

    if solution:
        save_output(solution, output_file)
        print(f"\nSolution saved to {output_file}")