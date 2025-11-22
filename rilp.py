"""
TV Channel Scheduling - Lightweight Relaxed ILP
Aggressive simplification for very large instances.

Key simplifications:
1. Use interval graph clique constraints instead of pairwise overlaps
2. Simplified genre constraints
3. Robust error handling

Run: python solution_relaxed.py input.json output.json 300
"""

import json
import sys
from pulp import *
from collections import defaultdict
import time

def load_input(filepath):
    with open(filepath, 'r') as f:
        return json.load(f)

def solve_tv_scheduling(input_data, time_limit=300):
    start_time = time.time()

    O = input_data['opening_time']
    E = input_data['closing_time']
    D = input_data['min_duration']
    R = input_data['max_consecutive_genre']
    S_pen = input_data['switch_penalty']
    T_pen = input_data['termination_penalty']
    priority_blocks = input_data.get('priority_blocks', [])
    time_prefs = input_data.get('time_preferences', [])
    channels = input_data['channels']

    # Build program list
    programs = []
    for channel in channels:
        for prog in channel['programs']:
            if prog['start'] < O or prog['end'] > E:
                continue
            if prog['end'] - prog['start'] < D:
                continue

            blocked = False
            for block in priority_blocks:
                if prog['start'] < block['end'] and prog['end'] > block['start']:
                    if channel['channel_id'] not in block['allowed_channels']:
                        blocked = True
                        break
            if blocked:
                continue

            bonus = 0
            for pref in time_prefs:
                if prog['genre'] == pref['preferred_genre']:
                    ov_start = max(prog['start'], pref['start'])
                    ov_end = min(prog['end'], pref['end'])
                    if ov_end - ov_start >= D:
                        bonus += pref['bonus']

            programs.append({
                'idx': len(programs),
                'id': prog['program_id'],
                'channel': channel['channel_id'],
                'start': prog['start'],
                'end': prog['end'],
                'genre': prog['genre'],
                'score': prog['score'],
                'bonus': bonus
            })

    programs.sort(key=lambda p: p['start'])
    for i, p in enumerate(programs):
        p['idx'] = i

    n = len(programs)
    print(f"Programs after filtering: {n}")

    # === BUILD INTERVAL CLIQUES ===
    # Instead of pairwise overlap constraints, find maximal cliques
    # For interval graphs, we can use a sweep line approach

    events = []
    for i, p in enumerate(programs):
        events.append((p['start'], 'start', i))
        events.append((p['end'], 'end', i))
    events.sort(key=lambda e: (e[0], e[1] == 'start'))  # ends before starts at same time

    # Find all maximal cliques using sweep line
    active = set()
    cliques = []

    for time_point, event_type, prog_idx in events:
        if event_type == 'start':
            active.add(prog_idx)
            # Current active set is a clique
            if len(active) > 1:
                cliques.append(frozenset(active))
        else:
            active.discard(prog_idx)

    # Remove dominated cliques (subsets of other cliques)
    cliques = list(set(cliques))
    cliques.sort(key=lambda c: -len(c))

    maximal_cliques = []
    for c in cliques:
        is_subset = False
        for mc in maximal_cliques:
            if c <= mc:
                is_subset = True
                break
        if not is_subset:
            maximal_cliques.append(c)

    print(f"Found {len(maximal_cliques)} maximal overlap cliques")

    # Create ILP
    prob = LpProblem("TV_Schedule", LpMaximize)

    # Variables
    x = LpVariable.dicts("x", range(n), cat='Binary')

    # Objective
    obj = lpSum((programs[i]['score'] + programs[i]['bonus']) * x[i] for i in range(n))
    prob += obj

    # Constraint 1: Clique constraints (at most 1 from each clique)
    for idx, clique in enumerate(maximal_cliques):
        if len(clique) > 1:
            prob += lpSum(x[i] for i in clique) <= 1, f"clique_{idx}"

    # Constraint 2: Genre diversity (simplified)
    # For each genre, in any time window of W minutes, limit selections
    # This is a heuristic approximation
    genre_progs = defaultdict(list)
    for i, p in enumerate(programs):
        genre_progs[p['genre']].append(i)

    # Only add constraints for genres with many programs
    for genre, prog_list in genre_progs.items():
        if len(prog_list) <= R:
            continue

        sorted_list = sorted(prog_list, key=lambda i: programs[i]['start'])

        # Sliding window: any R+1 consecutive programs (by time) can't all be selected
        # if they could potentially be scheduled consecutively
        for w in range(len(sorted_list) - R):
            window = sorted_list[w:w + R + 1]

            # Quick check: if first and last don't overlap, they might all fit
            first, last = window[0], window[-1]
            if programs[last]['start'] >= programs[first]['end']:
                # Check if no gaps large enough for another program
                could_be_consecutive = True
                for k in range(len(window) - 1):
                    gap = programs[window[k+1]]['start'] - programs[window[k]]['end']
                    if gap >= D:  # Another program could fit
                        could_be_consecutive = False
                        break

                if could_be_consecutive:
                    prob += lpSum(x[i] for i in window) <= R, f"g_{hash(genre) % 10000}_{w}"

    print(f"Constraints: {len(prob.constraints)}")

    # Solve with timeout and error handling
    ilp_time = min(time_limit * 0.7, time_limit - 60)
    print(f"Solving ILP ({ilp_time:.0f}s limit)...")

    try:
        solver = PULP_CBC_CMD(msg=1, timeLimit=ilp_time, options=['maxSolutions 10'])
        prob.solve(solver)
        status = LpStatus[prob.status]
    except Exception as e:
        print(f"Solver error: {e}")
        status = "Error"

    print(f"Status: {status}")

    # Extract solution
    selected = []
    if status in ["Optimal", "Not Solved"]:  # Not Solved means time limit with solution
        selected = [i for i in range(n) if value(x[i]) is not None and value(x[i]) > 0.5]

    if not selected:
        print("No ILP solution, using greedy fallback...")
        selected = greedy_solution(programs, R, D)

    selected.sort(key=lambda i: programs[i]['start'])
    print(f"Selected {len(selected)} programs")

    # Post-process: fix genre violations
    schedule = fix_genre_violations(selected, programs, R)

    # Try to add more programs
    schedule = fill_gaps(schedule, programs, n, R, D)

    # Final score
    schedule.sort(key=lambda i: programs[i]['start'])
    total_base = sum(programs[i]['score'] for i in schedule)
    total_bonus = sum(programs[i]['bonus'] for i in schedule)
    switches = sum(1 for k in range(len(schedule)-1)
                   if programs[schedule[k]]['channel'] != programs[schedule[k+1]]['channel'])

    print("\n" + "="*60)
    print("SOLUTION")
    print("="*60)
    print(f"Programs: {len(schedule)}")
    print(f"Base: {total_base}")
    print(f"Bonus: {total_bonus}")
    print(f"Switches: {switches} (penalty: -{switches * S_pen})")
    print(f"TOTAL: {total_base + total_bonus - switches * S_pen}")
    print(f"Time: {time.time() - start_time:.1f}s")

    # Verify
    violations = check_genre(schedule, programs, R)
    if violations:
        print(f"WARNING: {len(violations)} genre violations!")

    output = {
        'scheduled_programs': [
            {
                'program_id': programs[i]['id'],
                'channel_id': programs[i]['channel'],
                'start': programs[i]['start'],
                'end': programs[i]['end']
            }
            for i in schedule
        ]
    }
    return output

