# TV Channel Scheduling Optimization

This project implements two optimization approaches to solve the **TV Channel Scheduling Problem** using **Google OR-Tools CP-SAT**. The goal is to create an optimal viewing schedule from multiple TV channels that maximizes viewer satisfaction while respecting various content and temporal constraints.

## Problem Description

### Objective
Select and timing programs from multiple TV channels to create a single optimal viewing schedule that:
- **Maximizes total score** (viewer interest/ratings).
- **Incentivizes time preferences** (bonuses for specific genres at specific times).
- **Minimizes channel switching** (ILP only).
- **Ensures content diversity** (ILP only).
- **Respects priority blocks** (exclusive channel access during certain hours).

### Constraints
1. **No Overlap**: Only one program can be scheduled at any time point.
2. **Genre Diversity**: No more than $R$ consecutive programs of the same genre.
3. **Minimum Duration**: Every scheduled program must meet a minimum duration $D$.
4. **Flexible Trimming**: Programs can be trimmed to fit the schedule, but trimming is penalized (ILP only).
5. **Priority Blocks**: During specified times, only programs from "allowed channels" can be scheduled.
6. **Time Window**: All programs must fit within the global opening and closing times.

---

## Implementation Workflow

The system follows a 4-step process to generate the schedule:

### Step 1: Pre-Filtering
Before the solver starts, we "prune" the search space:
- Filter out programs that fall entirely outside the $[O, E]$ window.
- Calculate **Priority Block** conflicts: If a program is on an unauthorized channel during a priority block, we split it into valid segments or discard it if it's too short.
- Define a "Candidate List" of programs that meet the `min_duration` threshold.

### Step 2: Model Construction
The system translates the JSON input into a **Constraint Programming** model:
- **Intervals**: For each candidate, we create an `OptionalIntervalVar`.
- **Transitions**: We build a directed graph of all possible program-to-program transitions.
- **Constraints**: Apply the `Circuit`, `NoOverlap`, and `Genre Diversity` constraints using boolean logic.

### Step 3: Optimization
The CP-SAT solver performs a depth-first search augmented by:
- **Hints**: If a previous solution (`hint_file`) is provided, the solver uses it as a starting point.
- **Symmetry Breaking**: Pruning equivalent search paths to speed up convergence.
- **Time Limits**: The solver runs until it finds the optimal solution or hits the user-defined `--time` limit.

### Step 4: Post-Processing
- The solver results are sorted chronologically.
- A **Solution Summary** is generated, calculating the final breakdown of base scores, bonuses, and penalties.
- The results are saved to a JSON file and printed to the console for review.

---

## Technical Details

### Optimization Engine
Both scripts utilize **Google OR-Tools CP-SAT**, a state-of-the-art constraint programming solver. Unlike traditional Mixed-Integer Linear Programing (MILP) which uses Simplex and Branch-and-Bound, CP-SAT uses **Satisfiability (SAT)** techniques to quickly find feasible solutions and then optimizes them.

### Flexible Scheduling Logic
Our implementation allows programs to "shrink" (trimming). This is mathematically represented as:
$$Start_{scheduled} \in [Start_{orig}, End_{orig} - MinDuration]$$
$$End_{scheduled} \in [Start_{orig} + MinDuration, End_{orig}]$$
This ensures that the solver can "squeeze" in an important program by trimming 5 minutes from a less important neighbor.

---

## Comparison Summary

| Aspect | ILP (`ilp.py`) | RILP (`rilp.py`) |
|--------|----------------|------------------|
| **Primary Goal** | Realistic/Diverse Schedule | Maximum Achievable Score |
| **Solver** | OR-Tools CP-SAT | OR-Tools CP-SAT |
| **Channel Switches** | Penalized | Free |
| **Genre Diversity** | Enforced ($R$) | Ignored |
| **Program Trimming** | Penalized | Free |
| **Execution Time** | Slower (Complex Logic) | Fast (Simpler Logic) |

---

## Future Improvements
1. **Gap Filling**: Incentivize the solver to leave zero empty space between programs.
2. **Rolling Window Optimization**: For extremely large datasets (24h+), solve the morning and afternoon as separate but linked problems to reduce complexity.
3. **Multi-Viewer Support**: Generate $N$ parallel schedules for different target audiences simultaneously.

---

## Installation

### Requirements
- Python 3.9+
- Google OR-Tools

### Setup
```bash
pip install ortools
```

---

## Authors
Developed as part of the TV Programs Scheduler project for university research.
