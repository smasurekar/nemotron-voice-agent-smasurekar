"""Extended collated Sankey for tau3-identity-fixes-plan.md: Domain -> Identification -> Cause -> Proposed fix.

Reuses the page, style and renderer of the dump's fdh_sankeys.py (FDH fixes-on collated analysis) and adds a
fourth column. Every failed task maps to one primary fix (FIX_OF below; the plan's section 9 lists the secondary
fixes). The chart draws only fixes with at least MIN_FIX_TASKS tasks; the table view and the CSV list all of them.

    python3 tau3_identity_fixes_sankey.py <fdh_sankeys.py> <collated_failure_classes.csv> <out_dir>

Writes <out_dir>/tau3-identity-fixes-sankey.html and <out_dir>/tau3-identity-fixes-mapping.csv.
"""

import collections
import csv
import importlib.util
import os
import sys

FIXES = [  # id, label, in_plan, hover description
    (
        "I1",
        "I1 Telecom phone-number note (prompt)",
        True,
        "A telecom-only <domain_notes> block in the Hermes system prompt: phone numbers are passed as "
        '555-123-4567. After "not found", one silent retry with the same ten digits dashed, only if the argument '
        "had exactly ten digits (a format correction, not a guess; scopes spelling_v2); never add, remove or change "
        "digits. Otherwise read the digits back in groups and ask the user to correct the number. Airline and "
        "retail prompts stay byte-identical.",
    ),
    (
        "I2",
        "Deferred: I2 zip and repeat-lookup checks",
        False,
        "Voice-server tool-argument rules: a zip that is not 5 digits is never sent (answered locally with a "
        "read-back); "
        "find_user_id_by_email, find_user_id_by_name_zip and get_customer_by_phone join the retry guard. In the three "
        "runs no successful lookup had a non-5-digit zip, and none of 55 identical repeats succeeded.",
    ),
    (
        "I3",
        "I3 Recovery hint after a failed lookup",
        True,
        "Appended to the first failed airline or retail identity lookup's result (telecom excluded), so first-try "
        "lookups see nothing new: keep other fields provisionally (a spelled field can still be misheard), ask "
        "about the most doubtful field first, one at a time, "
        "build the retry from spelled letters, never invent digits, switch method after two misses.",
    ),
    (
        "I4",
        "I4 Word-assisted spelling (later miss)",
        True,
        "Prompt only, on the second and later misses (external or local): spell the doubtful field with a word per "
        'letter ("S as in Sam") and "double S"; ask when a word and a letter disagree. Targets doubled letters, a '
        "lost leading AA and B/D/G/V swaps.",
    ),
    (
        "C1",
        "Deferred: C1 frontend dead-air guards",
        False,
        'Decider guards: a ping ("Hello? Are you still there?") in IDLE never gets an empty DIRECT reply, and a DIRECT '
        '"Let me ..." promise with the backend IDLE is delegated, so a promised action actually runs.',
    ),
    (
        "C2",
        "Deferred: C2 same-item exchange guard",
        False,
        "An exchange or item modification in which every new item equals the old item is answered locally (policy: a "
        "different option only). Fired once in the three runs, on a failed task.",
    ),
    (
        "C3",
        "Deferred: C3 key figure first",
        False,
        "spoken_output variant: the asked-for number or fact goes in the first sentence; before a chain of more than "
        "three lookups, say what you already have. Separate ablation: it changes the prompt of every session.",
    ),
    (
        "C4",
        "Deferred: C4 numbers as digits",
        False,
        "spoken_output variant: write amounts and counts as digits; TTS reads them the same, and tau2 text "
        "checks match. "
        "Optional (one benchmark artifact).",
    ),
    (
        "D_nolookup",
        "Deferred: no external lookup reached",
        False,
        "The identity failure happened before any external lookup (a username only, or every attempt rejected "
        "locally), so a hook on a lookup result never fires. Needs a pre-lookup trigger.",
    ),
    (
        "D_judgement",
        "Deferred: backend policy judgement",
        False,
        "Wrong writes, missed troubleshooting steps, wrong facts. These need policy-specific reasoning fixes "
        "with their "
        "own ablation; no change here can be shown to be trade-off free.",
    ),
    (
        "D_transfer",
        "Deferred: transfer rules",
        False,
        "Telecom transfers too early or too late, airline transfer against the user's wish. A more eager or more "
        "reluctant transfer rule trades one side for the other, so it is left out of this plan.",
    ),
    (
        "D_latency",
        "Deferred: backend latency",
        False,
        "The agent was on track but a single backend turn took 17-25 s, or replies were cut to fragments under "
        "barge-in. Needs latency work, not a prompt.",
    ),
    (
        "D_benchmark",
        "Benchmark artifact (no agent fix)",
        False,
        "The simulated user went off-script or the expected outcome was impossible.",
    ),
]

