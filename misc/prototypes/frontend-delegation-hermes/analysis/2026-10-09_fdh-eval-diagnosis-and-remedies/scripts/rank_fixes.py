# Rank generic fixes by the failures they would address (fdh-eval-diagnosis-and-remedies.md §1).
#   python3 rank_fixes.py            (reads ../data/fix_map.json; prints the tables used in the report)
# fix_map.json: one row per failed task (207: 6 fixes-on runs, gpt-5.2 and gpt-5.5 user simulators) with the primary
# fix that addresses its first irrecoverable problem and a flip likelihood (high/medium/low).
# Expected flips weight high 0.8, medium 0.5, low 0.2.
import json, os, collections
HERE = os.path.dirname(os.path.abspath(__file__))
M = json.load(open(os.path.join(HERE, '..', 'data', 'fix_map.json')))
W = {'high': 0.8, 'medium': 0.5, 'low': 0.2}
COLS = [('airline', 'gpt-5.2'), ('retail', 'gpt-5.2'), ('telecom', 'gpt-5.2'), ('airline', 'gpt-5.5'), ('retail', 'gpt-5.5'), ('telecom', 'gpt-5.5')]
N = {'airline': 50, 'retail': 114, 'telecom': 114}
P = {('airline', 'gpt-5.2'): 37, ('retail', 'gpt-5.2'): 71, ('telecom', 'gpt-5.2'): 77,
     ('airline', 'gpt-5.5'): 39, ('retail', 'gpt-5.5'): 75, ('telecom', 'gpt-5.5'): 50}
sec = collections.Counter(f for m in M for f in m.get('secondary_fixes') or [])
print('| Fix | ' + ' | '.join(f'{d} {s}' for d, s in COLS) + ' | Primary | Expected flips | Also secondary |')
for f in sorted({m['primary_fix'] for m in M}):
    c = [sum(1 for m in M if m['primary_fix'] == f and (m['dom'], m['sim']) == x) for x in COLS]
    e = sum(W[m['flip_likelihood']] for m in M if m['primary_fix'] == f)
    lk = collections.Counter(m['flip_likelihood'] for m in M if m['primary_fix'] == f)
    print(f"| {f} | " + ' | '.join(map(str, c)) + f" | {sum(c)} | {e:.1f} (h{lk['high']}/m{lk['medium']}/l{lk['low']}) | {sec.get(f, 0)} |")
for name, S in (('F1+F2', {'F1', 'F2'}), ('+F4', {'F1', 'F2', 'F4'}), ('+F8', {'F1', 'F2', 'F4', 'F8'}),
                ('+F5+F9+F3+F6', {'F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F8', 'F9'})):
    print(name, ' | '.join(f"{d} {s}: {P[(d, s)] / N[d]:.3f} -> {(P[(d, s)] + sum(W[m['flip_likelihood']] for m in M if m['primary_fix'] in S and (m['dom'], m['sim']) == (d, s))) / N[d]:.3f}" for d, s in COLS))
