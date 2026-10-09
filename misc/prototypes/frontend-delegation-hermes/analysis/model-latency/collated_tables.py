# Writes one "collated across runs" table per model into model-latency-analysis.md (between <!-- collated:KEY --> markers,
# so it can be re-run) and into results/collated_tables.md. Inputs: results/log_metrics.json (analyze_logs.py) and the
# concurrency-1 benchmark numbers below (from results/bench_*.json, 2026-10-08 09:34-09:46 UTC, /localhome).
#   python3 collated_tables.py
import json, os, re
os.chdir(os.path.dirname(os.path.abspath(__file__)))  # paths below are relative to this folder
M = json.load(open('results/log_metrics.json'))
M['new_airline']['max_concurrency'] = [4, 2]   # stored sorted; the run went 4 then 2
RUNS = [('old_airline', 'C-air', 'airline'), ('old_retail_r3', 'C-r3', 'retail r3'), ('old_retail_r2', 'C-r2', 'retail r2 (429s)'),
        ('old_telecom', 'C-tel', 'telecom'), ('new_airline', 'F-air', 'airline'), ('new_retail_fixes', 'F-ret', 'retail run 1 (429s)'),
        ('new_retail_rerun43', 'F-r43', 'retail re-run 43'), ('new_telecom', 'F-tel', 'telecom (429s)')]
# model key -> (section heading prefix, metric, unit scale to display, unit, benchmark p50 in display units, bench label, extra-cols fn)
def be(r):
    return f"429 retries {r.get('backend_429_retries', 0)} in {r.get('backend_429_sessions', 0)} sessions" if r.get('backend_429_retries') else '0 × 429'
def fe(r): return f"{r.get('frontend_timeouts', 0)} timeouts (4 s)"
def tts(r):
    n = (r.get('tau2_retries') or {}).get('synthesize_voice:APITimeoutError', 0); return f'{n} APITimeoutError retries'
def none(r): return '—'
def judge(r): return f"{r.get('judge_gpt52_calls_outside_sims', '?')} calls (latency not logged)"
def nemo(r):
    e = r.get('nemo_speech_log_errors') or {}; return f"{e.get('tts_codec_worker_failed', 0)} cancelled-stream codec errors (barge-in)"
MODELS = [
    ('backend', '### Backend LLM', 'backend_llm_call_s', 1, 's', 1.76, 'total 1.76 s per call (TTFT 0.92 s)', be),
    ('backend_turn', None, 'backend_turn_ms', 0.001, 's', None, 'one delegated user turn (≈1.4 LLM calls)', be),
    ('frontend', '### Frontend LLM', 'frontend_decision_ms', 0.001, 's', 0.50, '0.50 s', fe),
    ('asr', '### Voice Agent ASR', 'asr_user_stop_to_final_ms', 1, 'ms', 15, '15 ms after last audio', none),
    ('tts', '### Voice Agent TTS', 'tts_decision_to_first_audio_ms', 1, 'ms', 35, '35 ms to first audio', nemo),
    ('usersim', '### User simulator LLM', 'user_sim_gpt52_s', 1, 's', 3.18, '3.18 s', none),
    ('judge', '### Judge LLM', None, 1, 's', 15.0, '15.0 s', judge),
    ('usertts', '### Tau3 Eval TTS', 'user_tts_s', 1, 's', 1.93, '1.93 s total', tts),
    ('decision', '### (Extra) Interruption', 'decision_gpt41_interrupt_s', 1, 's', 1.09, '1.09 s', none),
]
def fmt(x, unit):
    return '' if x is None else (f'{x:.0f}' if unit == 'ms' else f'{x:.2f}')
def table(key, metric, scale, unit, bench, blabel, extra):
    head = (f'| Run | Arm | Machine | Concurrency | n | p50 ({unit}) | p90 ({unit}) | p99 ({unit}) | p50 ÷ benchmark | Errors / notes |\n'
            '|---|---|---|---|---|---|---|---|---|---|\n')
    rows = []
    for rk, short, label in RUNS:
        r = M[rk]; d = r.get(metric) if metric else None
        mach = '/home' if r['machine'].startswith('other') else '/localhome'
        conc = '→'.join(str(c) for c in r.get('max_concurrency') or [])
        if d:
            p50, p90, p99 = d['p50'] * scale, d['p90'] * scale, d['p99'] * scale
            ratio = f'{p50 / bench:.1f}×' if bench else '—'
            bold = (lambda s: f'**{s}**') if bench and unit != 'ms' and p50 / bench >= 1.4 else (lambda s: s)
            rows.append(f"| {short} {label} | {r['arm']} | {mach} | {conc} | {d['n']} | {bold(fmt(p50, unit))} | {fmt(p90, unit)} | {fmt(p99, unit)} | {ratio} | {extra(r)} |")
        else:
            rows.append(f"| {short} {label} | {r['arm']} | {mach} | {conc} | — | — | — | — | — | {extra(r)} |")
    return (f'**Collated across runs** (benchmark today at concurrency 1: {blabel}; **bold** = p50 at least 1.4× the benchmark):\n\n'
            + head + '\n'.join(rows) + '\n')
