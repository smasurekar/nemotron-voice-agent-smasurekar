# TTS quality: gpt-4o-mini-tts vs Magpie TTS

Question: *is gpt-4o-mini-tts on par with Magpie TTS — what about the quality?* Speed is covered in
[`../model-latency/model-latency-analysis.md`](../model-latency/model-latency-analysis.md), section "Head-to-head".

## Verdict

- **Naturalness: on par.** The automatic MOS scores are within about 0.1 of each other. UTMOS: Magpie 4.38, ash 4.49, coral 4.41. DNSMOS OVRL: 3.44, 3.52, 3.42. That is noise level for automatic predictors, and the order flips between the two gpt voices.
- **Intelligibility on plain speech and numbers/prices/dates: on par.** Both models are near-perfect: WER ≤ 1.6% through our Nemotron ASR, and 100% of the price, date and time entities survive.
- **Intelligibility on spelled names, emails and IDs: gpt-4o-mini-tts is better.**
  - Through our Nemotron ASR, WER is 6–7% vs 12%, entity accuracy 76–77% vs 70%, and digit strings come through 92–95% vs 86%.
  - The gap comes almost entirely from *caller-style* spelled text ("C as in Charlie, H, E, N", "one nine one two two"). Magpie reads it fast and some letters and digits get merged or misheard.
  - On *agent-style* text, which is Magpie's real job in the agent, the two are close: WER 7.0% vs 6.1–6.3%. Even there, Magpie mangled the alphanumeric code "4WQ150".
- **Magpie is better at:** consistency (the same text gives a 5% duration change between repeats vs 9–11%), and of course latency (~30× faster to first audio).
- **gpt-4o-mini-tts is better at:** spelled-out letters and digit runs, because it speaks them slower and with clearer gaps.

Net: in *quality*, gpt-4o-mini-tts is at least on par with Magpie, and somewhat better on the entity-heavy content where our evals fail. Its problem is latency, not quality.

## Summary table

41 sentences × 2 repeats per arm (82 clips per arm). WER/CER = mean over clips. "Entities" = share of key entities found in the transcript (92 entity checks per arm). "Digits" = share of the entities' digit strings found exactly (64 per arm).

| Arm | Nemotron ASR (ours): WER / entities / digits | gpt-4o-mini-transcribe: WER / entities / digits | UTMOS (mean ± sd) | DNSMOS OVRL | Speaking rate (words/s) | Repeat duration diff (mean; pairs > 20%) |
|---|---|---|---|---|---|---|
| **Magpie** (John, local) | 12.4% / 70% / 86% | 4.5% / 82% / 94% | 4.38 ± 0.11 | 3.44 ± 0.09 | 2.55 | **5.3%; 0 of 41** |
| **gpt-4o-mini-tts, ash** (+ persona) | **6.7% / 77% / 95%** | **2.7% / 87% / 98%** | **4.49 ± 0.09** | **3.52 ± 0.07** | 2.16 | 8.6%; 3 of 41 |
| **gpt-4o-mini-tts, coral** (+ persona) | 6.2% / 76% / 92% | 2.9% / 85% / 97% | 4.41 ± 0.14 | 3.42 ± 0.07 | 2.29 | 10.7%; 6 of 41 |

Median WER is 0% for the gpt arms with both ASRs. For Magpie it is 5.9% with Nemotron and 0% with gpt-4o-mini-transcribe.

**Setup.** All runs were on 2026-10-08.
- Synthesis: 10:21–10:29 UTC, interleaved, concurrency 1, 0 errors, 0 retries.
- ASR: 10:29–10:36 UTC.

The arms:
- **Magpie:** magpie-tts-multilingual, voice John. Local NeMo Speech, gRPC `SynthesizeOnline`, LINEAR_PCM 22.05 kHz, exactly as the agent calls it.
- **gpt-4o-mini-tts:** `openai/openai/gpt-4o-mini-tts` on the Inference Hub, pcm 24 kHz, with the τ³ caller persona `instructions` (STYLE from `bench_tts_head_to_head.py`). Voices `ash` and `coral`.
- **Not tested:** the Hub's `nvidia/nvidia/magpie-tts-multilingual-357m`. `audio.speech.create` returns 400 `litellm.BadRequestError: LLM Provider NOT provided` (2 tries).