# Plain-language pop-up for the fixes in this plan: (what it does, a real example from the runs, why it's safe).
POPUP = {
    "I1": (
        "Tells the agent that phone numbers are written with dashes, like 555-123-4567. If a lookup fails and the "
        "number it sent had exactly ten digits with only the dashes wrong, it retries once with the same ten digits "
        "dashed, without asking the user again. It never adds, drops or changes a digit: a number with too few or "
        "too many digits (555123, 15551232002), or a retry that still fails, gets the digits read back in groups so "
        "the user can correct them.",
        'Telecom: the user says "five five five, one two three, two zero zero two". Today the agent looks up '
        '5551232002, gets "not found", asks for a date of birth the user doesn\'t have, and the user leaves. '
        "With the fix it looks up 555-123-2002 and finds the customer.",
        "It is added only for telecom calls; airline and retail don't change at all. Every successful telecom "
        "lookup in the runs already used dashes, so it only repeats what already works.",
    ),
    "I3": (
        "When a customer lookup fails, the agent gets a short tip with the error: keep the other details for now, "
        "but treat none as certain, because even a spelled name can be misheard. Ask about the most doubtful detail "
        "first, one at a time, have the user spell it, ask which is right when two versions differ, and never "
        "guess a missing letter or digit. After two misses, try the other way to identify the user (email, or name "
        "and zip).",
        'Retail 0: the user says zip "one, nine, one, two, two", but the agent hears 1912. Today it re-asks only the '
        'name and sends 1912 again. With the fix it keeps the name, reads back "1, 9, 1, 2", notices a zip needs '
        "five digits, and asks for the zip again: 19122.",
        "It appears only after a lookup has failed, and only in airline and retail. Calls where the first lookup "
        "works see nothing new.",
    ),
    "I4": (
        "If the lookup fails again, the agent asks the user to spell the doubtful part with a word for each letter "
        '("S as in Sam") and to say "double S" for repeated letters. Words are heard much more reliably than single '
        "letters.",
        'Airline 17: the user spells "R, O, S, S, I" again and again, and the agent hears "ROSI" or worse every '
        'time; the user gives up after 14 minutes. With the fix the user says "R as in Robert, O as in Oscar, '
        'double S as in Sam, I as in India", and the agent can write ROSSI.',
        "It appears only after two failed attempts, and it changes only the instruction the agent sees, not what "
        "the user said. If a word and a letter disagree, the agent asks instead of guessing.",
    ),
}

# The chart draws only the tasks of fixes with at least this many tasks; the table and the CSV keep every fix.
MIN_FIX_TASKS = 10

# Primary fix per failed task, keyed by (domain, task as shown in the collated CSV).
FIX_OF = {}
for t in ["#11", "#18", "#92", "#99", "#46"]:
    FIX_OF[("telecom", t)] = "I1"
for t in [
    "0",
    "3",
    "4",
    "5",
    "14",
    "26",
    "30",
    "35",
    "44",
    "66",
    "70",
    "79",
    "88",
    "90",
    "91",
    "92",
    "94",
    "98",
    "108",
    "23",
]:
    FIX_OF[("retail", t)] = "I3"
for t in ["9", "27", "29", "38", "42", "49", "63", "74", "75", "76", "78"]:
    FIX_OF[("retail", t)] = "I4"
for t in ["14", "15", "16", "17", "25", "37", "32"]:
    FIX_OF[("airline", t)] = "I4"
FIX_OF[("airline", "29")] = "I3"
for d, t in [("retail", "6"), ("retail", "8"), ("airline", "39")]:
    FIX_OF[(d, t)] = "D_nolookup"
for t in ["22", "106", "81", "59", "112"]:
    FIX_OF[("retail", t)] = "C1"
FIX_OF[("retail", "107")] = "C2"
for t in ["18", "99", "100", "111", "28", "31", "46"]:
    FIX_OF[("retail", t)] = "C3"
for t in ["11", "21"]:
    FIX_OF[("airline", t)] = "C3"
FIX_OF[("airline", "3")] = "C4"
for t in ["20", "21", "84", "71", "103"]:
    FIX_OF[("retail", t)] = "D_judgement"
for t in ["23", "24", "44", "7"]:
    FIX_OF[("airline", t)] = "D_judgement"
for t in ["#7", "#20", "#37"]:
    FIX_OF[("telecom", t)] = "D_judgement"
FIX_OF[("airline", "35")] = "D_transfer"
for t in ["#22", "#25"]:
    FIX_OF[("telecom", t)] = "D_latency"
for t in ["33", "73"]:
    FIX_OF[("retail", t)] = "D_latency"
