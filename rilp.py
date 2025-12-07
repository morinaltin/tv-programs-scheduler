"""
Relaxed TV Channel Scheduling Optimization
- Ignores channel switch penalties
- Ignores genre diversity constraints
- Maximizes base score + bonuses only
- Only enforces: no overlaps, min duration, time boundaries, priority blocks
"""

import json
import sys
from pulp import *


def load_input(filepath):
    with open(filepath, 'r') as f:
        return json.load(f)


def solve_relaxed_ilp(input_data, time_limit=300):
    """
    Solve TV scheduling with relaxed constraints using optimized ILP.

    Optimizations:
    - Always uses partial scheduling
    - Efficient overlap detection (O(n log n) instead of O(n²))
    - Uses Gurobi if available, otherwise CBC
    """
    print("=" * 60)
    print("OPTIMIZED RELAXED ILP SOLVER")
    print("Ignoring: Switch Penalties, Genre Diversity")
    print("Partial Scheduling: ALWAYS ENABLED")
    print("=" * 60)

    # Extract parameters
    O = input_data['opening_time']
    E = input_data['closing_time']
    D = input_data['min_duration']
    priority_blocks = input_data.get('priority_blocks', [])
    time_prefs = input_data.get('time_preferences', [])
    channels = input_data['channels']

    print(f"\nVenue Hours: {O} - {E} (min duration: {D})")
    print(f"Priority Blocks: {len(priority_blocks)}")
    print(f"Time Preferences: {len(time_prefs)}")

    # Build program candidates
    programs = []

    print("\nBuilding program segments...")
    for channel in channels:
        chan_id = channel['channel_id']

        for prog in channel['programs']:
            p_start = prog['start']
            p_end = prog['end']
            duration = p_end - p_start

            # Skip if completely outside venue hours
            if p_start >= E or p_end <= O:
                continue

            # Clip to venue hours
            p_start = max(p_start, O)
            p_end = min(p_end, E)
            duration = p_end - p_start

            # Check minimum duration
            if duration < D:
                continue

            # Handle priority blocks - always use partial scheduling
            segments = split_program_by_priority_blocks(
                p_start, p_end, chan_id, priority_blocks
            )

            for seg_start, seg_end in segments:
                seg_duration = seg_end - seg_start
                if seg_duration >= D:
                    score = calculate_program_score(
                        prog, seg_start, seg_end, time_prefs, D
                    )
                    programs.append({
                        'id': prog['program_id'],
                        'channel': chan_id,
                        'start': seg_start,
                        'end': seg_end,
                        'genre': prog['genre'],
                        'score': score,
                        'duration': seg_duration
                    })

    n = len(programs)
    print(f"\nProgram candidates: {n}")

    if n == 0:
        print("No valid programs found!")
        return None

    # Create ILP model
    prob = LpProblem("Relaxed_TV_Schedule", LpMaximize)

    # Decision variables
    x = LpVariable.dicts("x", range(n), cat='Binary')

    # Objective: maximize total score
    prob += lpSum(programs[i]['score'] * x[i] for i in range(n)), "Total_Score"

    # Constraint: no overlapping programs
    # OPTIMIZATION: Use clique-based formulation instead of pairwise
    # Group programs into time buckets for efficient overlap detection
    print("Building efficient overlap constraints...")

    # Sort programs by start time
    sorted_programs = sorted(enumerate(programs), key=lambda x: x[1]['start'])

    # For each program, only check overlaps with nearby programs
    overlaps = set()
    for idx, (i, pi) in enumerate(sorted_programs):
        # Only check programs that start before this one ends
        for j_idx in range(idx + 1, n):
            j, pj = sorted_programs[j_idx]

            # If program j starts after program i ends, no more overlaps possible
            if pj['start'] >= pi['end']:
                break

            # They overlap
            if pi['start'] < pj['end'] and pj['start'] < pi['end']:
                overlaps.add((min(i, j), max(i, j)))

    print(f"Overlap constraints: {len(overlaps)}")

    # Add constraints in batches for better performance
    for i, j in overlaps:
        prob += x[i] + x[j] <= 1, f"no_overlap_{i}_{j}"

    # Solve with appropriate solver
    print(f"\nSolving (time limit: {time_limit}s)...")

    # Just use CBC - it's the most reliable
    solver = PULP_CBC_CMD(msg=1, timeLimit=time_limit)
    print("Using CBC solver")

    prob.solve(solver)

    status = LpStatus[prob.status]
    print(f"Status: {status}")

    if status not in ['Optimal', 'Not Solved']:
        print("No feasible solution found")
        return None

    # Extract solution
    selected = [i for i in range(n) if value(x[i]) > 0.5]
    selected.sort(key=lambda i: programs[i]['start'])

    # Build output
    schedule = []
    total_score = 0

    print("\n" + "=" * 60)
    print("SCHEDULE")
    print("=" * 60)

    for idx, i in enumerate(selected):
        p = programs[i]
        total_score += p['score']

        schedule.append({
            "program_id": p['id'],
            "channel_id": p['channel'],
            "start": p['start'],
            "end": p['end']
        })

        print(f"{idx+1:2}. [{format_time(p['start'])} - {format_time(p['end'])}] "
              f"{p['id']:20} Ch{p['channel']} {p['genre']:15} +{p['score']:4}")

    print("=" * 60)
    print(f"TOTAL SCORE: {total_score}")
    print(f"Programs: {len(selected)}")
    print("=" * 60)

    return {
        "scheduled_programs": schedule,
        "total_score": total_score
    }


