"""Optional step E: in-eval intelligibility of gpt-4o-mini-tts as the simulated caller, from an existing tau3 run.

This is TTS + the eval's speech environment (background noise, frame drops, 8 kHz mu-law telephone channel) + our
Nemotron ASR, NOT the TTS alone.

Per simulation (one conversation):
  spoken  = concatenation of the tick user_chunk.content pieces in tau2 simulations/<sim>.json (exactly the caller
            text that was played, cut where the caller was interrupted); bracketed tags like "[pause]" removed.
            (audio/user_labels.txt has the same text but truncates long turns at 200 characters, so it is not used.)
  heard   = concatenation of the agent's asr_final transcripts for the sim's provider_session_id (agent/events.jsonl).
Both go through textnorm.normalize(); WER/CER over the whole conversation. Entity check: the task's expected
identity (first/last name, zip, or email local part) - when the caller said it, did the ASR output contain it?

    .venv/bin/python in_eval.py
"""

from __future__ import annotations

import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

import common as C
from textnorm import cer, has_entity, normalize, wer

RUN = Path("/localhome/local-smasurekar/smasurekar/voice-agent-evaluation-dump/tau-3-voice/"
           "2026-10-07_09-34-31Z_fdh-voice-geval-fixes/fdh_voice_geval_retail_regular_rerun43")


def main() -> None:
    finals = defaultdict(list)
    with open(RUN / "agent" / "events.jsonl") as f:
        for line in f:
            if '"asr_final"' not in line:
                continue
            e = json.loads(line)
            if e.get("kind") == "asr_final" and e.get("transcript", "").strip():
                finals[e["session_id"]].append((e["timestamp"], e["transcript"].strip()))
    tasks = json.loads((RUN / "observations" / "data" / "tasks.json").read_text())
    by_session = {t["session"]: t for t in tasks.values() if t.get("session")}
    rows = []
    for p in sorted((RUN / "tau2").glob("*/simulations/*.json")):
        d = json.loads(p.read_text())
        sess = d.get("provider_session_id")
        spoken = "".join((t.get("user_chunk") or {}).get("content") or "" for t in d["ticks"])
        spoken = re.sub(r"\[[^\]]*\]", " ", spoken)
        heard = " ".join(x for _, x in sorted(finals.get(sess, [])))
        if not spoken.strip() or not heard:
            continue
        rn, hn = normalize(spoken), normalize(heard)
        ents = []
        task = by_session.get(sess)
        if task and task.get("expected_identity"):
            args = task["expected_identity"][1]
            for k in ("first_name", "last_name", "zip"):
                if args.get(k):
                    ents.append(normalize(str(args[k])))
            if args.get("email"):
                ents.append(normalize(args["email"].split("@")[0]))
        ent_rows = [{"entity": e, "heard": has_entity(e, hn)} for e in ents if has_entity(e, rn)]
        rows.append({"sim": d["id"], "task_id": d.get("task_id"), "session": sess, "ref_words": len(rn.split()),
                     "wer": round(wer(rn, hn), 4), "cer": round(cer(rn, hn), 4), "entities": ent_rows})
    W = [r["wer"] for r in rows]; Cc = [r["cer"] for r in rows]
    tot_words = sum(r["ref_words"] for r in rows)
    E = [e["heard"] for r in rows for e in r["entities"]]
    summary = {"n_sessions": len(rows), "ref_words": tot_words,
               "wer_p50": round(statistics.median(W), 4), "wer_mean": round(statistics.fmean(W), 4),
               "wer_word_weighted": round(sum(r["wer"] * r["ref_words"] for r in rows) / tot_words, 4),
               "cer_p50": round(statistics.median(Cc), 4), "cer_mean": round(statistics.fmean(Cc), 4),
               "identity_entities_said": len(E), "identity_entities_heard": sum(E)}
    (C.RES / "in_eval_retail_rerun43.json").write_text(json.dumps({"run": str(RUN), "summary": summary, "rows": rows}, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
