import json
import sys
from collections import defaultdict


def load_input(filepath):
    with open(filepath, 'r') as f:
        return json.load(f)


def solve_tv_scheduling_dp(input_data):
    # --- 1. Extract Constants ---
    O = input_data['opening_time']
    E = input_data['closing_time']
    D = input_data['min_duration']
    R = input_data['max_consecutive_genre']
    S_pen = input_data['switch_penalty']

    priority_blocks = input_data.get('priority_blocks', [])
    time_prefs = input_data.get('time_preferences', [])
    channels = input_data['channels']

    # --- 2. Pre-process and Filter Programs ---
    # We flatten the structure to a simple list of valid program objects
    valid_programs = []

    for channel in channels:
        chan_id = channel['channel_id']
        for prog in channel['programs']:
            p_start = prog['start']
            p_end = prog['end']
            duration = p_end - p_start

            # Constraint: Time Window (Must fall strictly between O and E)
            if p_start < O or p_end > E:
                continue

            # Constraint: Minimum Duration
            if duration < D:
                continue

            # Constraint: Priority Blocks
            # If program overlaps with a block, the channel MUST be in allowed_channels
            is_blocked = False
            for block in priority_blocks:
                # Check for overlap: Start before block ends AND End after block starts
                if p_start < block['end'] and p_end > block['start']:
                    if chan_id not in block['allowed_channels']:
                        is_blocked = True
                        break
            if is_blocked:
                continue

            # Calculate Static Score (Base + Bonus)
            # Bonus applies if program genre matches AND overlap >= D
            current_score = prog['score']
            for pref in time_prefs:
                if prog['genre'] == pref['preferred_genre']:
                    overlap_start = max(p_start, pref['start'])
                    overlap_end = min(p_end, pref['end'])
                    if (overlap_end - overlap_start) >= D:
                        current_score += pref['bonus']

            valid_programs.append({
                'id': prog['program_id'],
                'channel': chan_id,
                'start': p_start,
                'end': p_end,
                'genre': prog['genre'],
                'total_val': current_score
            })

    # Sort by End Time, then Start Time. This is crucial for DP.
    valid_programs.sort(key=lambda x: (x['end'], x['start']))

    n = len(valid_programs)
    print(f"Processing {n} valid programs...")

    # --- 3. Dynamic Programming ---
    # dp[i][k] = Max score ending at program i, where i is the k-th consecutive program of its genre
    # k ranges from 1 to R
    dp = defaultdict(lambda: -float('inf'))

    # parent[i][k] = (previous_program_index, previous_k_count)
    # Used to reconstruct the schedule
    parent = {}

    # Initialize DP for all programs as potential starters
    for i in range(n):
        # Being the first program in a sequence (k=1)
        # Score is just the program's value (no switch penalty for the very first one)
        dp[(i, 1)] = valid_programs[i]['total_val']
        parent[(i, 1)] = None

    # Iterate through every program i
    for i in range(n):
        prog_i = valid_programs[i]

        # Iterate through every possible previous program j
        # Optimization: Since we sorted by end time, we only look at j < i
        for j in range(i):
            prog_j = valid_programs[j]

            # Constraint: No Overlap
            if prog_j['end'] > prog_i['start']:
                continue

            # Calculate Switch Penalty cost
            switch_cost = S_pen if prog_j['channel'] != prog_i['channel'] else 0

            # Case A: Genres are different
            if prog_j['genre'] != prog_i['genre']:
                # We can transition from j (at any k count) to i (resetting k to 1)
                # We want the max score among all k states of j
                for k_prev in range(1, R + 1):
                    if dp[(j, k_prev)] == -float('inf'): continue

                    new_score = dp[(j, k_prev)] - switch_cost + prog_i['total_val']

                    if new_score > dp[(i, 1)]:
                        dp[(i, 1)] = new_score
                        parent[(i, 1)] = (j, k_prev)

            # Case B: Genres are the same
            else:
                # We can transition from j (at k-1) to i (at k)
                # We can only extend if k < R
                for k_curr in range(2, R + 1):
                    k_prev = k_curr - 1
                    if dp[(j, k_prev)] == -float('inf'): continue

                    new_score = dp[(j, k_prev)] - switch_cost + prog_i['total_val']

                    if new_score > dp[(i, k_curr)]:
                        dp[(i, k_curr)] = new_score
                        parent[(i, k_curr)] = (j, k_prev)

    # --- 4. Reconstruct Path ---
    # Find the state (i, k) with the global maximum score
    best_state = None
    max_score = -float('inf')

    for state, score in dp.items():
        if score > max_score:
            max_score = score
            best_state = state

    if best_state is None:
        print("No valid schedule found.")
        return {'scheduled_programs': []}

    # Backtrack
    schedule_indices = []
    curr = best_state
    while curr is not None:
        idx, k = curr
        schedule_indices.append(idx)
        curr = parent.get(curr)

    # Reverse to get chronological order
    schedule_indices.reverse()

    # Format Output
    output_schedule = []
    final_score_check = 0

    print("\nOptimal Schedule Found:")
    for idx in schedule_indices:
        p = valid_programs[idx]
        output_schedule.append({
            "program_id": p['id'],
            "channel_id": p['channel'],
            "start": p['start'],
            "end": p['end']
        })
        print(f"[{p['start']}-{p['end']}] Ch{p['channel']} ({p['genre']}): {p['id']} (Val: {p['total_val']})")

    print(f"\nCalculated Max Score: {max_score}")
    return {"scheduled_programs": output_schedule}


def save_output(output_data, filepath):
    with open(filepath, 'w') as f:
        json.dump(output_data, f, indent=4)


if __name__ == "__main__":
    # File paths
    input_file = "inputs/1ipko_schedule_2025-10-221.json"
    output_file = "outputs/dp_1ipko_schedule_2025-10-221.json"

    # Run
    print(f"Loading {input_file}...")
    data = load_input(input_file)
    solution = solve_tv_scheduling_dp(data)
    save_output(solution, output_file)
    print(f"Solution saved to {output_file}")