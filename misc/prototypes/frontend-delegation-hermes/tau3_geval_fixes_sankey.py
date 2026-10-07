# Extended Sankey for tau3-geval-failure-fixes-plan.md: the collated geval failures (airline, retail r3, telecom),
# Run -> Cause bucket -> Root cause -> Proposed fix. Every failed task is assigned to exactly one root cause and fix
# (MAPPING below; the per-task evidence is in each run's observations/per_task_diagnoses.md).
#   python3 tau3_geval_fixes_sankey.py <collated_failure_classes.csv> <dir with fdh_sankeys.py> <out_dir>
#   -> <out_dir>/tau3-geval-fixes-sankey.html, <out_dir>/tau3-geval-fixes-mapping.csv
# The page/style/renderer come from the dump repo's fdh_sankeys.py (same look as the collated chart), widened to 4 columns.
import collections
import csv
import importlib.util
import os
import sys

CSV, LIBDIR, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
spec = importlib.util.spec_from_file_location('fdh_sankeys', os.path.join(LIBDIR, 'fdh_sankeys.py'))
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)

# root cause id -> (label, fix id); fix id -> (label, tier). Tier: code = fires only on a failure state, proven
# no-op on passing paths (G0/G1); prompt = behaviour change behind its own flag, gated by G2 replay + G4 paired A/B.
ROOT = {
    'phone_format': ('Phone in wrong format, never retried', 'A1'),
    'name_not_spelled': ('Misheard name: not re-spelled / spelling unused', 'A3'),
    'asr_lost': ('ASR lost or garbled the characters', 'NA_ASR'),
    'id_frontend': ('No lookup attempted', 'NA_OTHER'),
    'no_consent': ('Wrote without a clear yes to the exact change', 'B1'),
    'policy_forbids': ('Made a change the policy forbids', 'B2'),
    'incomplete_scope': ('Incomplete search or request scope', 'B3'),
    'open_steps': ('Transferred with required steps still open', 'C1'),
    'false_impossible': ('False "can\'t do that", then transfer', 'C2'),
    'stall_429': ('Backend 429 stall (60 s Retry-After)', 'P0'),
    'other': ('Breakdown, simulator, wrong fact, detail', 'NA_OTHER'),
}
FIX = {
    'A1': ('A1 Phone-field format retry in the tool relay', 'code'),
    'A3': ('A3 Spelled / heard / unknown field note', 'prompt'),
    'B1': ('B1 Policy-led consent: summary + explicit yes', 'prompt'),
    'B2': ('B2 Policy check: allow within limits', 'prompt'),
    'B3': ('B3 Whole-request scope check', 'prompt'),
    'C1': ('C1 Policy-first transfer, relevant steps', 'prompt'),
    'C2': ('C2 Capability check before "can\'t" / transfer', 'prompt'),
    'P0': ('P0 Run hygiene: 429 gate (runtime fixes: track I)', 'infra'),
    'NA_ASR': ('Not in this plan: ASR accuracy', 'none'),
    'NA_OTHER': ('Not in this plan (other buckets)', 'none'),
}
# (series, task) -> root cause, for the three priority buckets; every other failure maps by bucket below.
MAPPING = {
    ('telecom', '#24'): 'phone_format', ('telecom', '#70'): 'phone_format', ('telecom', '#86'): 'phone_format',
    ('telecom', '#92'): 'phone_format',
    ('retail_r3', '52'): 'name_not_spelled', ('retail_r3', '78'): 'name_not_spelled', ('retail_r3', '81'): 'name_not_spelled',
    ('retail_r3', '94'): 'name_not_spelled', ('retail_r3', '99'): 'name_not_spelled',
    ('retail_r3', '5'): 'asr_lost', ('retail_r3', '85'): 'asr_lost', ('telecom', '#2'): 'asr_lost', ('telecom', '#25'): 'asr_lost',
    ('telecom', '#36'): 'asr_lost', ('airline', '16'): 'asr_lost',
    ('retail_r3', '2'): 'id_frontend', ('retail_r3', '8'): 'id_frontend',
    ('airline', '12'): 'no_consent', ('retail_r3', '18'): 'no_consent', ('retail_r3', '21'): 'no_consent',
    ('retail_r3', '25'): 'no_consent', ('retail_r3', '71'): 'no_consent', ('retail_r3', '93'): 'no_consent',
    ('retail_r3', '98'): 'no_consent',
    ('airline', '1'): 'policy_forbids', ('airline', '14'): 'policy_forbids', ('airline', '29'): 'policy_forbids',
    ('airline', '30'): 'policy_forbids',
    ('airline', '21'): 'incomplete_scope', ('airline', '24'): 'incomplete_scope', ('airline', '42'): 'incomplete_scope',
    ('retail_r3', '20'): 'incomplete_scope', ('retail_r3', '37'): 'incomplete_scope', ('retail_r3', '79'): 'incomplete_scope',
    ('retail_r3', '103'): 'incomplete_scope',
    ('telecom', '#13'): 'open_steps', ('telecom', '#81'): 'open_steps',
    **{('telecom', f'#{k}'): 'open_steps' for k in (3, 4, 17, 19, 20, 22, 26, 30, 35, 38, 46)},
    ('airline', '32'): 'false_impossible', ('airline', '44'): 'false_impossible', ('retail_r3', '77'): 'false_impossible',
}
PRIORITY = {'wrong_change', 'transfer_wrong', 'id_format', 'id_misheard', 'id_none', 'id_late'}
SERIES = ['airline', 'retail_r3', 'telecom']
LABEL_SERIES = {'airline': 'airline', 'retail_r3': 'retail r3', 'telecom': 'telecom'}
BUCKETS = ['wrong_change', 'transfer_wrong', 'id_format', 'id_misheard', 'id_none', 'id_late',
           'breakdown', 'user_sim', 'info_wrong', 'detail_misheard', 'infra']
