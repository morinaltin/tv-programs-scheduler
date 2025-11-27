# Mathematical Formulation - TV Scheduling Problem

This document provides the complete mathematical formulations for both ILP and RILP approaches.

---

## Problem Notation

### Sets and Indices

- **P** = Set of all programs (after filtering), indexed by *i*, *j* ∈ {0, 1, ..., n-1}
- **G** = Set of all genres
- **CF(i)** = Set of programs that can follow program *i* (i.e., {j : start_j ≥ end_i})
- **CONFLICTS(i)** = Set of programs that overlap with program *i*

### Parameters

| Symbol | Description |
|--------|-------------|
| **O** | Opening time (minutes from midnight) |
| **E** | Closing time (minutes from midnight) |
| **D** | Minimum program duration (minutes) |
| **R** | Maximum consecutive programs of same genre |
| **S_pen** | Channel switch penalty |
| **T_pen** | Termination penalty (unused) |
| **n** | Number of programs after filtering |

### Program Attributes

For each program *i* ∈ P:

- **start_i** : Start time
- **end_i** : End time  
- **channel_i** : Channel ID
- **genre_i** : Genre (string/category)
- **score_i** : Base score (viewer interest)
- **id_i** : Program identifier

### Time Preferences

Set of tuples (t_start, t_end, genre, bonus):
- Programs of specified genre overlapping [t_start, t_end] by at least D minutes receive bonus points

### Priority Blocks

Set of tuples (t_start, t_end, allowed_channels):
- Only programs from allowed_channels can be scheduled in [t_start, t_end]

---

## ILP Formulation (Exact Model)

### Decision Variables

1. **x_i ∈ {0, 1}** : Selection variable
   - x_i = 1 if program i is selected in the schedule
   - x_i = 0 otherwise

2. **seq_{i,j} ∈ {0, 1}** : Sequencing variable (for all i, j where j ∈ CF(i))
   - seq_{i,j} = 1 if program j immediately follows program i
   - seq_{i,j} = 0 otherwise

3. **first_i ∈ {0, 1}** : First program indicator
   - first_i = 1 if program i is the first in the schedule
   - first_i = 0 otherwise

4. **last_i ∈ {0, 1}** : Last program indicator
   - last_i = 1 if program i is the last in the schedule
   - last_i = 0 otherwise

5. **run_{i,k} ∈ {0, 1}** : Genre run position (for k ∈ {1, 2, ..., R})
   - run_{i,k} = 1 if program i is the k-th consecutive program of its genre
   - run_{i,k} = 0 otherwise

### Objective Function

```
Maximize:  Z = Σ score_i · x_i + Σ bonus_i · x_i - S_pen · Σ seq_{i,j} · switch_{i,j}
              i∈P              i∈P                    (i,j)
```

Where:
- **bonus_i** = sum of all time preference bonuses applicable to program i
- **switch_{i,j}** = 1 if channel_i ≠ channel_j, 0 otherwise (constant)

Expanded:
```
Z = Σ (score_i + bonus_i) · x_i - S_pen · Σ seq_{i,j}
    i∈P                                    (i,j): channel_i ≠ channel_j
```

### Constraints

#### 1. No Overlapping Programs

For all pairs (i, j) where j ∈ CONFLICTS(i) and i < j:

```
x_i + x_j ≤ 1
```

*Ensures at most one program from each overlapping pair is selected.*

---

#### 2. Flow Conservation (Sequencing)

**2a. Outgoing Flow**

For each program i ∈ P:

```
Σ seq_{i,j} + last_i = x_i
j∈CF(i)
```

*If program i is selected, it either has a successor or is the last program.*

**2b. Incoming Flow**

For each program i ∈ P:

```
Σ seq_{j,i} + first_i = x_i
j: i∈CF(j)
```

*If program i is selected, it either has a predecessor or is the first program.*

**2c. At Most One First**

```
Σ first_i ≤ 1
i∈P
```

**2d. At Most One Last**

```
Σ last_i ≤ 1
i∈P
```

---

#### 3. Genre Diversity Constraints

**3a. Exactly One Run Position**

For each program i ∈ P:

```
Σ run_{i,k} = x_i
k=1 to R
```

*Every selected program has exactly one run position (1st, 2nd, ..., or R-th).*

**3b. Run Position Inheritance (Same Genre)**

For each program i ∈ P, each predecessor j where j ∈ {k : i ∈ CF(k) and genre_k = genre_i}, and each position k ∈ {1, ..., R-1}:

```
run_{i,k+1} ≥ seq_{j,i} + run_{j,k} - 1
```

*If program j at position k is followed by program i of the same genre, then i must be at position k+1.*

**3c. Run Position Reset (First or Different Genre)**

For each program i ∈ P:

```
run_{i,1} ≥ first_i
```

*If i is first, it's at position 1.*