## Test set

41 sentences in 5 categories, in `testset.py`, with the exact text and key entities.
- **Speakers:** 18 are written as the agent would say them (symbols such as `$1,286.40`, `#W4073673`, `daiki.sanchez1479@example.com`). 23 are written as the caller would (spelled out: "R O S S I", "five five five, one two three ...").
- **Same text for every arm.**

| Category | n | Examples |
|---|---|---|
| Plain conversational | 8 | "Hi, yeah, I need to cancel my flight. Something came up at work ..." |
| Numbers, prices, dates, times | 8 | "$1,286.40", "May 15th at 5:30 PM", "two point zero gigabytes" |
| Spelled-out names and letters | 8 | "Rossi, R O S S I", "Chen, C as in Charlie, H, E, N", "Sofia Li", "Kovacs" |
| Emails | 8 | "mia dot garcia four seven at example dot com", "daiki.sanchez1479@example.com" |
| IDs and digits | 9 | "#W4073673", "mei_kovacs_8020", "555-123-2002", "one nine one two two", "4WQ150" |

## A) Intelligibility

Every clip (both repeats) was resampled to 16 kHz mono and transcribed by:
1. **Nemotron ASR:** our agent's real ASR (nemotron-speech-streaming-en-0.6b, local Riva gRPC, the agent's config).
2. **gpt-4o-mini-transcribe:** on the Hub.

### Normaliser

`textnorm.py` is applied identically to the reference and to every hypothesis, for every arm. In order:
1. Lowercase; apostrophes deleted.
2. Money: `$54.20` → "54 dollars 20 cents", and "dollars and 20" → "dollars 20".
3. Thousands commas dropped; `2.0` → "2 point 0"; `5:30` → "5 30".
4. Dotted acronyms (`p.m.` → pm); `15th` → 15; "May fifteenth" → "may 15".
5. Email and ID symbols: `@` → at, `.` inside a word → dot, `_` → underscore, `#` and `-` → space. Also gb → gigabytes, mister → mr, ok → okay.
6. "C as in Charlie" → c.
7. Letters and digits split apart (`w4073673` → "w 4073673").
8. Other punctuation removed.
9. Number words → digits:
   - single-digit runs are read digit by digit;
   - anything else is read as cardinals ("fifty four" → 54, "five thirty" → 5 30, "two hundred and eighty six" → 286);
   - homophones inside digit runs: "for" → 4 and "to"/"too" → 2 (only between digits, so "five to seven" stays), "oh" → 0.
10. Adjacent digit tokens are merged, and so are runs of single letters ("r o s s i" → rossi).

**How to read WER.** WER counts a whole digit string as one word, so one wrong digit is one word error. CER shows partial misses.

**Entity checks.**
- An entity counts as found when its normalised tokens appear contiguously in the normalised transcript.
- "Digits" only requires the entity's digit strings: zip, phone, order and user-id digits, prices.
- Name spellings that no TTS can disambiguate (Sofia → "Sophia", Mei → "May", Kovacs → "Covax", Li → "Lee") count as misses for **every** arm. This is why entity accuracy is well below 100% even for the best arm.

### Per category, Nemotron ASR (our agent's ASR)

| Category | Magpie (John) | gpt-4o-mini-tts (ash) | gpt-4o-mini-tts (coral) |
|---|---|---|---|
| **Overall** | WER 12.4% (p50 5.9%), CER 4.1%; entities 70%, digits 86% | WER 6.7% (p50 0.0%), CER 3.2%; entities 77%, digits 95% | WER 6.2% (p50 0.0%), CER 3.1%; entities 76%, digits 92% |
| Plain conversational | WER 1.3% (p50 0.0%), CER 0.1% | WER 0.7% (p50 0.0%), CER 0.1% | WER 0.3% (p50 0.0%), CER 0.2% |
| Numbers, prices, dates, times | WER 1.6% (p50 0.0%), CER 0.9%; entities 100%, digits 100% | WER 0.3% (p50 0.0%), CER 0.2%; entities 100%, digits 100% | WER 0.0% (p50 0.0%), CER 0.0%; entities 100%, digits 100% |
| Spelled-out names and letters | WER 32.3% (p50 16.7%), CER 9.0%; entities 65% | WER 16.2% (p50 16.7%), CER 5.1%; entities 81% | WER 13.7% (p50 16.0%), CER 5.5%; entities 81% |
| Emails | WER 12.8% (p50 11.5%), CER 4.7%; entities 38%, digits 71% | WER 5.8% (p50 6.1%), CER 3.5%; entities 44%, digits 100% | WER 7.4% (p50 3.1%), CER 4.4%; entities 50%, digits 100% |
| IDs and digits | WER 13.8% (p50 15.0%), CER 5.7%; entities 59%, digits 77% | WER 10.2% (p50 5.0%), CER 6.8%; entities 68%, digits 86% | WER 9.2% (p50 0.0%), CER 5.3%; entities 59%, digits 77% |

