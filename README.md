# TV Channel Scheduling Optimization

This project implements two approaches to solve the **TV Channel Scheduling Problem** using Integer Linear Programming (ILP). The goal is to create an optimal viewing schedule from multiple TV channels that maximizes viewer satisfaction while respecting various constraints.

## Problem Description

### Objective
Select programs from multiple TV channels to create a single optimal viewing schedule that:
- Maximizes total score (viewer interest/ratings)
- Respects time and content constraints
- Minimizes channel switching (ILP only)
- Ensures content diversity (ILP only)

### Input Parameters

| Parameter | Description |
|-----------|-------------|
| `opening_time` | Start time of the viewing window (in minutes from midnight) |
| `closing_time` | End time of the viewing window (in minutes from midnight) |
| `min_duration` | Minimum program duration to consider (in minutes) |
| `max_consecutive_genre` | Maximum consecutive programs of the same genre (e.g., no more than 2 comedies in a row) |
| `channels_count` | Number of available TV channels |
| `switch_penalty` | Score penalty for each channel switch |
| `termination_penalty` | Penalty for early termination (unused) |
| `time_preferences` | Time windows where certain genres receive bonus points |
| `priority_blocks` | Time periods where only specific channels are allowed |
| `channels` | List of channels with their programs |

### Constraints

1. **No Overlap**: Only one program can be scheduled at any time
2. **Genre Diversity**: No more than R consecutive programs of the same genre
3. **Minimum Duration**: Only programs meeting minimum duration are considered
4. **Priority Blocks**: During specified times, only certain channels are allowed
5. **Time Window**: All programs must fit within opening and closing times
6. **Sequential**: Programs must be scheduled sequentially without gaps (implicit in ILP)

### Scoring

```
Total Score = Base Scores + Time Preference Bonuses - Channel Switch Penalties
```

- **Base Score**: Sum of individual program scores
- **Bonuses**: Extra points for matching genre-time preferences
- **Penalties**: Deduction for each channel switch (encourages watching one channel)

---

## Implementation Approaches

### 1. ILP (Integer Linear Programming) - `ilp.py`

**Full exact solution** using comprehensive mathematical modeling.

#### Decision Variables

1. **`x[i]`** (Binary): 1 if program i is selected
2. **`seq[i,j]`** (Binary): 1 if program j immediately follows program i
3. **`is_first[i]`** (Binary): 1 if program i starts the schedule
4. **`is_last[i]`** (Binary): 1 if program i ends the schedule
5. **`genre_run[i,k]`** (Binary): 1 if program i is the k-th consecutive program of its genre

#### Key Features

- **Exact sequencing**: Models the exact order of programs using flow constraints
- **Rigorous genre tracking**: Tracks run position (1st, 2nd, ..., Rth) for each program
- **Conflict graph**: Explicitly models which programs overlap and cannot both be selected
- **Flow conservation**: Ensures selected programs form a valid chain

#### Constraints

```python
# No overlapping programs
x[i] + x[j] <= 1  (for all overlapping pairs i, j)

# Flow conservation (programs form a chain)
outgoing_flow[i] + is_last[i] = x[i]
incoming_flow[i] + is_first[i] = x[i]

# Genre run tracking
genre_run[i, k+1] >= seq[j,i] + genre_run[j, k] - 1  (if same genre)
genre_run[i, 1] >= is_first[i] OR seq[j,i] (if different genre)

# Prevent exceeding max consecutive genre
seq[i,j] + genre_run[i, R] <= 1  (if same genre)
```

#### Usage

```bash
python ilp.py input.json output.json 300
```

Arguments:
1. Input JSON file
2. Output JSON file
3. Time limit in seconds (default: 300)

#### When to Use

- Small to medium instances (< 500 programs after filtering)
- When exact optimal solution is needed
- When you have sufficient computation time
- For validation and benchmarking

---

### 2. RILP (Relaxed ILP) - `rilp.py`

**Highly relaxed solution** that maximizes program scores without channel switch penalties or genre diversity constraints.

#### Key Simplifications

1. **No Channel Switch Penalties**: The objective function only maximizes program scores
   - Ignores `switch_penalty` parameter entirely
   - Programs can be selected from any channels without cost
   - Focus is purely on maximizing base scores + time preference bonuses

2. **No Genre Constraints**: Consecutive programs of the same genre are allowed
   - Ignores `max_consecutive_genre` parameter
   - Any number of consecutive programs with the same genre can be scheduled
   - Removes all genre diversity tracking logic

3. **Efficient Time-Based Constraints**: Uses discretized time buckets
   - Maps each minute to programs covering that time
   - Adds constraint: at most 1 program active at any given minute
   - Ensures no overlapping programs in the schedule

4. **No Sequencing Variables**: Doesn't model exact program order during optimization
   - Solution is sorted by start time after selection
   - No flow conservation or ordering constraints needed

#### Usage

```bash
python rilp.py input.json output.json 300
```

#### When to Use

- When you want to maximize raw program scores without penalties
- Testing upper bound of achievable score (ignoring switches and genre diversity)
- Large instances where you need fast solutions
- Scenarios where channel switching and genre diversity are not important

---

## Comparison

| Aspect | ILP | RILP |
|--------|-----|------|
| **Optimality** | Exact optimal (within time limit) | Optimal for relaxed problem |
| **Scalability** | Struggles with > 500 programs | Handles 1000+ programs |
| **Constraints** | O(n²) overlap constraints | O(T) time-based constraints |
| **Variables** | Many (sequencing, genre runs) | Minimal (selection only) |
| **Genre Constraint** | Exact with run tracking | None (ignored) |
| **Channel Switches** | Penalized in objective | None (ignored) |
| **Runtime** | Longer, may timeout | Very fast |
| **Solution Quality** | Respects all constraints | Ignores switch/genre constraints |