For each program i and each predecessor j where genre_j ≠ genre_i:

```
run_{i,1} ≥ seq_{j,i}
```

*If preceded by different genre, position resets to 1.*

**3d. Prevent Exceeding Maximum Run**

For each program i ∈ P and each successor j ∈ CF(i) where genre_j = genre_i:

```
seq_{i,j} + run_{i,R} ≤ 1
```

*A program at position R cannot be followed by the same genre.*

---

### Complete ILP Model Summary

```
Maximize:
    Z = Σ (score_i + bonus_i) · x_i - S_pen · Σ seq_{i,j}
        i∈P                                    (i,j): channel_i ≠ channel_j

Subject to:
    x_i + x_j ≤ 1                                    ∀(i,j): j ∈ CONFLICTS(i), i < j
    
    Σ seq_{i,j} + last_i = x_i                       ∀i ∈ P
    j∈CF(i)
    
    Σ seq_{j,i} + first_i = x_i                      ∀i ∈ P
    j: i∈CF(j)
    
    Σ first_i ≤ 1
    i∈P
    
    Σ last_i ≤ 1
    i∈P
    
    Σ run_{i,k} = x_i                                ∀i ∈ P
    k=1 to R
    
    run_{i,k+1} ≥ seq_{j,i} + run_{j,k} - 1         ∀i, j: genre_i = genre_j, k ∈ {1,...,R-1}
    
    run_{i,1} ≥ first_i                              ∀i ∈ P
    
    run_{i,1} ≥ seq_{j,i}                            ∀i, j: genre_i ≠ genre_j
    
    seq_{i,j} + run_{i,R} ≤ 1                        ∀i, j ∈ CF(i): genre_i = genre_j
    
    x_i ∈ {0,1}                                      ∀i ∈ P
    seq_{i,j} ∈ {0,1}                                ∀i, j ∈ CF(i)
    first_i, last_i ∈ {0,1}                          ∀i ∈ P
    run_{i,k} ∈ {0,1}                                ∀i ∈ P, k ∈ {1,...,R}
```

---

## RILP Formulation (Relaxed/Simplified Model)

### Additional Notation

- **C** = Set of maximal cliques in the interval overlap graph
- **C_k** = k-th maximal clique (set of mutually overlapping programs)
- **G_g** = Set of programs with genre g, sorted by start time
- **W_{g,m}** = m-th window of R+1 consecutive programs of genre g

### Decision Variables

Only selection variables:

```
x_i ∈ {0,1}    ∀i ∈ P
```

**No sequencing, first/last, or run position variables!**

### Objective Function

```
Maximize:  Z = Σ (score_i + bonus_i) · x_i
              i∈P
```

*Note: Channel switch penalties are NOT included in the ILP model, only in post-processing evaluation.*

### Constraints

#### 1. Clique Constraints (Replaces Pairwise Overlaps)

For each maximal clique C_k ∈ C:

```
Σ x_i ≤ 1
i∈C_k
```

*At most one program from each clique of overlapping programs can be selected.*

**Key Insight**: Instead of O(n²) pairwise constraints, we have O(|C|) clique constraints where |C| << n² for interval graphs.

---

#### 2. Genre Diversity (Heuristic Sliding Window)

For each genre g ∈ G, for each window W_{g,m} = {i_m, i_{m+1}, ..., i_{m+R}} of R+1 consecutive programs of genre g (sorted by start time):

**Only add constraint if programs could potentially be scheduled consecutively:**

Check: If end_{i_m} ≤ start_{i_{m+R}} (no overlap between first and last), AND
       For all consecutive pairs in window: start_{i_{k+1}} - end_{i_k} < D (gaps too small for other programs)

Then:
```
Σ x_i ≤ R
i∈W_{g,m}
```

*Among R+1 programs of the same genre that could be consecutive, select at most R.*

---

### Complete RILP Model Summary

```
Maximize:
    Z = Σ (score_i + bonus_i) · x_i
        i∈P

Subject to:
    Σ x_i ≤ 1                                        ∀C_k ∈ C
    i∈C_k
    
    Σ x_i ≤ R                                        ∀g ∈ G, ∀feasible windows W_{g,m}
    i∈W_{g,m}
    
    x_i ∈ {0,1}                                      ∀i ∈ P
```

---

## Model Comparison

### Complexity Analysis

| Aspect | ILP | RILP |
|--------|-----|------|
| **Variables** | O(n² + n·R) | O(n) |
| **Overlap Constraints** | O(n²) worst case | O(\|C\|), typically O(n) |
| **Genre Constraints** | O(n·R + n²·R) | O(n·R) sliding windows |
| **Total Constraints** | O(n²·R) | O(n·R) |

Where:
- n = number of programs
- R = max consecutive genre
- |C| = number of maximal cliques (typically much smaller than n²)

