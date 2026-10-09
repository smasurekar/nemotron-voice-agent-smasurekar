"""Step 4: score everything and build the summary + the blind A/B listening page.

Inputs: results/synth.json, results/asr.json, results/mos.json, results/audio/**.wav
Outputs: results/per_clip.csv (one row per arm x sentence x repeat x ASR), results/audio_facts.csv,
         results/summary.json, results/listening_ab.html

Audio facts are measured on the native-rate WAV:
  silence = 20 ms frames below -45 dBFS RMS; leading/trailing silence = time before the first / after the last
  frame above it; speaking rate = words in the reference text / (duration - leading - trailing silence);
  peak dBFS; clipped = share of samples at |x| >= 32700.
  consistency: per sentence, |dur_r0 - dur_r1| / mean(dur); "very different" = more than 20 %.

    .venv/bin/python analyze.py
"""

from __future__ import annotations

import csv
import html
import json
import random
import statistics
from collections import defaultdict

import numpy as np

import common as C
from testset import CATEGORIES, rows
from textnorm import cer, has_entity, normalize, wer

ASRS = {"nemotron": "Nemotron ASR (local, our agent's ASR)", "gpt4o_transcribe": "gpt-4o-mini-transcribe (Hub)"}


def r3(x):
    return None if x is None else round(float(x), 3)


def stats(v):
    v = [x for x in v if x is not None]
    if not v:
        return {}
    return {"n": len(v), "mean": r3(statistics.fmean(v)), "p50": r3(statistics.median(v)),
            "std": r3(statistics.pstdev(v)) if len(v) > 1 else 0.0, "min": r3(min(v)), "max": r3(max(v))}


def audio_facts(x: np.ndarray, sr: int) -> dict:
    f = x.astype(np.float64) / 32768.0
    hop = int(sr * 0.02)
    n = len(f) // hop
    rms = np.sqrt((f[: n * hop].reshape(n, hop) ** 2).mean(axis=1) + 1e-12)
    voiced = np.where(20 * np.log10(rms) > -45)[0]
    dur = len(f) / sr
    lead = voiced[0] * 0.02 if len(voiced) else dur
    trail = dur - (voiced[-1] + 1) * 0.02 if len(voiced) else 0.0
    peak = float(np.abs(f).max())
    return {"sr": sr, "dur_s": round(dur, 3), "lead_sil_s": round(lead, 3), "trail_sil_s": round(max(0.0, trail), 3),
            "speech_s": round(max(0.01, dur - lead - max(0.0, trail)), 3),
            "peak_dbfs": round(20 * np.log10(peak + 1e-12), 2),
            "clipped_pct": round(100 * float((np.abs(x.astype(np.int32)) >= 32700).mean()), 4),
            "rms_dbfs": round(20 * np.log10(np.sqrt((f ** 2).mean()) + 1e-12), 2)}