### Per category, gpt-4o-mini-transcribe (Hub)

| Category | Magpie (John) | gpt-4o-mini-tts (ash) | gpt-4o-mini-tts (coral) |
|---|---|---|---|
| **Overall** | WER 4.5% (p50 0.0%), CER 1.7%; entities 82%, digits 94% | WER 2.7% (p50 0.0%), CER 2.0%; entities 87%, digits 98% | WER 2.9% (p50 0.0%), CER 1.8%; entities 85%, digits 97% |
| Plain conversational | WER 0.0%, CER 0.0% | WER 0.0%, CER 0.0% | WER 0.0%, CER 0.0% |
| Numbers, prices, dates, times | WER 0.9%, CER 0.3%; entities 100%, digits 100% | WER 0.0%; entities 100%, digits 100% | WER 0.0%; entities 100%, digits 100% |
| Spelled-out names and letters | WER 10.2% (p50 8.3%), CER 3.6%; entities 88% | WER 3.1% (p50 0.0%), CER 1.8%; entities 92% | WER 2.6% (p50 0.0%), CER 1.1%; entities 92% |
| Emails | WER 5.7%, CER 2.2%; entities 56%, digits 86% | WER 4.4%, CER 2.2%; entities 69%, digits 100% | WER 5.6%, CER 2.7%; entities 56%, digits 100% |
| IDs and digits | WER 5.6%, CER 2.4%; entities 68%, digits 91% | WER 5.8%, CER 5.4%; entities 77%, digits 96% | WER 5.8%, CER 4.9%; entities 77%, digits 91% |

### Agent-style vs caller-style text

This is where the gap comes from.

| ASR | Text style | Magpie (John) | gpt-4o-mini-tts (ash) | gpt-4o-mini-tts (coral) |
|---|---|---|---|---|
| Nemotron | agent (18 sentences) | WER 7.0%, entities 75%, digits 83% | WER 6.3%, entities 77%, digits 97% | WER 6.1%, entities 77%, digits 92% |
| Nemotron | caller (23 sentences) | **WER 16.6%**, entities 65%, digits 89% | WER 7.1%, entities 77%, digits 93% | WER 6.3%, entities 75%, digits 93% |
| gpt-4o-mini-transcribe | agent | WER 3.9%, entities 82%, digits 89% | WER 3.5%, entities 86%, digits 97% | WER 3.6%, entities 86%, digits 94% |
| gpt-4o-mini-transcribe | caller | WER 5.0%, entities 81%, digits 100% | WER 2.1%, entities 88%, digits 100% | WER 2.3%, entities 83%, digits 100% |

### Worst clips (Nemotron ASR, reference → what was heard)

**Magpie.** Here the TTS output itself is at fault, or its fast delivery makes the ASR merge sounds:

| Clip | Reference | Heard |
|---|---|---|
| s02 (both repeats) | It's Chen, C as in Charlie, H, E, N. | "Its **chin** C as in Charlie **Hen**" |
| s03 | My first name is Daiki, D A I K I, ... | "My first name is **Dyke D Aik** ..." |
| s04 r0 | ... and Li is L I. | "... and Lee is L . L. L. L. L. L. L. L. L. L. L." (ASR loop on Magpie audio) |
| d08 | Your reservation ID is 4WQ150, on flight HAT214. | "... is **for Buy Yushi one hundred fifty** on flight hat two hundred fourteen" |
| d04 / d07 r0 | ... zip code is 19122 / one nine one two two | "... one nine one two" (the double "two two" merged) |
| e06 | ethan.kim8811@example.com | "Ethan **Dott** Kim **eight one one**" |
| s01, d07 | My last name ... / My zip code ... | "**By** last name ...", "**By** zip code ..." |