BUCKET_LABEL = {'wrong_change': '1. Wrong / unconfirmed change', 'transfer_wrong': '2. Transfer handled wrong',
                'id_format': '3. ID: wrong format', 'id_misheard': '3. ID: misheard', 'id_none': '3. ID: none attempted',
                'id_late': '3. ID: too late', 'breakdown': 'Breakdown', 'user_sim': 'Simulator artifact',
                'info_wrong': 'Wrong fact stated', 'detail_misheard': 'Detail misheard', 'infra': 'Infra (429)'}
TIER_COLOR = {'code': 'var(--t-code)', 'prompt': 'var(--t-prompt)', 'infra': 'var(--t-infra)', 'none': 'var(--neutral-node)'}

rows = [r for r in csv.DictReader(open(CSV)) if r.get('series', '') in SERIES or r['domain'] in ('airline', 'telecom')]
for r in rows:
    r['series'] = r.get('series') or r['domain']
    if r['series'] == 'retail':
        r['series'] = 'retail_r3'
    key = (r['series'], r['task'])
    if r['bucket'] in PRIORITY:
        r['root'] = MAPPING[key]                     # KeyError = an unmapped priority failure: fix MAPPING
    elif r['bucket'] == 'infra':
        r['root'] = 'stall_429'
    else:
        r['root'] = 'other'
    r['fix'] = ROOT[r['root']][1]
mapped = {(r['series'], r['task']) for r in rows if r['bucket'] in PRIORITY}
assert mapped == set(MAPPING), (sorted(set(MAPPING) - mapped), sorted(mapped - set(MAPPING)))
total = len(rows)
os.makedirs(OUT, exist_ok=True)
with open(f'{OUT}/tau3-geval-fixes-mapping.csv', 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['run', 'task', 'bucket', 'stage', 'root_cause', 'root_cause_label', 'fix', 'fix_label', 'fix_tier', 'note'])
    for r in sorted(rows, key=lambda r: (SERIES.index(r['series']), BUCKETS.index(r['bucket']), r['task'])):
        w.writerow([r['series'], r['task'], r['bucket'], r['stage'], r['root'], ROOT[r['root']][0], r['fix'],
                    FIX[r['fix']][0], FIX[r['fix']][1], r['note'][:300]])