def main() -> None:
    tests = {t["id"]: t for t in rows()}
    asr = json.loads((C.RES / "asr.json").read_text())["rows"]
    mos_path = C.RES / "mos.json"
    mos = {(m["arm"], m["id"], m["rep"]): m for m in json.loads(mos_path.read_text())["rows"]} if mos_path.exists() else {}
    synth = json.loads((C.RES / "synth.json").read_text())

    # --- audio facts
    facts = {}
    for arm in C.ARMS:
        for sid, t in tests.items():
            for rep in range(C.REPEATS):
                p = C.wav_path(arm, sid, rep)
                if p.exists():
                    x, sr = C.read_wav(p)
                    a = audio_facts(x, sr)
                    a["wps"] = round(len(t["text"].split()) / a["speech_s"], 3)
                    facts[(arm, sid, rep)] = a
    with open(C.RES / "audio_facts.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        keys = ["sr", "dur_s", "lead_sil_s", "trail_sil_s", "speech_s", "wps", "peak_dbfs", "rms_dbfs", "clipped_pct"]
        w.writerow(["arm", "id", "rep", *keys] + list(next(iter(mos.values())).keys())[3:] if mos else [])
        for (arm, sid, rep), a in sorted(facts.items()):
            m = mos.get((arm, sid, rep), {})
            w.writerow([arm, sid, rep, *[a[k] for k in keys]] + [m.get(k) for k in list(m)[3:]])

    # --- intelligibility per clip
    clip = []
    for r in asr:
        if not r.get("ok"):
            continue
        t = tests[r["id"]]
        ref, hyp = normalize(t["text"]), normalize(r["hyp"])
        ents = [has_entity(e, hyp) for e in t["entities"]]
        # digit part of each entity (zip, phone, order/user id digits, prices): every digit token must be in the hyp
        dents = [all(d in hyp.split() for d in e.split() if d.isdigit()) for e in t["entities"] if any(ch.isdigit() for ch in e)]
        clip.append({"arm": r["arm"], "id": r["id"], "category": t["category"], "speaker": t["speaker"], "rep": r["rep"],
                     "asr": r["asr"], "wer": round(wer(ref, hyp), 4), "cer": round(cer(ref, hyp), 4),
                     "n_entities": len(ents), "entities_ok": sum(ents), "all_entities_ok": all(ents) if ents else None,
                     "n_digit_entities": len(dents), "digit_entities_ok": sum(dents),
                     "ref_text": t["text"], "hyp_raw": r["hyp"], "ref_norm": ref, "hyp_norm": hyp})
    with open(C.RES / "per_clip.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(clip[0]))
        w.writeheader(); w.writerows(clip)

    def intel(sel):
        ent_clips = [c for c in sel if c["n_entities"]]
        return {"n_clips": len(sel), "wer": stats([c["wer"] for c in sel]), "cer": stats([c["cer"] for c in sel]),
                "entity_sentence_acc": r3(sum(c["all_entities_ok"] for c in ent_clips) / len(ent_clips)) if ent_clips else None,
                "entity_item_acc": r3(sum(c["entities_ok"] for c in ent_clips) / sum(c["n_entities"] for c in ent_clips)) if ent_clips else None,
                "n_entity_items": sum(c["n_entities"] for c in ent_clips),
                "digit_entity_item_acc": r3(sum(c["digit_entities_ok"] for c in sel) / sum(c["n_digit_entities"] for c in sel))
                if sum(c["n_digit_entities"] for c in sel) else None,
                "n_digit_entity_items": sum(c["n_digit_entities"] for c in sel)}

    summary = {"arms": {a: l for a, (l, _) in C.ARMS.items()}, "asrs": ASRS, "categories": CATEGORIES,
               "synth_meta": synth["meta"], "synth_errors": sum(1 for r in synth["rows"] if not r["ok"]),
               "synth_retries": sum(r["attempts"] - 1 for r in synth["rows"]),
               "intelligibility": {}, "mos": {}, "audio": {}, "worst": {}}
    for a in C.ARMS:
        summary["intelligibility"][a] = {}
        for s in ASRS:
            sel = [c for c in clip if c["arm"] == a and c["asr"] == s]
            summary["intelligibility"][a][s] = {"overall": intel(sel),
                                                **{cat: intel([c for c in sel if c["category"] == cat]) for cat in CATEGORIES}}
        # mos
        M = [m for k, m in mos.items() if k[0] == a]
        mk = ["utmos", "dnsmos_sig", "dnsmos_bak", "dnsmos_ovrl", "dnsmos_p808"]
        summary["mos"][a] = {"overall": {k: stats([m[k] for m in M]) for k in mk},
                             **{cat: {k: stats([m[k] for m in M if tests[m["id"]]["category"] == cat]) for k in mk}
                                for cat in CATEGORIES}}
        # audio facts + consistency
        F = {k: v for k, v in facts.items() if k[0] == a}
        rel = []
        for sid in tests:
            if (a, sid, 0) in F and (a, sid, 1) in F:
                d0, d1 = F[(a, sid, 0)]["dur_s"], F[(a, sid, 1)]["dur_s"]
                rel.append(abs(d0 - d1) / ((d0 + d1) / 2))
        summary["audio"][a] = {
            "sample_rate": sorted({v["sr"] for v in F.values()}),
            **{k: stats([v[k] for v in F.values()]) for k in ("dur_s", "wps", "lead_sil_s", "trail_sil_s", "peak_dbfs", "rms_dbfs", "clipped_pct")},
            "wps_by_category": {cat: stats([v["wps"] for k, v in F.items() if tests[k[1]]["category"] == cat]) for cat in CATEGORIES},
            "repeat_dur_rel_diff": stats(rel),
            "repeat_dur_diff_gt20pct": sum(1 for x in rel if x > 0.20), "repeat_pairs": len(rel),
            "clips_with_clipping": sum(1 for v in F.values() if v["clipped_pct"] > 0),
        }
        # repeat consistency of intelligibility: entity result differs between r0 and r1 (Nemotron)
        flip = 0; pairs = 0
        for sid, t in tests.items():
            if not t["entities"]:
                continue
            e = {c["rep"]: c["all_entities_ok"] for c in clip if c["arm"] == a and c["id"] == sid and c["asr"] == "nemotron"}
            if 0 in e and 1 in e:
                pairs += 1; flip += e[0] != e[1]
        summary["audio"][a]["entity_result_flips_between_repeats_nemotron"] = {"flips": flip, "pairs": pairs}
        # worst clips per ASR
        summary["worst"][a] = {s: [{k: c[k] for k in ("id", "rep", "category", "wer", "cer", "all_entities_ok", "ref_text", "hyp_raw")}
                                   for c in sorted([c for c in clip if c["arm"] == a and c["asr"] == s],
                                                   key=lambda c: (-c["cer"], -c["wer"]))[:8]] for s in ASRS}
    (C.RES / "summary.json").write_text(json.dumps(summary, indent=1))
    build_ab(tests)
    print(json.dumps({a: {s: {"wer_mean": v[s]["overall"]["wer"].get("mean"), "cer_mean": v[s]["overall"]["cer"].get("mean"),
                              "ent": v[s]["overall"]["entity_sentence_acc"]} for s in ASRS}
                      for a, v in summary["intelligibility"].items()}, indent=1))


def build_ab(tests: dict) -> None:
    rnd = random.Random(20261008)
    trs = []
    for sid, t in tests.items():
        flip = rnd.random() < 0.5
        trs.append((sid, t, flip))
    rows_html = []
    for sid, t, flip in trs:
        rows_html.append(
            f'<tr data-id="{sid}" data-flip="{int(flip)}"><td class="id">{sid}<br><span class="cat">{html.escape(CATEGORIES[t["category"]])}'
            f' &middot; {t["speaker"]}</span></td><td class="txt">{html.escape(t["text"])}</td>'
            f'<td><div class="lab">A <span class="who"></span></div><audio controls preload="none" class="a"></audio>'
            f'<div class="lab">B <span class="who"></span></div><audio controls preload="none" class="b"></audio></td>'
            f'<td class="vote"><label><input type="radio" name="v{sid}" value="A">A</label>'
            f'<label><input type="radio" name="v{sid}" value="same">same</label>'
            f'<label><input type="radio" name="v{sid}" value="B">B</label>'
            f'<button class="rev" type="button">reveal</button></td></tr>')
    page = PAGE.replace("{{ROWS}}", "\n".join(rows_html)).replace("{{N}}", str(len(trs)))
    (C.RES / "listening_ab.html").write_text(page)


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Blind TTS A/B</title>
<style>
:root{--bg:#fbfbfa;--fg:#1d1d1b;--muted:#6b6b66;--line:#e2e1dc;--card:#fff;--accent:#2f6f4f}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#161615;--fg:#ecebe6;--muted:#9a9993;--line:#2e2e2b;--card:#1e1e1c;--accent:#7cc39b}}
:root[data-theme="dark"]{--bg:#161615;--fg:#ecebe6;--muted:#9a9993;--line:#2e2e2b;--card:#1e1e1c;--accent:#7cc39b}
body{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto}h1{font-size:22px;margin:0 0 6px}p{color:var(--muted);margin:4px 0 12px}
.bar{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin:12px 0 16px}
select,button{font:inherit;color:var(--fg);background:var(--card);border:1px solid var(--line);border-radius:6px;padding:4px 10px;cursor:pointer}
table{width:100%;border-collapse:collapse;background:var(--card)}td{border-top:1px solid var(--line);padding:10px 8px;vertical-align:top}
.id{white-space:nowrap;font-weight:600}.cat{font-weight:400;color:var(--muted);font-size:12px}.txt{max-width:380px}
audio{width:240px;height:34px;display:block;margin-bottom:6px}.lab{font-size:12px;font-weight:600}.who{color:var(--accent);font-weight:600}
.vote label{display:inline-block;margin-right:8px}.vote button{margin-top:6px;display:block}#score{font-weight:600;color:var(--accent)}
@media (max-width:720px){table,tbody,tr,td{display:block}td{border:0;padding:4px 0}tr{border-top:1px solid var(--line);padding:10px 0}audio{width:100%}}
</style></head><body><main>
<h1>Blind A/B: Magpie TTS vs gpt-4o-mini-tts</h1>
<p>{{N}} sentences from the &tau;&sup3; quality test set. In each row A and B are the same text from the two models, in random order.
Listen, pick the one you prefer (or "same"), then reveal. Votes stay in this browser only.</p>
<div class="bar"><label>gpt-4o-mini-tts voice: <select id="voice"><option value="gpt4omini_ash">ash</option><option value="gpt4omini_coral">coral</option></select></label>
<button id="revall" type="button">Reveal all + score</button><button id="clear" type="button">Clear votes</button><span id="score"></span></div>
<table><tbody>
{{ROWS}}
</tbody></table></main>
<script>
const NAMES={magpie_john:"Magpie (John)",gpt4omini_ash:"gpt-4o-mini-tts (ash)",gpt4omini_coral:"gpt-4o-mini-tts (coral)"};
function store(k,v){try{if(v===undefined)return localStorage.getItem(k);if(v===null)localStorage.removeItem(k);else localStorage.setItem(k,v)}catch(e){return null}}
const sel=document.getElementById("voice");sel.value=store("ab_voice")||"gpt4omini_ash";
function arms(tr){const g=sel.value,f=tr.dataset.flip==="1";return f?[g,"magpie_john"]:["magpie_john",g]}
function load(){document.querySelectorAll("tr[data-id]").forEach(tr=>{const id=tr.dataset.id,[a,b]=arms(tr);
 tr.querySelector("audio.a").src="audio/"+a+"/"+id+"_r0.wav";tr.querySelector("audio.b").src="audio/"+b+"/"+id+"_r0.wav";
 tr.querySelectorAll(".who").forEach(w=>w.textContent="");const v=store("ab_"+sel.value+"_"+id);
 tr.querySelectorAll("input").forEach(i=>i.checked=(i.value===v))});document.getElementById("score").textContent=""}
function reveal(tr){const [a,b]=arms(tr),w=tr.querySelectorAll(".who");w[0].textContent=NAMES[a];w[1].textContent=NAMES[b]}
sel.onchange=()=>{store("ab_voice",sel.value);load()};
document.querySelectorAll("tr[data-id]").forEach(tr=>{tr.querySelector(".rev").onclick=()=>reveal(tr);
 tr.querySelectorAll("input").forEach(i=>i.onchange=()=>store("ab_"+sel.value+"_"+tr.dataset.id,i.value))});
document.getElementById("revall").onclick=()=>{let m=0,g=0,s=0;document.querySelectorAll("tr[data-id]").forEach(tr=>{reveal(tr);
 const v=(tr.querySelector("input:checked")||{}).value;if(!v)return;if(v==="same"){s++;return}
 const [a,b]=arms(tr);const pick=v==="A"?a:b;pick==="magpie_john"?m++:g++});
 document.getElementById("score").textContent="Preferred Magpie: "+m+" · gpt-4o-mini-tts: "+g+" · same: "+s};
document.getElementById("clear").onclick=()=>{document.querySelectorAll("tr[data-id]").forEach(tr=>store("ab_"+sel.value+"_"+tr.dataset.id,null));load()};
load();
</script></body></html>
"""

if __name__ == "__main__":
    main()