\* If solver completes within time limit

---

## Example Workflow

### 1. Prepare Input

```json
{
  "opening_time": 0,
  "closing_time": 1438,
  "min_duration": 30,
  "max_consecutive_genre": 2,
  "channels_count": 172,
  "switch_penalty": 5,
  "time_preferences": [
    {
      "start": 0,
      "end": 60,
      "preferred_genre": "animacion",
      "bonus": 100
    }
  ],
  "priority_blocks": [
    {
      "start": 1200,
      "end": 1260,
      "allowed_channels": [0, 1, 2]
    }
  ],
  "channels": [
    {
      "channel_id": 0,
      "programs": [
        {
          "program_id": "prog_001",
          "start": 30,
          "end": 90,
          "genre": "dramë",
          "score": 85
        }
      ]
    }
  ]
}
```

### 2. Run Solver

```bash
# For small/medium instances
python ilp.py inputs/sample.json outputs/ilp_result.json 300

# For large instances
python rilp.py inputs/large_sample.json outputs/rilp_result.json 300
```

### 3. Output Format

```json
{
  "scheduled_programs": [
    {
      "program_id": "prog_001",
      "channel_id": 0,
      "start": 30,
      "end": 90
    },
    {
      "program_id": "prog_042",
      "channel_id": 2,
      "start": 90,
      "end": 150
    }
  ]
}
```

### 4. Solution Summary

Both solvers print summaries:

**ILP Output:**
```
Programs after filtering: 245
Solving ILP (time limit: 300s)...
Variables: 12450, Constraints: 8932
Status: Optimal

============================================================
SOLUTION SUMMARY
============================================================
Base score: 8450
Bonus score: 1200
Channel switches: 12 (penalty: -60)
Genre constraint: OK (max 2 consecutive)

TOTAL SCORE: 9590

Schedule (34 programs):
   1. prog_001         Ch0 00:30-01:30 dramë           score=85
   2. prog_042         Ch2 01:30-02:45 humoristik      score=92
   ...
```

**RILP Output:**
```
Programs considered: 245
Constraints added: 1438
Solver Status: Optimal

--- RELAXED SCHEDULE (No Penalties) ---
[30-90] prog_001 (Score: 85)
[90-150] prog_042 (Score: 192)
...
========================================
CALCULATED RELAXED SCORE: 9850
========================================
```

Note: RILP scores are typically higher since they ignore switch penalties and genre restrictions.

---

## Installation

### Requirements

- Python 3.7+
- PuLP library

### Install Dependencies

```bash
pip install pulp
```

---

## Algorithm Details

### ILP Genre Constraint (Key Innovation)

The genre diversity constraint is the most complex part. Here's how it works:

```
For each program i:
  - Assign exactly one "run position" k ∈ {1, 2, ..., R}
  - If i is first OR preceded by different genre: k = 1
  - If i follows program j of same genre at position k: i gets position k+1
  - If i is at position R, it cannot be followed by same genre
```

This ensures no more than R consecutive programs of the same genre.

### RILP Time-Based Overlap Constraints

```python
time_map = {}
for each program i:
    for each minute t in [start, end):
        time_map[t].append(i)

for each minute t with multiple programs:
    add constraint: sum(x[i] for i in time_map[t]) <= 1
```

This ensures no two programs overlap, using O(T) constraints where T is the number of distinct minutes with overlaps.

---

### Performance Tips

1. **Filter Early**: The solvers filter out invalid programs before building the ILP model
2. **Time Limits**: Set realistic time limits; ILP often finds good solutions early
3. **Choose Wisely**: 
   - Use **ILP** when you need to respect all constraints (channel switches, genre diversity)
   - Use **RILP** when you want maximum score without penalties/restrictions, or as an upper bound
4. **Monitor Progress**: Both solvers print progress; interrupt if taking too long
5. **Preprocessing**: Consider removing very low-score programs to reduce problem size

---

## Theoretical Background

### Complexity

The TV scheduling problem is **NP-hard** (related to interval scheduling and bin packing).

- **Without genre constraint**: Solvable in polynomial time (weighted interval scheduling)
- **With genre constraint**: NP-hard (requires ILP or heuristics)

### ILP Advantages

- Proven optimal solutions
- Handles complex constraints naturally
- Well-studied with mature solvers (CBC, Gurobi, CPLEX)

### ILP Limitations

- Exponential worst-case complexity
- May timeout on large instances
- Memory intensive for many variables

---

## Future Improvements

1. **Column Generation**: For very large instances
2. **Constraint Programming**: Alternative exact approach
3. **Local Search**: Metaheuristics (simulated annealing, genetic algorithms)
4. **Machine Learning**: Learn good initial solutions
5. **Parallel Processing**: Solve multiple subproblems simultaneously
6. **Rolling Horizon**: Break problem into time windows

---

## Common Issues

### Solver Timeout
- Reduce time limit or use RILP
- Filter programs more aggressively
- Relax some constraints

### No Feasible Solution
- Check if constraints are too restrictive
- Verify input data (overlaps, time windows)
- Review genre distribution

### Poor Solution Quality
- Increase time limit
- Check score distribution (are some programs dominating?)
- Verify bonus/penalty values are appropriate

---

## References

- [PuLP Documentation](https://coin-or.github.io/pulp/)
- [Integer Linear Programming](https://en.wikipedia.org/wiki/Integer_programming)
- [Interval Scheduling](https://en.wikipedia.org/wiki/Interval_scheduling)
- [CBC Solver](https://github.com/coin-or/Cbc)

---

## License

This is a university project for educational purposes.

## Authors

Developed for Operations Research / Optimization course.
