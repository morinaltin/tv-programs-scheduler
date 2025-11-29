import json
import sys
from pulp import *


def load_input(filepath):
    with open(filepath, 'r') as f:
        return json.load(f)


def solve_relaxed_ilp(input_data, time_limit=300):
    print("--- Setting up Relaxed ILP (Ignoring Switch Penalties & Genre Limits) ---")

    # 1. Extract Constants
    O = input_data['opening_time']
    E = input_data['closing_time']
    D = input_data['min_duration']
    # R (Genre limit) and S (Switch penalty) are IGNORED in this relaxed version

    priority_blocks = input_data.get('priority_blocks', [])
    time_prefs = input_data.get('time_preferences', [])
    channels = input_data['channels']

    # 2. Pre-process Programs
    # We filter out programs that physically cannot be played (too short, wrong time)
    # But we keep everything else.
    valid_programs = []

    for channel in channels:
        chan_id = channel['channel_id']
        for prog in channel['programs']:
            p_start = prog['start']
            p_end = prog['end']
            duration = p_end - p_start

            # 1. Global Time Window Check
            if p_start < O or p_end > E: continue
            if p_start >= E or p_end <= O: continue

            # 2. Min Duration Check
            if duration < D: continue

            # 3. Priority Block Check (Hard Constraint - cannot be violated)
            is_blocked = False
            for block in priority_blocks:
                # If program overlaps with block
                if p_start < block['end'] and p_end > block['start']:
                    if chan_id not in block['allowed_channels']:
                        is_blocked = True
                        break
            if is_blocked: continue

            # 4. Calculate "Self-Contained" Score
            # Since we ignore penalties, the score is just Base + Bonus
            current_score = prog['score']
            for pref in time_prefs:
                if prog['genre'] == pref['preferred_genre']:
                    overlap_start = max(p_start, pref['start'])
                    overlap_end = min(p_end, pref['end'])
                    # Bonus applies if overlap is at least D
                    if (overlap_end - overlap_start) >= D:
                        current_score += pref['bonus']

            valid_programs.append({
                'id': prog['program_id'],
                'channel': chan_id,
                'start': p_start,
                'end': p_end,
                'genre': prog['genre'],
                'score_val': current_score,
                'original_obj': prog
            })

    n = len(valid_programs)
    print(f"Programs considered: {n}")

    # 3. Create ILP Model
    prob = LpProblem("Relaxed_TV_Schedule", LpMaximize)

    # Variables: x[i] = 1 if program i is selected, 0 otherwise
    x = LpVariable.dicts("x", range(n), cat='Binary')

    # Objective: Maximize sum of scores (ignoring switch costs)
    prob += lpSum([valid_programs[i]['score_val'] * x[i] for i in range(n)])

    # 4. Constraints
    # We ONLY apply the Overlap Constraint.
    # (Genre constraints and Sequence constraints are removed)

    # Efficient Overlap: Discretize time into 1-minute buckets
    # For every minute t, sum of active programs <= 1
    # We only care about minutes where a program actually starts or ends to save memory,
    # but iterating strict minutes is safer for correctness.

    # Map time points to programs that cover them
    time_map = {}
    for i in range(n):
        p = valid_programs[i]
        # Range is inclusive start, exclusive end
        for t in range(p['start'], p['end']):
            if t not in time_map: time_map[t] = []
            time_map[t].append(i)

    # Add constraints for relevant time points
    # (Optimization: We only need to check 'start' times of overlapping interactions,
    # but checking every occupied minute is the most robust implementation)
    count_constraints = 0
    for t in time_map:
        if len(time_map[t]) > 1:  # Only need constraint if overlap is possible
            prob += lpSum([x[i] for i in time_map[t]]) <= 1
            count_constraints += 1

    print(f"Constraints added: {count_constraints}")

    # 5. Solve
    # We use PULP_CBC_CMD (Coin-OR Branch and Cut)
    # Msg=0 turns off the verbose solver logs
    solver = PULP_CBC_CMD(msg=0, timeLimit=time_limit)
    prob.solve(solver)

    status = LpStatus[prob.status]
    print(f"Solver Status: {status}")

    if status != 'Optimal':
        print("Could not find optimal solution.")
        return None

    # 6. Extract Result
    selected_indices = [i for i in range(n) if value(x[i]) == 1]

    # Sort by start time for the output list
    selected_indices.sort(key=lambda i: valid_programs[i]['start'])

    schedule_output = []
    final_score = 0

    print("\n--- RELAXED SCHEDULE (No Penalties) ---")
    for idx in selected_indices:
        p = valid_programs[idx]
        final_score += p['score_val']
        schedule_output.append({
            "program_id": p['id'],
            "channel_id": p['channel'],
            "start": p['start'],
            "end": p['end']
        })
        print(f"[{p['start']}-{p['end']}] {p['id']} (Score: {p['score_val']})")

    print("=" * 40)
    print(f"CALCULATED RELAXED SCORE: {final_score}")
    print("=" * 40)

    return {"scheduled_programs": schedule_output, "total_score": final_score}


def save_output(output_data, filepath):
    # Remove the score key before saving to keep format identical to competition spec
    data_to_save = {"scheduled_programs": output_data["scheduled_programs"]}
    with open(filepath, 'w') as f:
        json.dump(data_to_save, f, indent=4)


if __name__ == "__main__":
    # Default values
    input_file = "kosovo_tv_input.json"
    output_file = "relaxed_solution.json"
    time_limit = 300

    # Parse command line arguments if provided
    # Format: python script.py [input_file] [output_file] [time_limit]
    if len(sys.argv) > 1:
        input_file = sys.argv[1]
    if len(sys.argv) > 2:
        output_file = sys.argv[2]
    if len(sys.argv) > 3:
        try:
            time_limit = int(sys.argv[3])
        except ValueError:
            print("Invalid time limit provided. Using default (300s).")

    print(f"Running with:\n Input: {input_file}\n Output: {output_file}\n Time Limit: {time_limit}s")

    try:
        input_data = load_input(input_file)
        result = solve_relaxed_ilp(input_data, time_limit)

        if result:
            save_output(result, output_file)
            print(f"Output saved to {output_file}")
    except FileNotFoundError:
        print(f"Error: Input file '{input_file}' not found.")
    except Exception as e:
        print(f"An error occurred: {e}")