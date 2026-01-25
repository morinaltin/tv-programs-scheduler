# Mathematical Formulation - TV Scheduling Problem (CP-SAT)

This document provides a rigorous mathematical formulation of the TV program scheduling problem as implemented using the **Google OR-Tools CP-SAT** solver. This implementation shifts from traditional Mixed-Integer Linear Programming (MILP) to Constraint Programming (CP) to better handle sequencing and flexible interval logic.

---

## 1. Problem Notation

### Sets and Indices
- $P$: Set of candidate programs (after initial temporal and priority filtering), indexed by $i, j \in \{0, \dots, n-1\}$.
- $G$: Set of genres.
- $B$: Set of time preference bonus windows.

### Constants and Parameters
- $O, E$: Global viewing window bounds (Opening and Closing minutes).
- $D$: Minimum required duration for any program to be considered "valid".
- $R$: Maximum allowed consecutive programs of the same genre.
- $S_{pen}$: Fixed penalty for each channel switch.
- $T_{pen}$: Fixed penalty for terminating a program early (either start late or end early).
- For each program $i \in P$:
  - $orig\_start_i, orig\_end_i$: The original broadcast times.
  - $w\_start_i, w\_end_i$: The feasible time window $[w\_start, w\_end]$ considering global bounds and priority blocks.
  - $score_i$: Base interest score.
  - $genre_i$: Categorical genre.
  - $channel_i$: Source channel ID.
  - $min\_d_i$: Minimum duration $\max(D, \text{portion of program in window if shorter than } D)$. *Wait, code says `min(D, orig_dur)`. Correction: `min(D, orig_dur)`.*

---

## 2. Decision Variables

### Timing and Selection
For each program $i \in P$:
- $x_i \in \{0, 1\}$: Presence variable ($1$ if scheduled).
- $s_i \in [w\_start_i, w\_end\_i]$: Scheduled start time.
- $e_i \in [w\_start\_i, w\_end\_i]$: Scheduled end time.
- $d_i \in [0, w\_end\_i - w\_start\_i]$: Scheduled duration.
- $Interval_i$: An optional interval variable defined by $(s_i, d_i, e_i, x_i)$.

### Sequencing (The Circuit Model)
A **Circuit constraint** is applied over a graph where nodes are programs $\{0, \dots, n-1\}$, plus a special "Source/Sink" node $n$.
- $a_{i,j} \in \{0, 1\}$: Binary arc variable. $1$ if program $j$ immediately follows $i$.
- $a_{start, i} \in \{0, 1\}$: Program $i$ is the first in the schedule.
- $a_{i, end} \in \{0, 1\}$: Program $i$ is the last in the schedule.
- $a_{i, i} \in \{0, 1\}$: Self-loop variable. $a_{i, i} = 1 \iff x_i = 0$ (program not selected).

### Penalty and Bonus Tracking
- $late\_s_i \in \{0,1\}$: Indicates if $s_i > orig\_start_i$.
- $early\_e_i \in \{0,1\}$: Indicates if $e_i < orig\_end_i$.
- $gr_i \in \{1, \dots, R\}$: The "run position" of program $i$ within a same-genre sequence.
- $bonus_{i,b} \in \{0,1\}$: Indicates if program $i$ earns bonus from preference window $b$.

---

## 3. Detailed Constraints

### 3.1 Timing and Non-Overlap
For all $i \in P$:
- **Duration Equality**: $d_i = e_i - s_i$.
- **Optional Presence**: 
  - If $x_i = 1 \implies d_i \ge min\_d_i$.
  - If $x_i = 0 \implies d_i = 0, s_i = e_i$.
- **Non-Overlap Condition**:
  $$\text{NoOverlap}(\{Interval_i \mid i \in P\})$$
  *Mathematically: For any $i, j$ where $x_i = 1$ and $x_j = 1$, the intervals $[s_i, e_i)$ and $[s_j, e_j)$ must be disjoint.*

### 3.2 Circuit and Sequencing
The arcs $a_{i,j}$ must form a single Hamiltonian circuit that includes the "Source/Sink" node and all selected programs.
- **Circuit Linkage**: $\text{Circuit}(\{a_{i,j}\})$.
- **Temporal Order**: If $a_{i,j} = 1$ and $i, j \neq n$, then $e_i \le s_j$. This prevents "backward" sequences in time.
- **Presence Consistency**: If $a_{i,j} = 1$ and $i \neq j$, then $x_i = 1$ and $x_j = 1$.