# overview: model x run p50
def overview():
    cols = [s for _, s, _ in RUNS]
    out = ['| Model | Benchmark (c=1) | ' + ' | '.join(cols) + ' |', '|---|---|' + '---|' * len(cols)]
    names = {'backend': 'Backend LLM nemotron-3-ultra, per call (s)', 'backend_turn': 'Backend, per user turn (s)',
             'frontend': 'Frontend LLM nemotron-3.5-lightning (s)', 'asr': 'ASR nemotron-speech-streaming (ms)',
             'tts': 'TTS magpie (ms, first audio)', 'usersim': 'User sim gpt-5.2 (s)', 'judge': 'Judge gpt-5.2 (s)',
             'usertts': 'User TTS gpt-4o-mini-tts (s)', 'decision': 'Decision gpt-4.1 (s)'}
    for key, _, metric, scale, unit, bench, blabel, _ in MODELS:
        cells = []
        for rk, _, _ in RUNS:
            d = M[rk].get(metric) if metric else None
            cells.append('—' if not d else (f"**{fmt(d['p50'] * scale, unit)}**" if bench and unit != 'ms' and d['p50'] * scale / bench >= 1.4 else fmt(d['p50'] * scale, unit)))
        out.append(f"| {names[key]} | {fmt(bench, unit) if bench else '—'} | " + ' | '.join(cells) + ' |')
    errs = ['| 429 retries (backend) | — | ' + ' | '.join(str(M[rk].get('backend_429_retries', 0)) for rk, _, _ in RUNS) + ' |',
            '| Frontend timeouts | — | ' + ' | '.join(str(M[rk].get('frontend_timeouts', 0)) for rk, _, _ in RUNS) + ' |',
            '| User TTS timeouts | — | ' + ' | '.join(str((M[rk].get('tau2_retries') or {}).get('synthesize_voice:APITimeoutError', 0)) for rk, _, _ in RUNS) + ' |',
            '| Machine | — | ' + ' | '.join('/home' if M[rk]['machine'].startswith('other') else '/localhome' for rk, _, _ in RUNS) + ' |',
            '| Concurrency | — | ' + ' | '.join('→'.join(map(str, M[rk].get('max_concurrency') or [])) for rk, _, _ in RUNS) + ' |']
    return ('**All models × all runs, p50** (C- = control/fixes off, F- = fixes on; **bold** = at least 1.4× today\'s concurrency-1 '
            'benchmark; local ASR/TTS ms values are never bold, they are far below anything audible):\n\n' + '\n'.join(out + errs) + '\n')
doc = open('model-latency-analysis.md').read()
def put(doc, key, text, anchor_heading=None):
    block = f'<!-- collated:{key} -->\n{text}<!-- /collated:{key} -->\n'
    if f'<!-- collated:{key} -->' in doc:
        return re.sub(rf'<!-- collated:{key} -->.*?<!-- /collated:{key} -->\n', lambda m: block, doc, flags=re.S)
    # insert at the end of the section that starts with anchor_heading (before the next ## or ### heading)
    i = doc.index(anchor_heading); j = re.search(r'\n#{2,3} ', doc[i + 4:]); j = i + 4 + j.start() + 1 if j else len(doc)
    return doc[:j] + '\n' + block + '\n' + doc[j:]
allmd = ['# Collated tables (generated by collated_tables.py)\n', overview()]
doc = put(doc, 'overview', overview(), '## Summary table')
for key, heading, metric, scale, unit, bench, blabel, extra in MODELS:
    t = table(key, metric, scale, unit, bench, blabel, extra)
    allmd.append(f'\n## {key}\n\n' + t)
    if key == 'backend_turn':
        doc = put(doc, key, '\nPer delegated user turn:\n\n' + t, '### Backend LLM')
    elif heading:
        doc = put(doc, key, t, heading)
open('model-latency-analysis.md', 'w').write(doc)
open('results/collated_tables.md', 'w').write('\n'.join(allmd))
print('ok')
