import sys
import json
import time
import argparse
import os
from ortools.sat.python import cp_model
from collections import defaultdict


def load_input(filepath):
    with open(filepath, 'r') as f:
        return json.load(f)


def save_output(output_data, filepath):
    with open(filepath, 'w') as f:
        json.dump(output_data, f, indent=4)


def solve_with_ortools(input_data, output_file, time_limit=300, num_cores=8, hint_file=None, min_score=None,
                       max_score=None):
    start_time = time.time()

    O = input_data['opening_time']
    E = input_data['closing_time']
    D = input_data['min_duration']
    R = input_data['max_consecutive_genre']
    S_pen = input_data['switch_penalty']
    T_pen = input_data['termination_penalty']

    priority_blocks = input_data.get('priority_blocks', [])
    time_prefs = input_data.get('time_preferences', [])
    channels_data = input_data['channels']

    candidates = []
    prog_id_map = {}

    for channel in channels_data:
        cid = channel['channel_id']
        for prog in channel['programs']:
            if prog['start'] >= E or prog['end'] <= O:
                continue

            v_start = max(O, prog['start'])
            v_end = min(E, prog['end'])

            forbidden = []
            for block in priority_blocks:
                if cid not in block['allowed_channels']:
                    f_s = max(v_start, block['start'])
                    f_e = min(v_end, block['end'])
                    if f_s < f_e:
                        forbidden.append((f_s, f_e))

            windows = [(v_start, v_end)]
            for f_s, f_e in forbidden:
                next_w = []
                for c_s, c_e in windows:
                    if c_e <= f_s or c_s >= f_e:
                        next_w.append((c_s, c_e))
                        continue
                    if c_s < f_s:
                        next_w.append((c_s, f_s))
                    if c_e > f_e:
                        next_w.append((f_e, c_e))
                windows = next_w

            for w_s, w_e in windows:
                orig_dur = prog['end'] - prog['start']
                m_d = min(D, orig_dur)
                if (w_e - w_s) < m_d:
                    continue

                candidates.append({
                    'id': prog['program_id'],
                    'channel': cid,
                    'genre': prog['genre'],
                    'w_start': w_s,
                    'w_end': w_e,
                    'orig_start': prog['start'],
                    'orig_end': prog['end'],
                    'min_d': m_d,
                    'orig_dur': orig_dur,
                    'score': prog['score']
                })

    candidates.sort(key=lambda c: c['w_start'])

    n = len(candidates)
    model = cp_model.CpModel()

    is_present = [model.NewBoolVar(f'p_{i}') for i in range(n)]
    starts = [model.NewIntVar(c['w_start'], c['w_end'], f's_{i}') for i, c in enumerate(candidates)]
    ends = [model.NewIntVar(c['w_start'], c['w_end'], f'e_{i}') for i, c in enumerate(candidates)]
    durations = [model.NewIntVar(0, c['w_end'] - c['w_start'], f'd_{i}') for i, c in enumerate(candidates)]
    intervals = []

    for i, c in enumerate(candidates):
        model.Add(durations[i] == ends[i] - starts[i])
        model.Add(durations[i] >= c['min_d']).OnlyEnforceIf(is_present[i])
        model.Add(durations[i] == 0).OnlyEnforceIf(is_present[i].Not())
        intervals.append(model.NewOptionalIntervalVar(starts[i], durations[i], ends[i], is_present[i], f'int_{i}'))

    model.AddNoOverlap(intervals)

    start_node = n
    end_node = n + 1
    arcs = []
    arc_literals = {}
    switch_vars = []

    # Candidates are sorted by w_start, so nearby indices are temporally close.
    # Building a full O(n^2) transition graph is intractable for large inputs
    # (e.g. 100k+ programs), so we only connect each program to its K nearest
    # temporal successors, plus a "safety net" arc to the first reachable
    # program on every channel so no channel becomes unreachable later in the
    # timeline just because it's surrounded by many candidates in between.
    K_NEIGHBORS = 100
    segments_by_channel = defaultdict(list)
    for idx, c in enumerate(candidates):
        segments_by_channel[c['channel']].append(idx)

    def feasible_transition(i, j):
        return candidates[i]['w_start'] + candidates[i]['min_d'] <= candidates[j]['w_end'] - candidates[j]['min_d']

    def add_transition(i, j):
        if (i, j) in arc_literals:
            return
        lit = model.NewBoolVar(f'a_{i}_{j}')
        arcs.append([i, j, lit])
        arc_literals[(i, j)] = lit
        model.AddImplication(lit, is_present[i])
        model.AddImplication(lit, is_present[j])
        model.Add(ends[i] <= starts[j]).OnlyEnforceIf(lit)
        if candidates[i]['channel'] != candidates[j]['channel']:
            switch_vars.append(lit)

    for i in range(n):
        lit = model.NewBoolVar(f'st_{i}')
        arcs.append([start_node, i, lit])
        arc_literals[(start_node, i)] = lit
        model.AddImplication(lit, is_present[i])

        lit = model.NewBoolVar(f'{i}_en')
        arcs.append([i, end_node, lit])
        arc_literals[(i, end_node)] = lit
        model.AddImplication(lit, is_present[i])

        lit = model.NewBoolVar(f'self_{i}')
        arcs.append([i, i, lit])
        model.Add(is_present[i] == 0).OnlyEnforceIf(lit)
        model.Add(is_present[i] == 1).OnlyEnforceIf(lit.Not())

        # K nearest temporal successors.
        count = 0
        for j in range(i + 1, n):
            if not feasible_transition(i, j):
                continue
            add_transition(i, j)
            count += 1
            if count >= K_NEIGHBORS:
                break

        # Safety net: first reachable program on every channel.
        for cid, indices in segments_by_channel.items():
            for j in indices:
                if j <= i:
                    continue
                if not feasible_transition(i, j):
                    continue
                add_transition(i, j)
                break

    arcs.append([end_node, start_node, model.NewConstant(1)])
    model.AddCircuit(arcs)

    genre_runs = [model.NewIntVar(1, R, f'gr_{i}') for i in range(n)]
    for i in range(n):
        if (start_node, i) in arc_literals:
            model.Add(genre_runs[i] == 1).OnlyEnforceIf(arc_literals[(start_node, i)])

    for (i, j), lit in arc_literals.items():
        if i == start_node or j in (start_node, end_node) or i == j:
            continue
        if candidates[i]['genre'] == candidates[j]['genre']:
            model.Add(genre_runs[j] == genre_runs[i] + 1).OnlyEnforceIf(lit)
        else:
            model.Add(genre_runs[j] == 1).OnlyEnforceIf(lit)

    obj_base = sum(c['score'] * is_present[i] for i, c in enumerate(candidates))

    term_penalties = []
    for i, c in enumerate(candidates):
        late_s = model.NewBoolVar(f'ls_{i}')
        model.Add(starts[i] > c['orig_start']).OnlyEnforceIf(late_s)
        model.Add(starts[i] == c['orig_start']).OnlyEnforceIf([is_present[i], late_s.Not()])
        model.AddImplication(late_s, is_present[i])

        early_e = model.NewBoolVar(f'ee_{i}')
        model.Add(ends[i] < c['orig_end']).OnlyEnforceIf(early_e)
        model.Add(ends[i] == c['orig_end']).OnlyEnforceIf([is_present[i], early_e.Not()])
        model.AddImplication(early_e, is_present[i])

        term_penalties.extend([late_s, early_e])

    bonus_terms = []
    for i, c in enumerate(candidates):
        for b_idx, pref in enumerate(time_prefs):
            if c['genre'] == pref['preferred_genre']:
                p_s = max(O, c['orig_start'], pref['start'])
                p_e = min(E, c['orig_end'], pref['end'])
                if p_e - p_s >= c['min_d']:
                    b_lit = model.NewBoolVar(f'b_{i}_{b_idx}')
                    model.Add(starts[i] <= pref['end'] - D).OnlyEnforceIf(b_lit)
                    model.Add(ends[i] >= pref['start'] + D).OnlyEnforceIf(b_lit)
                    model.AddImplication(b_lit, is_present[i])
                    bonus_terms.append(b_lit * pref['bonus'])

    total_obj = obj_base + sum(bonus_terms) - (S_pen * sum(switch_vars)) - (T_pen * sum(term_penalties))
    if min_score is not None: model.Add(total_obj >= min_score)
    if max_score is not None: model.Add(total_obj <= max_score)
    model.Maximize(total_obj)

    if hint_file and os.path.exists(hint_file):
        try:
            with open(hint_file, 'r') as hf:
                h_data = json.load(hf)
            for sp in h_data.get('scheduled_programs', []):
                for i, c in enumerate(candidates):
                    if c['id'] == sp['program_id'] and c['channel'] == sp['channel_id']:
                        if c['w_start'] <= sp['start'] and c['w_end'] >= sp['end']:
                            model.AddHint(is_present[i], 1)
                            model.AddHint(starts[i], sp['start'])
                            model.AddHint(ends[i], sp['end'])
                            break
        except:
            pass

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = num_cores
    solver.parameters.log_search_progress = True
    status = solver.Solve(model)

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        res_progs = []
        for i, c in enumerate(candidates):
            if solver.BooleanValue(is_present[i]):
                res_progs.append({
                    "program_id": c['id'], "channel_id": c['channel'],
                    "start": solver.Value(starts[i]), "end": solver.Value(ends[i])
                })
        res_progs.sort(key=lambda x: x['start'])
        output_data = {"scheduled_programs": res_progs, "total_score": int(solver.ObjectiveValue())}
        save_output(output_data, output_file)
        return output_data
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('input')
    parser.add_argument('output')
    parser.add_argument('-t', '--time', type=int, default=300)
    parser.add_argument('-c', '--cores', type=int, default=8)
    parser.add_argument('--hint')
    parser.add_argument('--min_score', type=int)
    parser.add_argument('--max_score', type=int)
    args = parser.parse_args()
    solve_with_ortools(load_input(args.input), args.output, args.time, args.cores, args.hint, args.min_score,
                       args.max_score)