**gpt-4o-mini-tts (ash/coral).** Failures are rarer, and mostly spelling ambiguity or the voice skipping symbols:

| Clip | Reference | Heard |
|---|---|---|
| d04 (ash, coral) | Your user ID is mei_kovacs_8020 ... | "Your user ID is **MEI COVAX** eight zero two zero ..." (the TTS does not say "underscore") |
| d01 r1 (ash) | I found order #W4073673. | "... order number W **four seven three six seven three**" (a zero dropped) |
| d04 r0 (coral) | ... zip code is 19122. | "... one nine one two two **two**" |
| s06 (ash, coral) | ... and Ms. Nguyen. | "... and Miss **W**." / "Miss **Wynne**" |
| e02 | daiki.sanchez1479@example.com | "**deikey** dot Sanchez one four seven nine ..." |

Every clip with its raw transcript is in `results/per_clip.csv`. The worst 8 per arm × ASR are in `results/summary.json` → `worst`.

## B) Naturalness (automatic MOS, 16 kHz)

| Predictor | Magpie (John) | gpt-4o-mini-tts (ash) | gpt-4o-mini-tts (coral) |
|---|---|---|---|
| UTMOS22-strong | 4.38 ± 0.11 | 4.49 ± 0.09 | 4.41 ± 0.14 |
| DNSMOS P.835 SIG | 3.66 ± 0.07 | 3.71 ± 0.06 | 3.65 ± 0.06 |
| DNSMOS P.835 BAK | 4.21 ± 0.05 | 4.21 ± 0.03 | 4.19 ± 0.03 |
| DNSMOS P.835 OVRL | 3.44 ± 0.09 | 3.52 ± 0.07 | 3.42 ± 0.07 |
| DNSMOS P.808 | 4.03 ± 0.13 | 4.11 ± 0.13 | 3.94 ± 0.13 |

Per category (UTMOS / DNSMOS OVRL):

| Category | Magpie (John) | gpt-4o-mini-tts (ash) | gpt-4o-mini-tts (coral) |
|---|---|---|---|
| Plain conversational | 4.32 / 3.46 | 4.49 / 3.54 | 4.37 / 3.47 |
| Numbers, prices, dates, times | 4.40 / 3.44 | 4.50 / 3.53 | 4.40 / 3.41 |
| Spelled-out names and letters | 4.31 / 3.40 | 4.47 / 3.52 | 4.37 / 3.40 |
| Emails | 4.43 / 3.46 | 4.50 / 3.51 | 4.45 / 3.42 |
| IDs and digits | 4.43 / 3.42 | 4.49 / 3.48 | 4.46 / 3.40 |

All three are high-quality, clean TTS: UTMOS above 4.3 and DNSMOS BAK above 4.1 (no noise).
- ash is consistently a little ahead.
- coral and Magpie are tied.
- The voice matters about as much as the model.