### 3.3 Trimming (Termination) Logic
This logic penalizes "shrinking" a program to fit the timeline.
- **Start Trimming**:
  - $late\_s_i \implies s_i > orig\_start_i$
  - $(x_i = 1 \land \neg late\_s_i) \implies s_i = orig\_start_i$
- **End Trimming**:
  - $early\_early_e_i \implies e_i < orig\_end_i$
  - $(x_i = 1 \land \neg early\_e_i) \implies e_i = orig\_end_i$
- **Implication**: $late\_s_i \implies x_i = 1$ and $early\_e_i \implies x_i = 1$.

### 3.4 Genre Diversity
Tracking is done via the $gr_i$ variables:
1. **Inheritance**: If $genre_i = genre_j$ and $a_{i,j} = 1 \implies gr_j = gr_i + 1$.
2. **Reset**: If $genre_i \neq genre_j$ and $a_{i,j} = 1 \implies gr_j = 1$.
3. **Start Reset**: If $a_{start, i} = 1 \implies gr_i = 1$.
4. **Limit**: $gr_i \le R$ for all selected programs.

---

## 4. Objective Function

Maximize the total schedule utility $Z$:

$$Z = \sum_{i \in P} (score_i \cdot x_i) + \sum_{i \in P} \sum_{b \in B} (bonus\_val_b \cdot bonus_{i,b}) - \text{Total Penalties}$$

Where **Total Penalties** is:
$$\text{Total Penalties} = S_{pen} \cdot \sum_{\substack{a_{i,j} = 1 \\ chan_i \neq chan_j \\ i,j \neq n}} 1 + T_{pen} \cdot \sum_{i \in P} (late\_s_i + early\_e_i)$$

---

## 5. Trimming Example Calculation

Consider a scenario with **Termination Penalty $T_{pen} = 50$**.

**Program A**: `orig_start: 100`, `orig_end: 200`, `score: 500`.
**Program B**: `orig_start: 180`, `orig_end: 250`, `score: 400`.

There is a 20-minute overlap. The solver has three main strategies:

1. **Keep A, discard B**:
   - Score: 500. Penalties: 100 (for omitting B). Total: 400.
2. **Shorten A (End Early)**:
   - Program A runs [100, 180]. Program B runs [180, 250].
   - $early\_e_A = 1$ (A ends at 180 instead of 200). $late\_s_B = 0$.
   - Scores: 500 + 400 = 900.
   - Penalty: $T_{pen} = 50$.
   - **Total: 850**.
3. **Shorten B (Start Late)**:
   - Program A runs [100, 200]. Program B runs [200, 250].
   - $late\_s_B = 1$ (B starts at 200 instead of 180).
   - Scores: 900. Penalty: 50. Total: 850.

The solver will choose strategy 2 or 3 because 850 > 400.

---

## 6. Theoretical Background: CP vs. MILP

### Why CP-SAT?
Standard ILP (using PuLP/Cbc) models non-overlap by creating $O(n^2)$ pairwise constraints: $x_i + x_j \le 1$. In our project, where start/end times are variables, this becomes even harder to model linearly.

**OR-Tools CP-SAT provides:**
1. **Global Constraints**: `NoOverlap` uses a dedicated algorithm (Interval Algebra) which is much faster than individual linear inequalities.
2. **Circuit Propagator**: The `Circuit` constraint handles sequencing and sub-tour elimination (ensuring you don't have three programs trapped in a loop in the middle of the day) automatically.
3. **Lazy Clauses**: Instead of checking every combination, the solver "learns" during search which constraints are being violated and adds them on the fly.

### Complexity
- **Variables**: $4n$ for timing/selection + $n^2$ for sequencing arcs.
- **Constraints**: $O(n)$ for timing + $O(n^2)$ for transitions.
- **Pruning**: CP-SAT uses "Domain Reduction" to quickly eliminate times that are physically impossible (e.g., a program with 30 min duration starting 10 minutes before the end of the day).

---

## 7. RILP Variation (Relaxed)

The **RILP** (Relaxed model) is a subset of the above. It removes:
- The `Circuit` transitions logic (all $a_{i,j}$ involving channel changes).
- The `gr_i` genre run tracking.
- The `late_s` and `early_e` penalty variables.

This reduces the problem to a **Weighted Optional Interval Scheduling Problem**, which provides an "Ideal Score" benchmark. Using this "Ideal Score", we can measure exactly how much "viewer happiness" we sacrificed to enforce diversity and reduce channel switching.