def greedy_solution(programs, R, D):
    """Greedy fallback solution"""
    n = len(programs)
    sorted_idx = sorted(range(n), key=lambda i: -(programs[i]['score'] + programs[i]['bonus']))

    schedule = []
    last_end = -1
    genre_run = 0
    last_genre = None

    for i in sorted_idx:
        p = programs[i]
        if p['start'] >= last_end:
            # Check genre
            if p['genre'] == last_genre:
                if genre_run >= R:
                    continue
                genre_run += 1
            else:
                genre_run = 1
                last_genre = p['genre']

            schedule.append(i)
            last_end = p['end']

    return sorted(schedule, key=lambda i: programs[i]['start'])

def check_genre(schedule, programs, R):
    """Check genre violations"""
    violations = []
    run = 1
    last_g = None
    for idx, i in enumerate(schedule):
        g = programs[i]['genre']
        if g == last_g:
            run += 1
            if run > R:
                violations.append(idx)
        else:
            run = 1
        last_g = g
    return violations

def fix_genre_violations(schedule, programs, R):
    """Remove programs to fix genre violations"""
    schedule = list(schedule)

    max_iter = 100
    for _ in range(max_iter):
        violations = check_genre(schedule, programs, R)
        if not violations:
            break

        # Find the run containing first violation
        v = violations[0]
        g = programs[schedule[v]]['genre']

        # Find run boundaries
        start = v
        while start > 0 and programs[schedule[start-1]]['genre'] == g:
            start -= 1
        end = v
        while end < len(schedule)-1 and programs[schedule[end+1]]['genre'] == g:
            end += 1

        # Remove lowest value in run
        run_indices = list(range(start, end + 1))
        worst = min(run_indices, key=lambda idx: programs[schedule[idx]]['score'] + programs[schedule[idx]]['bonus'])
        schedule.pop(worst)

    return schedule

def fill_gaps(schedule, programs, n, R, D):
    """Try to add more programs"""
    schedule = list(schedule)
    used = set(schedule)
    unused = sorted([i for i in range(n) if i not in used],
                    key=lambda i: -(programs[i]['score'] + programs[i]['bonus']))

    for prog_idx in unused:
        p = programs[prog_idx]

        # Find position
        pos = 0
        for k, s in enumerate(schedule):
            if programs[s]['start'] >= p['end']:
                break
            if programs[s]['end'] <= p['start']:
                pos = k + 1

        # Check overlap
        if pos > 0 and programs[schedule[pos-1]]['end'] > p['start']:
            continue
        if pos < len(schedule) and p['end'] > programs[schedule[pos]]['start']:
            continue

        # Check genre
        test = schedule[:pos] + [prog_idx] + schedule[pos:]
        if not check_genre(test, programs, R):
            schedule = test
            used.add(prog_idx)

    return schedule

def save_output(data, filepath):
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=4)

if __name__ == "__main__":
    input_file = sys.argv[1] if len(sys.argv) > 1 else "input.json"
    output_file = sys.argv[2] if len(sys.argv) > 2 else "output.json"
    time_limit = int(sys.argv[3]) if len(sys.argv) > 3 else 300

    print(f"Input: {input_file}")
    data = load_input(input_file)
    result = solve_tv_scheduling(data, time_limit)

    if result:
        save_output(result, output_file)
        print(f"\nSaved to {output_file}")