def split_program_by_priority_blocks(start, end, channel_id, priority_blocks):
    """
    Split a program's time window into valid segments that don't violate priority blocks.
    Returns list of (start, end) tuples.
    """
    # Find all priority blocks that affect this program and channel
    blocking_intervals = []

    for block in priority_blocks:
        # If this channel is NOT allowed during this block
        if channel_id not in block['allowed_channels']:
            # And the block overlaps with the program
            if start < block['end'] and end > block['start']:
                block_start = max(start, block['start'])
                block_end = min(end, block['end'])
                blocking_intervals.append((block_start, block_end))

    if not blocking_intervals:
        return [(start, end)]

    # Sort blocking intervals
    blocking_intervals.sort()

    # Merge overlapping blocking intervals
    merged = [blocking_intervals[0]]
    for curr_start, curr_end in blocking_intervals[1:]:
        last_start, last_end = merged[-1]
        if curr_start <= last_end:
            merged[-1] = (last_start, max(last_end, curr_end))
        else:
            merged.append((curr_start, curr_end))

    # Build valid segments between blocks
    segments = []
    current = start

    for block_start, block_end in merged:
        if current < block_start:
            segments.append((current, block_start))
        current = max(current, block_end)

    if current < end:
        segments.append((current, end))

    return segments


def calculate_program_score(prog, start, end, time_prefs, min_duration):
    """Calculate base score + bonuses for a program segment."""
    score = prog['score']

    # Add time preference bonuses
    for pref in time_prefs:
        if prog['genre'] == pref['preferred_genre']:
            overlap_start = max(start, pref['start'])
            overlap_end = min(end, pref['end'])
            overlap_duration = overlap_end - overlap_start

            if overlap_duration >= min_duration:
                score += pref['bonus']

    return score


def format_time(minutes):
    """Format minutes as HH:MM"""
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours:02d}:{mins:02d}"


def save_output(output_data, filepath):
    """Save schedule to JSON file"""
    data_to_save = {"scheduled_programs": output_data["scheduled_programs"]}
    with open(filepath, 'w') as f:
        json.dump(data_to_save, f, indent=4)


if __name__ == "__main__":
    # Parse arguments - all required from command line
    if len(sys.argv) < 3:
        print("Usage: python script.py <input_file> <output_file> [time_limit] [allow_partial]")
        print("  input_file: Path to input JSON file (required)")
        print("  output_file: Path to output JSON file (required)")
        print("  time_limit: Solver time limit in seconds (default: 300)")
        print("  allow_partial: Enable partial program scheduling (default: false)")
        print("\nExample: python script.py input.json output.json 300 true")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]

    time_limit = 300
    if len(sys.argv) > 3:
        try:
            time_limit = int(sys.argv[3])
        except ValueError:
            print(f"Warning: Invalid time limit '{sys.argv[3]}'. Using default (300s).")

    allow_partial = True  # Always enabled

    print(f"\nInput:  {input_file}")
    print(f"Output: {output_file}")
    print(f"Time Limit: {time_limit}s\n")

    try:
        input_data = load_input(input_file)
        result = solve_relaxed_ilp(input_data, time_limit)

        if result:
            save_output(result, output_file)
            print(f"\nSolution saved to {output_file}")
        else:
            print("\nNo solution found")

    except FileNotFoundError:
        print(f"Error: File '{input_file}' not found")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()