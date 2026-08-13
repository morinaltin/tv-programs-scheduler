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

def solve_with_ortools(input_data, output_file, time_limit=300, num_cores=8, hint_file=None, min_score=None, max_score=None):
    start_time = time.time()
    
    O = input_data['opening_time']
    E = input_data['closing_time']
    D = input_data['min_duration']
    
    priority_blocks = input_data.get('priority_blocks', [])
    time_prefs = input_data.get('time_preferences', [])
    channels_data = input_data['channels']

    candidates = []
    
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
                    'score': prog['score']
                })

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

    # RILP has no sequencing-dependent terms (no channel-switch penalty, no
    # genre-run tracking, no trimming penalty), so the objective only depends
    # on which programs are selected and their timing. NoOverlap alone fully
    # captures that, making an O(n^2) Circuit/transition graph unnecessary
    # extra work that doesn't scale to large inputs (10k+ programs).
    model.AddNoOverlap(intervals)

    obj_base = sum(c['score'] * is_present[i] for i, c in enumerate(candidates))
    
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

    total_obj = obj_base + sum(bonus_terms)
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
        except: pass

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
    args = parser.parse_args()
    solve_with_ortools(load_input(args.input), args.output, args.time, args.cores, args.hint)