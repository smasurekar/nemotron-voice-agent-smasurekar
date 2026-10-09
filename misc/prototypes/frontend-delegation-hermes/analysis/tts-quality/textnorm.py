"""Fair text normaliser for TTS -> ASR scoring. The SAME function is applied to the reference text and to every
ASR hypothesis, for every arm, so neither side gets credit for formatting.

Rules, in order:
 1. lowercase; curly quotes -> straight; apostrophes deleted ("it's" -> "its"); "e-mail" -> "email".
 2. money: "$1,286.40" -> "1286 dollars 40 cents"; "$54" -> "54 dollars"; "dollars and <n>" -> "dollars <n>".
 3. thousands commas removed ("1,286" -> "1286"); decimals "2.0" -> "2 point 0"; times "5:30" -> "5 30".
 4. dotted acronyms "p.m." / "i.d." -> "pm" / "id"; numeric ordinals "15th" -> "15"; month + ordinal word
    ("may fifteenth") -> "may 15".
 5. email/id symbols: "@" -> " at ", a "." between two word characters -> " dot ", "_" -> " underscore ",
    "#" / "-" -> space; "gb"/"gigs" -> "gigabytes", "mister" -> "mr", "ok" -> "okay"; homophones inside digit runs:
    "for" -> "four" after a digit or single letter ("w for zero seven" -> "w 4 0 7"), "to"/"too" -> "two" between
    digits in a run of 3+ ("one to three" -> "1 2 3", but "five to seven" stays) or before "point <digit>";
    "oh" -> "zero" after a digit inside a digit run ("eight oh two oh" -> 8020);
    "hundred and <number>" / "thousand and <number>" drop the "and".
 6. "<letter> as in <word>" / "<letter> for <word>" -> "<letter>" (NATO-style spelling; not when <word> is a number).
 7. letters and digits are split apart ("w4073673" -> "w 4073673", "sanchez1479" -> "sanchez 1479").
 8. all other punctuation removed.
 9. number words -> digits. A run of single-digit words ("nine four one") becomes digits one by one; any other
    run is parsed as cardinals, starting a new number when a word cannot continue the current one
    ("fifty four" -> 54, "five thirty" -> 5 30, "five hundred" -> 500).
10. adjacent digit tokens are merged ("5 5 5 1 2 3" -> "555123", "5 30" -> "530"); runs of 2+ single letters are
    merged ("r o s s i" -> "rossi", "p m" -> "pm").
WER is computed on the resulting tokens (so a whole digit string is ONE word: one wrong digit = one word error);
CER on the resulting string, which shows partial digit errors.
"""

from __future__ import annotations

import re

UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine".split())}
TEENS = {w: i + 10 for i, w in enumerate("ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
TENS = {w: (i + 2) * 10 for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
SCALES = {"hundred": 100, "thousand": 1000}
NUMWORDS = set(UNITS) | set(TEENS) | set(TENS) | set(SCALES)
MONTHS = "january february march april may june july august september october november december".split()
ORD = {"first": "one", "second": "two", "third": "three", "fourth": "four", "fifth": "five", "sixth": "six",
       "seventh": "seven", "eighth": "eight", "ninth": "nine", "tenth": "ten", "eleventh": "eleven",
       "twelfth": "twelve", "thirteenth": "thirteen", "fourteenth": "fourteen", "fifteenth": "fifteen",
       "sixteenth": "sixteen", "seventeenth": "seventeen", "eighteenth": "eighteen", "nineteenth": "nineteen",
       "twentieth": "twenty", "thirtieth": "thirty"}
SYN = {"gb": "gigabytes", "gigs": "gigabytes", "gig": "gigabytes", "gigabyte": "gigabytes", "mister": "mr",
       "ok": "okay"}


def _cardinals(words: list[str]) -> list[str]:
    if all(w in UNITS for w in words):
        return [str(UNITS[w]) for w in words]
    out, total, cur, last = [], 0, 0, None

    def flush():
        nonlocal total, cur, last
        if last is not None:
            out.append(str(total + cur))
        total, cur, last = 0, 0, None

    for w in words:
        if w in UNITS or w in TEENS or w in TENS:
            v = UNITS.get(w, TEENS.get(w, TENS.get(w)))
            kind = "unit" if w in UNITS else "teen" if w in TEENS else "tens"
            ok = (last in ("hundred", "thousand")) or (last == "tens" and kind == "unit" and cur % 10 == 0)
            if not ok:
                flush()
            cur += v; last = kind
        elif w == "hundred":
            if last in ("unit", "teen", "tens") and 0 < cur < 100:
                cur *= 100
            else:
                flush(); cur = 100
            last = "hundred"
        elif w == "thousand":
            if last is not None and cur > 0:
                total += cur * 1000; cur = 0
            else:
                flush(); total = 1000
            last = "thousand"
    flush()
    return out


def normalize(text: str) -> str:
    t = text.lower().replace("’", "'").replace("‘", "'").replace("—", " ").replace("–", " ")
    t = t.replace("'", "").replace("e-mail", "email")
    t = re.sub(r"\$\s?(\d[\d,]*)\.(\d\d)\b", lambda m: f"{m[1]} dollars {m[2]} cents", t)
    t = re.sub(r"\$\s?(\d[\d,]*)", r"\1 dollars", t)
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)
    t = re.sub(r"(\d)\.(\d)", r"\1 point \2", t)
    t = re.sub(r"(\d):(\d\d)", r"\1 \2", t)
    t = re.sub(r"\b((?:[a-z]\.){2,})", lambda m: m[1].replace(".", "") + " ", t)
    t = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", t)
    t = re.sub(r"\b(" + "|".join(MONTHS) + r")\s+(?:the\s+)?(" + "|".join(ORD) + r")\b", lambda m: f"{m[1]} {ORD[m[2]]}", t)
    t = t.replace("@", " at ")
    t = re.sub(r"(?<=\w)\.(?=\w)", " dot ", t)
    t = t.replace("_", " underscore ").replace("#", " ").replace("-", " ")
    t = re.sub(r"\b([a-z]) (as in|for) ([a-z]+)\b", lambda m: m[0] if m[3] in NUMWORDS else m[1], t)
    t = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    toks = [SYN.get(w, w) for w in t.split()]
    # homophones inside digit runs: "for" -> four, "to"/"too" -> two, only when the neighbours are digits
    dig = lambda w: w in UNITS or w.isdigit()  # noqa: E731
    for i in range(1, len(toks) - 1):
        w, prev, nxt = toks[i], toks[i - 1], toks[i + 1]
        prev2 = toks[i - 2] if i >= 2 else ""
        nxt2 = toks[i + 2] if i + 2 < len(toks) else ""
        in_run = dig(prev) and dig(nxt) and (dig(prev2) or dig(nxt2))
        if w == "for" and nxt in UNITS and (dig(prev) or (len(prev) == 1 and prev.isalpha()) or in_run):
            toks[i] = "four"
        elif w in ("to", "too") and (in_run or (nxt == "point" and dig(nxt2))):
            toks[i] = "two"
        elif w == "oh" and dig(prev) and (dig(nxt) or dig(prev2)):
            toks[i] = "zero"
    # "two hundred and eighty six" -> "two hundred eighty six"
    toks = [w for i, w in enumerate(toks) if not (w == "and" and 0 < i < len(toks) - 1
                                                   and toks[i - 1] in ("hundred", "thousand") and toks[i + 1] in NUMWORDS)]
    # number words -> digits
    out, run = [], []
    for w in toks + [None]:
        if w is not None and w in NUMWORDS:
            run.append(w); continue
        if run:
            out += _cardinals(run); run = []
        if w is not None:
            out.append(w)
    out = re.sub(r"\bdollars and (?=\d)", "dollars ", " ".join(out)).split()
    # merge digit runs and single-letter runs
    merged: list[str] = []
    letters: list[bool] = []  # True if merged[i] was built only from single letters
    for w in out:
        single = len(w) == 1 and w.isalpha()
        if merged and w.isdigit() and merged[-1].isdigit():
            merged[-1] += w
        elif merged and single and letters[-1]:
            merged[-1] += w
        else:
            merged.append(w); letters.append(single)
    return " ".join(merged)


def edit_distance(a: list, b: list) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def wer(ref: str, hyp: str) -> float:
    r = ref.split()
    return edit_distance(r, hyp.split()) / max(1, len(r))


def cer(ref: str, hyp: str) -> float:
    return edit_distance(list(ref), list(hyp)) / max(1, len(ref))


def has_entity(entity: str, hyp_norm: str) -> bool:
    e, h = entity.split(), hyp_norm.split()
    return any(h[i:i + len(e)] == e for i in range(len(h) - len(e) + 1))
