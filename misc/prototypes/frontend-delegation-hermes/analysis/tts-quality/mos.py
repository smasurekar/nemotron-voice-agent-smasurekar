"""Step 3: automatic MOS on 16 kHz audio. Runs in the SEPARATE venv ~/.cache/fdh/tts-quality-venv
(torch, speechmos, librosa, soundfile, onnxruntime), not the agent venv.

  UTMOS22-strong  tarepan/SpeechMOS v1.2.0 (torch.hub, loaded from a local clone at ~/.cache/fdh/SpeechMOS because
                  the GitHub API was rate limited; weights from the GitHub release)
  DNSMOS          speechmos.dnsmos (Microsoft DNSMOS P.835 SIG/BAK/OVRL + P.808 ONNX models)

    ~/.cache/fdh/tts-quality-venv/bin/python mos.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common as C  # noqa: E402
from testset import rows  # noqa: E402


def main() -> None:
    import torch

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    repo = os.path.expanduser("~/.cache/fdh/SpeechMOS")
    utmos = torch.hub.load(repo, "utmos22_strong", source="local", trust_repo=True).to(dev).eval()
    # the SpeechMOS repo and the pip DNSMOS package are both called "speechmos": drop the repo's from the
    # module cache before importing the pip one
    for name in [m for m in sys.modules if m == "speechmos" or m.startswith("speechmos.")]:
        del sys.modules[name]
    sys.path[:] = [x for x in sys.path if os.path.abspath(x) != os.path.abspath(repo)]
    from speechmos import dnsmos
    out = []
    for rep in range(C.REPEATS):
        for t in rows():
            for arm in C.ARMS:
                p = C.wav_path(arm, t["id"], rep)
                if not p.exists():
                    continue
                x, sr = C.read_wav(p)
                y = C.to_16k(x, sr).astype(np.float32) / 32768.0
                with torch.no_grad():
                    u = float(utmos(torch.from_numpy(y)[None].to(dev), 16000).item())
                d = dnsmos.run(y, sr=16000)
                row = {"arm": arm, "id": t["id"], "rep": rep, "utmos": round(u, 4),
                       "dnsmos_sig": round(float(d["sig_mos"]), 4), "dnsmos_bak": round(float(d["bak_mos"]), 4),
                       "dnsmos_ovrl": round(float(d["ovrl_mos"]), 4), "dnsmos_p808": round(float(d["p808_mos"]), 4)}
                out.append(row); print(json.dumps(row), flush=True)
    (C.RES / "mos.json").write_text(json.dumps({"meta": {"utmos": "tarepan/SpeechMOS v1.2.0 utmos22_strong",
                                                          "dnsmos": "speechmos 0.0.1.1 (DNSMOS P.835 + P.808)",
                                                          "device": dev, "input": "16 kHz mono"},
                                                 "rows": out}, indent=1))


if __name__ == "__main__":
    main()