nodes, links = [], []
cnt = lambda pred: [r for r in rows if pred(r)]
for s in SERIES:
    rs = cnt(lambda r: r['series'] == s)
    nodes.append({'id': 's_' + s, 'label': LABEL_SERIES[s], 'col': 0, 'value': len(rs), 'color': f'var(--d-{s})',
                  'sub': f'{len(rs)} failed', 'ctx': 'valid geval run', 'tasks': []})
for b in BUCKETS:
    rs = cnt(lambda r: r['bucket'] == b)
    if not rs:
        continue
    nodes.append({'id': 'b_' + b, 'label': BUCKET_LABEL[b], 'col': 1, 'value': len(rs), 'color': 'var(--neutral-node)',
                  'sub': ' · '.join(f"{LABEL_SERIES[s]} {sum(r['series'] == s for r in rs)}" for s in SERIES if any(r['series'] == s for r in rs)),
                  'ctx': f'of {total} failures', 'tasks': []})
    for i, s in enumerate(SERIES):
        x = [r for r in rs if r['series'] == s]
        if x:
            links.append({'s': 's_' + s, 't': 'b_' + b, 'value': len(x), 'color': f'var(--d-{s})', 'order': i,
                          'label': f'{LABEL_SERIES[s]}: {BUCKET_LABEL[b]}', 'tasks': [r['task'] for r in x], 'ctx': ''})
for rc, (lab, fx) in ROOT.items():
    rs = cnt(lambda r: r['root'] == rc)
    if not rs:
        continue
    nodes.append({'id': 'r_' + rc, 'label': lab, 'col': 2, 'value': len(rs), 'color': TIER_COLOR[FIX[fx][1]],
                  'sub': S.short_list([f"{LABEL_SERIES[r['series']]} {r['task']}" for r in rs], 6), 'ctx': f'fix {fx}',
                  'tasks': [f"{LABEL_SERIES[r['series']]} {r['task']}" for r in rs],
                  'detail': 'e.g. ' + rs[0]['note'][:220]})
    for i, b in enumerate(BUCKETS):
        x = [r for r in rs if r['bucket'] == b]
        if x:
            links.append({'s': 'b_' + b, 't': 'r_' + rc, 'value': len(x), 'color': TIER_COLOR[FIX[fx][1]], 'order': i,
                          'label': f'{BUCKET_LABEL[b]} → {lab}', 'tasks': [f"{LABEL_SERIES[r['series']]} {r['task']}" for r in x], 'ctx': ''})
for fx, (lab, tier) in FIX.items():
    rs = cnt(lambda r: r['fix'] == fx)
    if not rs:
        continue
    nodes.append({'id': 'f_' + fx, 'label': lab, 'col': 3, 'value': len(rs), 'color': TIER_COLOR[tier],
                  'sub': {'code': 'code · narrowest trigger, gated', 'prompt': 'prompt · flag + replay + paired A/B, no guarantee',
                          'infra': 'eval infrastructure', 'none': 'out of scope'}[tier],
                  'ctx': f'{len(rs)} failures targeted', 'tasks': [f"{LABEL_SERIES[r['series']]} {r['task']}" for r in rs]})
    for i, rc in enumerate(ROOT):
        x = [r for r in rs if r['root'] == rc]
        if x:
            links.append({'s': 'r_' + rc, 't': 'f_' + fx, 'value': len(x), 'color': TIER_COLOR[tier], 'order': i,
                          'label': f'{ROOT[rc][0]} → {lab}', 'tasks': [f"{LABEL_SERIES[r['series']]} {r['task']}" for r in x], 'ctx': ''})

script = S.SCRIPT.replace('const W = 1130,', 'const W = 1290,').replace('const COLX = [40, 285, 665];', 'const COLX = [20, 200, 480, 890];') \
    .replace('const cols = [0, 1, 2].map', 'const cols = [0, 1, 2, 3].map') \
    .replace('Math.max(600, 60 + cols[2].length * 60)', 'Math.max(640, 60 + Math.max(cols[2].length, cols[3].length) * 58)')