### Variable Counts Example

For n = 500 programs, R = 2:

**ILP:**
- Selection: 500
- Sequencing: ~50,000 (n² worst case)
- First/Last: 1,000
- Run positions: 1,000
- **Total: ~52,500 variables**

**RILP:**
- Selection: 500
- **Total: 500 variables** (100× reduction!)

---

## Key Mathematical Insights

### 1. Interval Graph Property (RILP)

The overlap graph is an **interval graph** (each program is an interval on a timeline). Interval graphs have special properties:

- Maximal cliques can be found in O(n log n) time
- Perfect graph: clique cover = chromatic number
- Enables dramatic constraint reduction

### 2. Genre Constraint Modeling (ILP)

The genre constraint is modeled using **run position tracking**, similar to:
- State machines / finite automata
- Dynamic programming state transitions
- Each program "inherits" position from predecessor or resets

Mathematical property:
```
run_{i,k+1} ≥ seq_{j,i} + run_{j,k} - 1

Equivalent to:
If seq_{j,i} = 1 AND run_{j,k} = 1, then run_{i,k+1} ≥ 1
```

### 3. Flow Conservation (ILP)

The sequencing constraints form a **network flow** model:
- Source: "first" node
- Sink: "last" node  
- Each program: transshipment node
- Flow = 1 if selected, 0 otherwise

This ensures the schedule forms a **single chain** (Hamiltonian path on selected programs).

---

## Example Calculation

### Small Example

**Programs:**
- P1: [0, 30], Drama, score=50
- P2: [30, 60], Drama, score=60
- P3: [30, 90], Comedy, score=80
- P4: [60, 90], Drama, score=40

**Parameters:** R=2, S_pen=5

**ILP Solution:**

Variables:
```
x = [1, 1, 0, 0]  (select P1, P2)
seq_{1,2} = 1     (P2 follows P1)
first_1 = 1
last_2 = 1
run_{1,1} = 1     (P1 is 1st Drama)
run_{2,2} = 1     (P2 is 2nd Drama)
```

Objective value:
```
Z = 50 + 60 - 0 (same channel) = 110
```

Constraints satisfied:
- P1 and P3 overlap: x_1 + x_3 = 1 ≤ 1 ✓
- P2 and P3 overlap: x_2 + x_3 = 1 ≤ 1 ✓
- Flow from P1: seq_{1,2} + last_1 = 1 = x_1 ✓
- Flow to P2: seq_{1,2} + first_2 = 1 = x_2 ✓
- Genre: P2 follows P1 (both Drama), run positions [1,2] ✓

---

## Implementation Notes

### Computing bonus_i

```
bonus_i = Σ pref.bonus
          pref ∈ TimePreferences
          where:
            pref.genre = genre_i AND
            max(start_i, pref.start) < min(end_i, pref.end) - D
```

### Finding Cliques (RILP)

**Sweep Line Algorithm:**

```
1. Create events: (start_i, 'START', i) and (end_i, 'END', i)
2. Sort events by time (ENDs before STARTs at same time)
3. Active = ∅, Cliques = ∅
4. For each event:
     If START:
       Active = Active ∪ {i}
       If |Active| > 1: Cliques = Cliques ∪ {Active}
     If END:
       Active = Active \ {i}
5. Remove non-maximal cliques
```

Time complexity: O(n log n + m) where m = |Cliques|

---

## Extensions and Variations

### 1. Soft Genre Constraint

Replace hard limit R with penalty:

```
Add variable: violation_g ≥ 0 for each genre g

Objective: Z = ... - penalty · Σ violation_g
                              g

Constraint: consecutive_run_count ≤ R + violation_g
```

### 2. Minimum Schedule Length

Add constraint:
```
Σ (end_i - start_i) · x_i ≥ MinLength
i∈P
```

### 3. Required Programs

For must-include programs M ⊆ P:
```
x_i = 1    ∀i ∈ M
```

### 4. Multi-Objective

Lexicographic optimization:
1. Maximize score (primary)
2. Minimize switches (secondary)
3. Maximize coverage (tertiary)

```
Solve:
  max score
  s.t. original constraints
  
Then:
  min switches
  s.t. score ≥ optimal_score - ε
```

---

## References

### Mathematical Programming

- Wolsey, L. A. (1998). *Integer Programming*. Wiley.
- Bertsimas, D., & Weismantel, R. (2005). *Optimization over Integers*. Dynamic Ideas.

### Interval Scheduling

- Golumbic, M. C. (2004). *Algorithmic Graph Theory and Perfect Graphs*. Elsevier.
- Kleinberg, J., & Tardos, É. (2006). *Algorithm Design*. Pearson.

### Network Flows

- Ahuja, R. K., Magnanti, T. L., & Orlin, J. B. (1993). *Network Flows*. Prentice Hall.