for t in ["41", "62", "95", "105"]:
    FIX_OF[("retail", t)] = "D_benchmark"


def fix_for(row):
    """The primary fix of one collated failure row."""
    if row["domain"] == "telecom" and row["bucket"] == "id_format":
        return "I1"
    if row["bucket"] == "transfer_wrong":
        return "D_transfer"
    return FIX_OF[(row["domain"], row["task"])]


def main(sankeys_py, csv_path, out_dir):
    """Write the extended Sankey and the per-task mapping CSV."""
    spec = importlib.util.spec_from_file_location("fdh_sankeys", sankeys_py)
    S = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(S)
    # Four columns: wider canvas and card, one more column position.
    S.SCRIPT = (
        S.SCRIPT.replace("const W = 1130", "const W = 1420")
        .replace("GAP = 24", "GAP = 32")
        .replace("const COLX = [40, 285, 665];", "const COLX = [30, 250, 590, 1000];")
        .replace("[0, 1, 2].map(c =>", "[0, 1, 2, 3].map(c =>")
        .replace("60 + cols[2].length * 60", "80 + Math.max(cols[2].length, cols[3].length) * 84")
        # Fix bars: a two-part pop-up ("What it does", "Why it's safe") instead of the long description.
        .replace("n.desc, n.example));", "n.desc, n.example, n.sections));")
        .replace(
            "function tipContent(value, label, color, context, tasks, detail, desc, example) {",
            "function tipContent(value, label, color, context, tasks, detail, desc, example, sections) {",
        )
        .replace(
            "    if (tasks && tasks.length) {",
            "    (sections || []).forEach(([head, text]) => {\n"
            "      const x = document.createElement('div'); x.className = 'ts';\n"
            "      const h = document.createElement('div'); h.className = 'th'; h.textContent = head;\n"
            "      x.appendChild(h);\n"
            "      x.appendChild(document.createTextNode(text)); f.appendChild(x); });\n"
            "    if (tasks && tasks.length) {",
        )
    )
    assert "n.sections" in S.SCRIPT and "className = 'ts'" in S.SCRIPT, "fdh_sankeys.py renderer changed"
    S.STYLE = S.STYLE.replace("max-width: 1180px", "max-width: 1480px") + (
        "  #tip .ts { margin-top: 8px; line-height: 1.45; }\n  #tip .th { font-weight: 600; margin-bottom: 2px; }\n"
    )

    with open(csv_path) as f:
        all_rows = list(csv.DictReader(f))
    for r in all_rows:
        r["proposed_fix"] = fix_for(r)
    doms = [d for d in S.DOMAINS if any(r["domain"] == d for r in all_rows)]
    all_fcnt = collections.Counter((r["domain"], r["proposed_fix"]) for r in all_rows)
    fix_total = {f: sum(all_fcnt[(d, f)] for d in doms) for f, _, _, _ in FIXES}
    # The chart draws only the tasks of the large fixes, so every band runs through all four columns.
    rows = [r for r in all_rows if fix_total[r["proposed_fix"]] >= MIN_FIX_TASKS]
    total = len(rows)
    dc = {d: f"var(--d-{d})" for d in doms}
    nodes, links = [], []

    def add_node(node_id, label, col, value, color, sub, ctx, desc=None, example=None):
        nodes.append(
            {"id": node_id, "label": label, "col": col, "value": value, "color": color, "sub": sub,
             "ctx": ctx, "tasks": [], "desc": desc, "example": example}
        )  # fmt: skip

    def add_link(src, dst, d, order, label, ctx, tasks, value=None):
        links.append(
            {"s": src, "t": dst, "value": len(tasks) if value is None else value, "color": dc[d],
             "label": label, "ctx": ctx, "tasks": tasks, "order": order}
        )  # fmt: skip

    def tasks_of(d, **match):
        return [r["task"] for r in rows if r["domain"] == d and all(r[k] == v for k, v in match.items())]

    shown = collections.Counter(r["domain"] for r in rows)
    for d in doms:
        add_node(d, d, 0, shown[d], dc[d], f"{shown[d]} failed tasks shown", "FDH fixes-on run", S.DOMAIN_DESC.get(d))
    for g in "AB":
        per = {d: tasks_of(d, group=g) for d in doms}
        add_node(
            "G" + g, S.GROUPS[g], 1, sum(map(len, per.values())), "var(--neutral-node)",
            " · ".join(f"{d} {len(per[d])}" for d in doms if per[d]), f"of {total} tasks shown", S.GROUP_DESC[g],
        )  # fmt: skip
        for i, d in enumerate(doms):
            if per[d]:
                add_link(d, "G" + g, d, i, f"{d}: {S.GROUPS[g]}", f"{len(per[d])} {d} tasks", [], len(per[d]))
    for b, g, lab in S.TAXONOMY:
        per = {d: tasks_of(d, bucket=b) for d in doms}
        if not any(per.values()):
            continue
        add_node(
            b, lab, 2, sum(map(len, per.values())), "var(--neutral-node)",
            " · ".join(f"{d} {len(per[d])}" for d in doms if per[d]), S.GROUPS[g], S.BUCKET_DESC[b],
            S.BUCKET_EXAMPLE[b],
        )  # fmt: skip
        for i, d in enumerate(doms):
            if per[d]:
                add_link("G" + g, b, d, i, f"{d}: {lab}", f"{len(per[d])} {d} tasks", per[d])
    for f, lab, in_plan, desc in FIXES:
        if fix_total[f] < MIN_FIX_TASKS:
            continue
        color = "var(--fix-node)" if in_plan else "var(--deferred-node)"
        sub = " · ".join(f"{d} {all_fcnt[(d, f)]}" for d in doms if all_fcnt[(d, f)])
        add_node(f, lab, 3, fix_total[f], color, sub, "In this plan" if in_plan else "Not in this plan", desc)
        if f in POPUP:
            nodes[-1]["desc"] = None
            what, example, safe = POPUP[f]
            nodes[-1]["sections"] = [["What it does", what], ["Example", example], ["Why it's safe", safe]]
        for b, _, blab in S.TAXONOMY:
            for i, d in enumerate(doms):
                tasks = tasks_of(d, bucket=b, proposed_fix=f)
                if tasks:
                    add_link(b, f, d, i, f"{d}: {blab} → {lab}", f"{len(tasks)} {d} tasks", tasks)

    table = [
        [lab, "yes" if in_plan else "no"] + [str(all_fcnt[(d, f)]) for d in doms] + [str(fix_total[f])]
        for f, lab, in_plan, _ in FIXES
        if fix_total[f]
    ]
    failed = collections.Counter(r["domain"] for r in all_rows)
    table.append(["Total failed", ""] + [str(failed[d]) for d in doms] + [str(len(all_rows))])
    data = {
        "nodes": nodes,
        "links": links,
        "total": total,
        "table": table,
        "colheads": ["Domain", "Identification", "Cause", "Proposed fix"],
    }
    tiles = [(fix_total[f], label) for f, label in [
        ("I1", "telecom: phone-number note (I1)"),
        ("I4", "word-assisted spelling (I4)"),
        ("I3", "recovery hint after a failed lookup (I3)"),
    ]]  # fmt: skip
    light = " ".join(f"--d-{d}:{S.DOMAIN_COLOR[d][0]};" for d in doms)
    light += " --fix-node:#3d3c38; --deferred-node:#b9b8b2;"
    dark = " ".join(f"--d-{d}:{S.DOMAIN_COLOR[d][1]};" for d in doms)
    dark += " --fix-node:#e8e7e1; --deferred-node:#5f5e58;"
    sub = (
        f"{total} of the {len(all_rows)} failed tasks of the FDH fixes-on runs, by the fix that targets them "
        f"(fixes with at least {MIN_FIX_TASKS} tasks). Band colour = domain. Hover a bar or a band for details; "
        "the table view lists every fix."
    )
    desc = f"{total} failed tasks by domain, identification outcome, cause and the proposed fix that targets them."
    foot = (
        "Source: the collated <code>collated_failure_classes.csv</code> "
        "and <code>tau3-identity-fixes-mapping.csv</code> "
        "(next to this file); the plan is <code>tau3-identity-fixes-plan.md</code>. Task numbers: airline and retail = "
        "tau2 task ids, telecom = #n index in its data/tasks.json."
    )
    html = S.page(
        "FDH fixes-on failures by proposed fix",
        "FDH fixes ON · failed tasks by proposed fix",
        sub,
        tiles,
        [(dc[d], d) for d in doms] + [("var(--fix-node)", "in this plan"), ("var(--deferred-node)", "deferred")],
        desc,
        ["Proposed fix", "In plan"] + doms + ["All"],
        list(range(2, 3 + len(doms))),
        data,
        foot,
        light,
        dark,
    )
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "tau3-identity-fixes-sankey.html"), "w") as f:
        f.write(html)
    fix_label = {f: lab for f, lab, _, _ in FIXES}
    with open(os.path.join(out_dir, "tau3-identity-fixes-mapping.csv"), "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["domain", "task", "bucket", "stage", "proposed_fix", "fix_label"])
        for r in all_rows:
            fix = r["proposed_fix"]
            w.writerow([r["domain"], r["task"], r["bucket"], r["stage"], fix, fix_label[fix]])
    print(fix_total, "shown", total, "of", len(all_rows))


if __name__ == "__main__":
    main(*sys.argv[1:4])