assert script.count('COLX = [20, 200, 480, 890]') == 1 and '[0, 1, 2, 3].map' in script
S.SCRIPT = script
fixed = [r for r in rows if FIX[r['fix']][1] in ('code', 'prompt')]
table = [[FIX[fx][0], FIX[fx][1]] + [str(sum(r['fix'] == fx and r['series'] == s for r in rows)) for s in SERIES]
         + [str(sum(r['fix'] == fx for r in rows))] for fx in FIX if any(r['fix'] == fx for r in rows)]
table.append(['Total failed', ''] + [str(sum(r['series'] == s for r in rows)) for s in SERIES] + [str(total)])
data = {'nodes': nodes, 'links': links, 'total': total, 'table': table,
        'colheads': ['Run', 'Cause (priority first)', 'Root cause', 'Proposed fix']}
tiles = [(total, 'failed tasks, valid geval runs'),
         (sum(r['bucket'] in PRIORITY for r in rows), 'in the 3 priority issues'),
         (len(fixed), 'targeted by a proposed fix'),
         (sum(FIX[r['fix']][1] == 'code' for r in rows), 'by a code fix (narrowest trigger, gated)')]
light = '--d-airline:#2a78d6; --d-retail_r3:#b8457a; --d-telecom:#1d9a6c; --t-code:#1d9a6c; --t-prompt:#2a78d6; --t-infra:#c98a00;'
dark = '--d-airline:#3987e5; --d-retail_r3:#d0649a; --d-telecom:#2bb583; --t-code:#2bb583; --t-prompt:#3987e5; --t-infra:#e0a21a;'
sub = ('FDH <b>geval</b> (agent <code>66f6c67</code>), valid runs only: airline, retail r3, telecom. Each failed task is assigned one '
       'root cause and one proposed fix from <code>tau3-geval-failure-fixes-plan.md</code>. Bands start in the run colour and, from the '
       'cause column on, take the <b>fix tier</b> colour: green = code fix with the narrowest trigger (runs only after a failed phone lookup; still gated, not guaranteed); '
       'blue = prompt rule behind its own flag, accepted only after the decision replay and a paired A/B against a clean control (5 trials, each fix and combined) show no regression beyond noise; '
       'amber = evaluation infrastructure; grey = not addressed by this plan. Targeted ≠ fixed: these are targets, not forecasts.')
foot = ('Source: collated_failure_classes.csv of the geval campaigns (2026-10-05_05-59-44Z_fdh-voice-geval/observations/collated/), '
        'mapping in tau3-geval-fixes-mapping.csv (next to this file), per-task evidence in each run\'s observations/per_task_diagnoses.md. '
        'Task numbers: airline and retail = tau2 task ids, telecom = #n index.')
desc = f'{total} failed tasks mapped to root causes and proposed fixes; ' + '; '.join(
    f"{FIX[fx][0]}: {sum(r['fix'] == fx for r in rows)}" for fx in FIX if any(r['fix'] == fx for r in rows)) + '.'
html = S.page('FDH geval: failures → root causes → proposed fixes', 'FDH geval: from failure to proposed fix', sub, tiles,
              [('var(--d-airline)', 'airline'), ('var(--d-retail_r3)', 'retail r3'), ('var(--d-telecom)', 'telecom'),
               ('var(--t-code)', 'code fix'), ('var(--t-prompt)', 'prompt fix (gated)'), ('var(--t-infra)', 'infra'),
               ('var(--neutral-node)', 'not in this plan')],
              desc, ['Fix', 'Tier', 'airline', 'retail r3', 'telecom', 'All'], [2, 3, 4, 5], data, foot, light, dark)
html = html.replace('max-width: 1180px', 'max-width: 1320px')
open(f'{OUT}/tau3-geval-fixes-sankey.html', 'w').write(html)
print('rows', total, 'targeted', len(fixed), collections.Counter(r['fix'] for r in rows))