Predictors:
- **UTMOS22-strong:** `tarepan/SpeechMOS` v1.2.0, loaded from a local clone because the GitHub API was rate-limiting `torch.hub`.
- **DNSMOS:** the `speechmos` 0.0.1.1 package (Microsoft's P.835 and P.808 ONNX models).
- Both ran in the separate venv `~/.cache/fdh/tts-quality-venv` on the H100.

## C) Audio facts

| | Magpie (John) | gpt-4o-mini-tts (ash) | gpt-4o-mini-tts (coral) |
|---|---|---|---|
| Sample rate | 22 050 Hz | 24 000 Hz | 24 000 Hz |
| Clip duration, mean (min–max) | 6.31 s (2.83–9.43) | 6.94 s (3.95–10.00) | 6.63 s (3.30–9.65) |
| Speaking rate, words/s (mean) | **2.55** | 2.16 | 2.29 |
| Rate by category: plain / numbers / names / emails / IDs | 3.29 / 2.75 / 2.91 / 1.84 / 2.02 | 2.88 / 2.48 / 2.17 / 1.59 / 1.75 | 3.14 / 2.58 / 2.21 / 1.75 / 1.81 |
| Leading silence, mean (p50) | 0.22 s (0.22) | 0.09 s (0.02) | 0.10 s (0.04) |
| Trailing silence, mean (max) | 0.56 s (0.88) | 0.46 s (1.81) | 0.45 s (1.39) |
| Peak level, mean (max) | −9.7 dBFS (−5.3) | −6.9 dBFS (−2.7) | −12.2 dBFS (−6.0) |
| RMS level, mean | −28.2 dBFS | −23.6 dBFS | −28.8 dBFS |
| Clipped samples | none | none | none |
| Same text twice: duration difference, mean (max) | **5.3% (18%)** | 8.6% (26%) | 10.7% (32%) |
| Repeat pairs differing > 20% | **0 of 41** | 3 of 41 | 6 of 41 |
| Entity result flips between repeats (Nemotron) | 2 of 33 | 5 of 33 | 7 of 33 |

What this shows:
- **Pace.** Magpie speaks about 15% faster, and the gap is largest on spelled names (2.9 vs 2.2 words/s). This matches where it loses intelligibility.
- **Consistency.** gpt-4o-mini-tts varies more from take to take: in pacing, and occasionally in content (a dropped or extra digit).
- **Silence and level.**
  - Magpie always leaves a ~0.2 s lead-in.
  - gpt-4o-mini-tts usually starts at once, but sometimes leaves up to 1.8 s of trailing silence.
  - ash is about 4–5 dB louder than the other two.
  - None of them clip.

Silence means 20 ms frames below −45 dBFS. Speaking rate = words in the reference text ÷ (duration − leading − trailing silence).

## D) Blind A/B listening page

[`results/listening_ab.html`](results/listening_ab.html). Open it locally, with the WAVs next to it in `results/audio/`.
- **Each row:** one sentence, players A and B (Magpie vs gpt-4o-mini-tts), in a random order fixed per row.
- **Controls:** a voice switch (ash/coral); a per-row reveal and a "Reveal all + score" tally.
- **Votes:** saved in the browser only.

Nobody has listened to it yet: **no human preference results are reported here.**

## E) In-eval intelligibility of the caller voice (optional, TTS + noise + ASR)

From the τ³ run `2026-10-07_09-34-31Z_fdh-voice-geval-fixes/fdh_voice_geval_retail_regular_rerun43`, 43 conversations. Script: `in_eval.py`.

What was compared:
- **What the caller said:** the gpt-4o-mini-tts caller text that was actually played. These are the tick `user_chunk.content` pieces in the tau2 simulation JSON, cut at interruptions. `user_labels.txt` was not used because it truncates turns at 200 characters.
- **What the agent heard:** the agent's `asr_final` transcripts for that session.

**This is not the TTS alone.** It is gpt-4o-mini-tts + the eval's speech environment (background noise, frame drops, 8 kHz μ-law telephone audio) + Nemotron ASR, compared at conversation level.

| | Value |
|---|---|
| Conversations / caller words | 43 / 23 973 |
| WER, p50 / mean / word-weighted | 16.4% / 22.1% / 20.6% |
| CER, p50 / mean | 13.3% / 17.8% |
| Task identity entities the caller said (name, zip, email local part) that the ASR heard | 106 of 123 (86%) |

**How it compares with section A.**
- The same voice, in clean 16 kHz audio, has 6.7% WER.
- So in the eval, the channel (noise, drops, 8 kHz) and conversation effects add far more error than the TTS itself. Conversation effects include barge-in overlap and caller speech the agent's ASR ignores while it is speaking.
- Most missed identities are email local parts and names, the same pattern as in section A.

**Not available:** a matching in-eval number for Magpie. The agent's own speech is not transcribed in the eval.

## Caveats

- **Automatic MOS ≠ human MOS.**
  - UTMOS and DNSMOS were trained on other data. Differences of about 0.1 are within their error.
  - They do not judge prosody, persona fit or "does it sound like a caller".
  - Use the A/B page for a human check.
- **Small test set.** 41 sentences × 2 repeats; 5 categories of 8–9 sentences. One clip changes a category's entity accuracy by 6–12 points. Treat category differences under ~10 points as noise.
- **Voices and setups differ.**
  - Magpie used one voice (John). gpt-4o-mini-tts used two (ash, coral), with the τ³ caller persona instructions.
  - The persona's "calm, slightly impatient caller" may slow the delivery, which helps spelled content.
  - Magpie's other voices were not tested.
- **ASR bias.**
  - gpt-4o-mini-transcribe is an OpenAI model and may favour OpenAI TTS audio. That is why our own Nemotron ASR is the primary measure. It shows the same direction.
  - Nemotron and Magpie are both NVIDIA models, but that did not give Magpie an advantage here.
- **The normaliser decides some results.**
  - It forgives formatting (digits vs words, "dot" vs ".") and common homophones inside digit runs. It does not forgive name spellings.
  - Without the "to/too → 2", "oh → 0", "to point" and "hundred and" rules, Magpie's Nemotron WER was 16.3% instead of 12.4%. The gpt arms barely change (ash 7.0% vs 6.7%, coral 6.6% vs 6.2%).
  - So the rules narrow the gap; they do not create it.
- **Text choice.**
  - Each arm read both agent-style and caller-style text, although in τ³ Magpie only speaks agent text.
  - Read the agent/caller split before generalising.
- **Single machine and day.** Magpie ran on the local NeMo Speech container build that the agent uses.

## How to re-run

From this folder, with the agent venv (`/localhome/local-smasurekar/smasurekar/nemotron-voice-agent-smasurekar/.venv/bin/python`, here `.venv/bin/python`):

```bash
.venv/bin/python synth.py        # needs NeMo Speech on localhost:50051 + NVIDIA_API_KEY (.env, loaded in-process)
.venv/bin/python asr.py          # Nemotron (local) + gpt-4o-mini-transcribe (Hub); resumable
~/.cache/fdh/tts-quality-venv/bin/python mos.py   # UTMOS + DNSMOS (separate venv: torch, speechmos, librosa, soundfile)
.venv/bin/python analyze.py      # per-clip scores, summary.json, audio facts, listening_ab.html
.venv/bin/python in_eval.py      # optional: in-eval caller intelligibility from the rerun43 dump
```

**MOS venv setup:**
```bash
uv venv --python 3.11 ~/.cache/fdh/tts-quality-venv
uv pip install --python ~/.cache/fdh/tts-quality-venv/bin/python torch torchaudio --index-url https://download.pytorch.org/whl/cu124
uv pip install --python ~/.cache/fdh/tts-quality-venv/bin/python numpy scipy soundfile librosa onnxruntime speechmos
git clone --depth 1 --branch v1.2.0 https://github.com/tarepan/SpeechMOS ~/.cache/fdh/SpeechMOS
```

**Hub volume:** 164 TTS calls (41 × 2 × 2 voices) and 246 transcriptions, all sequential.

## Files

| File | What |
|---|---|
| `tts-quality-analysis.md` | This write-up |
| `testset.py` | The 41 sentences: category, speaker style, exact text, key entities |
| `textnorm.py` | The normaliser, WER/CER and entity matching (rules in the docstring) |
| `common.py` | Paths, arms, WAV I/O, 16 kHz resampling; imports `model-latency/bench_common.py` for the Hub client |
| `synth.py` | Step 1: synthesis, writes `results/audio/<arm>/<id>_r<rep>.wav` and `results/synth.json` |
| `asr.py` | Step 2: both ASRs → `results/asr.json` |
| `mos.py` | Step 3: UTMOS + DNSMOS → `results/mos.json` (separate venv) |
| `analyze.py` | Step 4: `results/per_clip.csv`, `results/audio_facts.csv`, `results/summary.json`, `results/listening_ab.html` |
| `in_eval.py` | Optional step E → `results/in_eval_retail_rerun43.json` |
| `results/audio/{magpie_john,gpt4omini_ash,gpt4omini_coral}/` | 82 WAVs per arm (native rate) |
| `results/listening_ab.html` | Blind A/B page |
| `results/*.log` | Raw run logs |
