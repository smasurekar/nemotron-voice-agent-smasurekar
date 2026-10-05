# τ³ Voice: domain-specific code and prompt changes, and how to make them generic

Inventory of every **code-, config- and prompt-level** behaviour in the voice
prototypes that is specific to τ-bench domains or derived from τ-bench data.
For each one:

- the exact issue;
- why it is not generic and could cause problems with a Sierra (τ³ Voice
  leaderboard) submission;
- how to make it generic **without losing the reward it was added for**.

- Status as of 2026-10-05. Repo HEAD `7bc823a` on
  `dev/smasurekar/agent-prototypes`.
- Primary system:
  `src/prototypes/voice_delegation_hermes_agent/` (FDH, frontend delegation
  to Hermes), with default deployment `config/profiles/tau3_eval.yaml` +
  `config/gateway.yaml`.
- Secondary: `src/prototypes/voice_frontend_backend_agent/` (FBA). FDH
  inherits part of its normalization code and prompt catalog
  (`config/prompts.voice.yaml`).
- **Status of this doc: design proposal.** The schema rule builder, `auto`
  switches, `phone_format` feature and session-tools fingerprint described here
  are **unimplemented designs**. Generic does not mean proven safe: every item
  **requires validation** (§6), and no reward effect is claimed until a run
  shows it.
- Companion doc (benchmark-side rules and the full submission audit):
  `tau2-bench-smasurekar/misc/prototypes/voice-tau3-leaderboard-submission.md`.

---

## 1. Background: why this matters for a standard submission

From `sierra-research/tau2-bench` `docs/leaderboard-submission.md`:

- A **standard** τ-voice submission "presents one τ-voice-compatible agent
  interface … The voice system may use any internal product architecture
  behind that interface, including … internal prompts and tools, routing, and
  orchestration."
- But every standard submission must be "a model or system that was **not
  trained specifically on τ-bench tasks, rewards, or evaluation data**". It
  must also have "no access to hidden task goals, reference actions, evaluator
  state, or **other benchmark data** outside the standard agent interface".
- Sierra re-runs or parity-checks voice entries with its own voices and reads
  the trajectories. Our trajectories and (via `references`) our code will be
  visible to them.

So internal prompts, a tool relay and domain awareness are **allowed**. What
is risky is:

1. **Literal benchmark values** (task user IDs, reservation codes, surnames)
   in runtime prompts. This looks like task leakage.
2. **DB facts not stated by the benchmark** (storage formats, exact digit
   counts). This is "benchmark data outside the standard agent interface".
3. **Rules keyed to τ-bench tool names or domains and chosen from failure
   analysis on the scored `base` split.** This is test-set tuning.

### What "generic" means in this doc

A behaviour is generic if **all** of these hold:

- **G1. No benchmark values.** Examples are invented and do not resemble DB
  data (no `555-123-` prefix, no `_\d{4}` user IDs, no task codes or names).
- **G2. Driven by the interface, not by names.** Rules are selected from what
  the session actually provides: tool **schemas** (argument names, types,
  descriptions, and the examples in those descriptions) and the policy text.
  They are never selected from a hard-coded list of τ-bench tool names or
  domains.
- **G3. Preferences, not facts.** Wording like "prefer XXX-XXX-XXXX; if a
  lookup fails, try the other common format". Never "numbers are stored with
  dashes".
- **G4. Same behaviour in every domain.** A rule fires wherever its trigger
  (schema shape, error shape) appears, including mock, banking_knowledge and a
  customer's own deployment.
- **G5. Product-plausible.** It would exist in an NVIDIA voice agent that had
  never seen τ-bench.
- **G6. Write-safe and idempotent.** The agent never rewrites the arguments
  of a **write** tool (a tool that changes data). τ-bench scores the final DB
  state, so a wrong rewrite there directly costs reward. On read/lookup tools
  every rewrite is **idempotent**: a value that is already well-formed (for
  example copied from an earlier tool result) is never changed. Read vs write
  is decided generically from the schema: names starting with `get_`, `find_`,
  `search_`, `list_`, `lookup_`, `check_` or `calculate`, or descriptions
  starting "Get/Find/Search/List/Look up", count as **read**; **anything else
  counts as write** (the safe default).

### Provenance still has to be disclosed

Every item below (except pure comments) came from failure analysis of runs on
the τ³ `base` split. That is the same task set the leaderboard scores
(`tau3-failure-fixes-plan.md`, `tau3-identity-fixes-plan.md`,
`tau3-identity-fixes-mapping.csv`, sentinel tasks in
`frontend-delegation-hermes/runbook.md:415-416`). **Making a rule generic
does not erase where it came from.** The submission's `methodology.notes`
should say so, and Sierra should be asked whether the result counts as
standard. See [§7](#7-disclosure-text-for-methodologynotes).

### How "without hampering the reward" is achieved

The reward-relevant effect of each fix comes from a **mechanism**: format
correction, read-back and re-spell, loop guard, spelling hold. The τ-bench
specifics are only how each mechanism was **triggered** or **worded**. The
generic versions below keep the mechanism, widen the trigger to the general
case (a superset of the cases the original covered), and reword the text
without benchmark facts. On τ-bench they are **intended** to fire in the
same situations as the originals. That is a design goal, **not a demonstrated
result**. New hint targets, a broader retry guard, changed phone formatting and
different spelling-hold timing can all change the conversation, and through it
the outcome, even when no write-tool argument is rewritten. Reward equivalence
must be measured (§6) before it is claimed.

---

## 2. Summary table

Type: **P** = prompt text, **C** = code or config, **Cm** = comment or
docstring only (not sent to any model).

Priority:

- 🔴 must fix before a standard submission
- 🟡 should fix or make generic, and disclose
- 🟢 disclose only / cosmetic

| # | Item | Type | Location | Scope today | Priority |
|---|---|---|---|---|---|
| 1 | Example "ROSSI or IFOYYZ" in the backend spelling prompt | P | FDH `config/prompts.backend.yaml:27-28` | All domains | 🔴 |
| 2 | Example `mia_kim_4397` in the frontend normalization note | P | FDH `config/prompts.yaml:122-125` | All domains (incl. baseline) | 🔴 |
| 3 | Examples `mia_kim_4397` / `mia_fancy_4739` in `identifier_note_voice` | P | FBA `config/prompts.voice.yaml:372-381` (`normalization.transcript.frontend_note_key` in `voice/base.yaml`) | All domains (FBA; not rendered by FDH) | 🔴 |
| 4 | I1 telecom `<domain_notes>` (phone numbers "stored with dashes", `555-123-4567`) | P + C | FDH `config/prompts.backend.yaml:69-85`; `gateway.yaml:61` (`domain_notes: true`) | Telecom only | 🔴 |
| 5 | Domain detection by τ-bench tool names | C | FDH `config/gateway.yaml:63-69`; `backend/controller.py:255-269` | telecom / retail / airline | 🟡 |
| 6 | Tool-argument rule hard-wired to `get_user_details.user_id`, `format_hint: firstname_lastname_1234` | C | FDH `config/voice/tau3.yaml:16-27`; FBA `config/profiles/tau3_eval.yaml:63-75`, `backend_only.yaml:33-46` | Airline + retail | 🟡 |
| 7 | User-ID regex `^[a-z]+_[a-z]+_\d{4}$` with `on_invalid: answer_locally` | C | FDH `config/voice/tau3.yaml:28-29`; FBA `config/profiles/tau3_eval.yaml:74-75` | Airline + retail | 🔴 |
| 8 | Retry guard matching τ-bench's error string `^Error: .*\bnot found\b` | C | FDH `config/voice/tau3.yaml:30-33`; FBA `tau3_eval.yaml:76-80` | Rule tools | 🟢 |
| 9 | Recovery hints (I3/I4) attached to a hard-coded list of τ-bench identity tools | C | FDH `config/voice/tau3_recovery_hint.yaml:12-20`, `voice/tau3_fixes.yaml:10-13`; `tools/result_hints.py:46-67` | Airline + retail (telecom excluded on purpose) | 🔴 |
| 10 | I3 hint wording ("a zip code that is not five digits", "the other method your policy allows") | P | FBA `config/prompts.voice.yaml:410-422` (`identity_not_found_hint`) | Airline + retail | 🔴 |
| 11 | I4 hint wording | P | FBA `config/prompts.voice.yaml:424-432` (`identity_not_found_hint_words`) | Airline + retail | 🟡 |
| 12 | Spelling-hold "complete" pattern `^[A-Za-z0-9]{6}$` (airline reservation code) | C | FDH `config/profiles/tau3_eval.yaml:16-17`, `profiles/tau3_arm_m3_spelling.yaml:17` | All domains (written for airline) | 🟡 |
| 13 | Comments/docstrings quoting task values (`IFOYYZ`, `NQNU5R`, `K1NW8N`, `ROSSI`, `mia_kim_4397`) | Cm | FBA `normalization/transcript.py:11, 24`, `normalization/rules.py:243`; FDH `engine/spoken_text.py:12`, `config/voice/tau3_spelling.yaml:3-5`, `config/profiles/tau3_eval.yaml:17`, `tau3_arm_m3_spelling.yaml:17`, `config/delegation_agent.yaml:52`; FBA `tau3_eval.yaml:79` | — | 🟡 (before sharing code) |
| 14 | `<spoken_output>` / `<consent>` / `spelling_v2` backend prompt blocks (G2 / G4 / M3.4) | P | FDH `config/prompts.backend.yaml:26-35, 48-67` | All domains | 🟢 |
| 15 | Fixed greeting seeded into history ("Hi! How can I help you today?") | C | FDH `config/voice/tau3.yaml:9-11` | All domains | 🟢 |
| 16 | VAD override: 800 ms, `honor_client_values: false` | C | FDH `config/voice/base.yaml:17-20` | All domains | 🟢 |
| 17 | Profile / file naming `tau3_*` and the comments "tau3 fixes" | C | `config/profiles/tau3_*.yaml`, `config/voice/tau3*.yaml` | — | 🟢 |
| 18 | `transfer_to_human_agents` special case | C | FDH `backend/agent_like.py:282` | **Fake/stub backend only**, not on the eval path | 🟢 (no change) |
| 19 | `DOMAINS = ("mock", "airline", …)` | C | FBA `cli/tau2_gates/capture_session_updates.py:27` | Offline test CLI only | 🟢 (no change) |

---

## 3. Detailed table: issue / why not generic / generic solution

| # | What is the exact issue? | Why is it not generic, and what is the risk for a Sierra submission? | How to make it generic without hampering the reward |
|---|---|---|---|
| **1** | `spelling_v2` block in the backend system prompt: "Spoken identifiers and names may arrive spelled out; spelled letters are already joined **(for example ROSSI or IFOYYZ)**." | `IFOYYZ` is a reservation in airline tasks 9 and 37. Task 9's instruction is "cancel two of your upcoming reservations (IFOYYZ and NQNU5R)". "Rossi" appears 13× in airline `tasks.json`, 15× in retail `tasks.json` and in 33 user IDs across the airline/retail DBs. Literal task values in a runtime prompt look like **task leakage** to a reviewer, even though they are only examples. Added in commit `36df307` (2026-09-30), so it is clearly part of the τ³ tuning. | Replace with invented values that look nothing like DB data, e.g. "(for example **QUENTIN** or **XB7TP2**)". The instruction is the same (letters arrive joined; read back and re-spell on failure), so behaviour and reward are unchanged. Better still, use a non-ID example, e.g. "(for example a surname such as QUENTIN, or a code such as XB7TP2)". |
| **2** | Frontend `normalization_note`: "Identifiers in user turns may already be written out **(for example mia_kim_4397** or a booking code)." | `mia_kim_4397` is the user ID of airline task 24 (and in `airline/db.json`). It is also in the **baseline** prompt (commit `3a7e04a`), so even the control arm carries it. Same leakage concern as #1. Its shape (`first_last_4digits`) also teaches the τ-bench DB format. | Use an invented identifier **in the benchmark's documented shape**: τ-bench's own tool docs use `sara_doe_496`, but better not to copy that either. Use e.g. "**jordan_lee_82**" or a generic "**a user ID such as an underscore-separated name and number**". The note only tells the frontend to copy identifiers verbatim, and any example does that. |
| **3** | FBA `identifier_note_voice` (appended to the frontend prompt by `normalization.transcript.frontend_note_key: identifier_note_voice` in `voice/base.yaml`): "such as **mia_kim_4397** … 'user ID mia_kim_4397 (latest attempt: **mia_fancy_4739**)'". | Same task-24 user ID, plus a near-miss variant modelled on the real ASR error seen in the run. Runtime prompt on the **FBA** path. FDH's frontend renders `prompts.yaml:normalization_note` instead (`engine/turn_manager.py:359`), so on FDH this key is inherited config but not rendered. FDH does load the **same catalog** (`prompts.voice.yaml`, via `voice.agent.prompts_path`) for the tool-argument and hint texts (`server.py:77-90`), so #10/#11 are live on FDH. Fix it anyway so the shared catalog is clean. | Replace with invented values: "such as jordan_lee_82 … 'user ID jordan_lee_82 (latest attempt: jordan_leigh_28)'". The behaviour (keep the earlier complete ID, append the later attempt) is unchanged. |
| **4** | I1 `<domain_notes>` for telecom: "Phone numbers **are stored with dashes**, in the form **555-123-4567**. Always pass a phone number to a tool in that form … retry once with those same digits in XXX-XXX-XXXX form … read the digits you heard back in groups (for example "555, 123, 4567")". Switched on by `prompt_features.domain_notes: true` and rendered only when the gateway detects telecom (#5). | (a) "**are stored** with dashes" states a **DB fact**: telecom `get_customer_by_phone` does an exact string match against `db.toml` values like `"555-123-2001"`, and neither the tool docstring ("The phone number to search for.") nor `main_policy.md` gives a format. This is "benchmark data outside the standard agent interface". (b) The example `555-123-4567` shares the `555-123-` prefix with **every** DB number. (c) It is **telecom-only**, gated on a τ-bench tool name. (d) It was chosen from "144 failed undashed `get_customer_by_phone` calls in the telecom run" (`tau3-identity-fixes-plan.md:209-215`), which is **test-set tuning**. The underlying failure is real and every voice agent hits it: the user simulator holds `555-123-2002`, speaks it, and ASR returns `5551232002`. | Turn it into a **domain-agnostic phone-number rule for lookups** in `backend_system` (always on; no `<domain_notes>`; full text in §4.4). **Lookups:** pass ten digits as XXX-XXX-XXXX unless the tool's description shows another format; if the lookup fails, retry once in the other common form, never changing digits; then read back and ask. **Writes** (any tool that changes data, e.g. banking `log_verification(…, phone_number, …)`): pass the number **exactly as the user confirmed it or as a tool returned it**, with no reformatting. Why the split: banking data contains `0412-555-947` (10 digits, grouped 4-3-3), so a blanket "ten digits → XXX-XXX-XXXX" would write `041-255-5947` into scored state. **Open evidence question:** whether the backend receives the user's spoken digit grouping after ASR and ITN (e.g. `555 123 2002` vs `5551232002`) has not been checked. Inspect logged backend inputs from earlier telecom runs before relying on grouping anywhere. **Reward:** intended to keep the I1 gain on telecom lookups; requires validation. Optional **code** layer: for **read** tools only (G6), when an argument whose **schema name** contains `phone` gets "not found", retry once in the other format (dashed ↔ digits-only). Never reformat upfront; never touch write-tool arguments. |
| **5** | `gateway.yaml` `domains:` maps τ-bench tool names to domain labels (`telecom ← get_customer_by_phone`, `retail ← find_user_id_by_name_zip / find_user_id_by_email`, `airline ← get_reservation_details`). `backend/controller.py:255-269` uses it to pick `backend_domain_notes[<domain>]` and logs `backend_domain`. | Recognising the domain from the supplied tool schemas is **allowed**: it is information from the standard interface. The problem is only **what it triggers** (#4). A hard-coded list of τ-bench tool names is an obvious "built for τ-bench" marker in code Sierra will see, and it makes behaviour differ across domains in a way a generic agent would not. | Once #4 is generic, **no domain-specific note remains**, so: (a) remove the `domains:` map and `backend_domain_notes`, or set `domain_notes: false` and leave the map empty; and (b) for fingerprinting, replace the `backend_domain` label with a **new `session_tools_sha256`**: a hash of the sorted tool schemas from `session.update`. This field **does not exist yet**. `backend_catalog_sha256` is something else: it hashes the backend **prompt catalog** (`sidecar/templates.py:57`, logged at `backend/controller.py:249`), not the session's tools. No reward impact once #4 is moved into the generic prompt. If you want to keep a domain-notes *mechanism* for products, keep the code but ship **no τ-bench entries** in the config used for submission. |
| **6** | The tool-argument normalisation rule is configured for **one τ-bench tool by name**: `tool: get_user_details`, `argument: user_id`, `format_hint: firstname_lastname_1234`, `spoken_form`, `strip: " .-"`, `collapse_separators: "_"`, `case: lower`. Executed in `tools/relay.py` `_screen` before the call reaches τ-bench. | Selecting the rule by a **τ-bench tool name** is not generic (G2). `format_hint` encodes the DB shape (`_1234`, four digits), which is more than the benchmark tells the agent: τ-bench's own docs say `'sara_doe_496'` (airline `tools.py:205, 392, 493`; retail `tools.py:49, 385`). It also only covers `get_user_details`, while the same `user_id` argument appears in other tools. The canonicalisation itself (strip spaces and dots, join with `_`, lowercase) is reasonable. | **Make the rule schema-driven.** At `session.update`, scan every tool's parameters. For any **string argument whose name ends in `_id`** (or is `id`) **and whose description contains an example** (`such as 'xxx'`), derive the rule from that example: separators, case (all lowercase → `case: lower`; all uppercase → `upper`; mixed → keep), and character classes per segment. **Apply it only to read/lookup tools (G6).** Write tools (`book_reservation.user_id`, `modify_pending_order_payment.payment_method_id`, …) are never rewritten; the backend normally copies their values from earlier lookup results anyway. Rules for all read tools: **idempotent** (a value that already matches the example's shape is left alone); **strip only characters that do not appear in the example** (so `_` in `sara_doe_496` and `#` in `#W0000000` are kept, while spaces, dots and dashes are stripped); never add or change letters or digits. Set `format_hint` from the example's *shape* (e.g. `name_name_digits`), not from DB data. **Scope note:** in τ-bench only airline and retail schemas contain examples. Telecom, banking_knowledge and mock have none, so the rule is active everywhere but only has an effect there. **Reward:** on τ-bench this produces the same canonicalisation for `get_user_details.user_id` (`sara_doe_496` → lowercase, `_` separators), so the same lookups succeed. Write-tool values are unchanged, as today. Disclose it as "schema-example-driven identifier normalisation on lookup tools". |
| **7** | `pattern: '^[a-z]+_[a-z]+_\d{4}$'` with `on_invalid: answer_locally`. A `user_id` that does not match **exactly 4 digits** is never sent to τ-bench; the server answers locally ("Not looked up: … Read back what you heard…"). | "Exactly 4 digits" is a **DB fact**: all 500 airline and all 500 retail user IDs have exactly four digits, but the benchmark documents `sara_doe_496` (three). The rule would **reject the benchmark's own documented example**. It is the clearest case of tuning to evaluation data rather than to the interface, and a reviewer comparing it with the tool docstrings will see that immediately. | Reject locally **only when a value is clearly incomplete**, never because its shape differs from the example. "Clearly incomplete" means the example has N separator-delimited segments and the value has fewer, or has an empty segment (`name_`, `name__12`), or the example's final segment is digits and the value has none. For `sara_doe_496` this gives `^[a-z]+_[a-z]+_\d+$`-equivalent behaviour. **Never reject over a missing non-alphanumeric prefix**: retail's `'#W0000000'` would otherwise make the server reject a spoken `W1234567`, and the user cannot pronounce "#". In that case **restore the literal symbol** from the example (`#` + value). That is a format fix, not a guess, because it adds no letters or digits. Never add letters or digits (e.g. do not add the `W`). Read tools only (G6). **Reward:** the local answer was added to catch *incomplete* IDs, where ASR dropped the number or a part (`tau3-failure-fixes-plan.md` M3.3). Those still fail `\d+` (no digits, missing segment), so the read-back flow is kept. The only case lost is a wrong *count* of digits (3 or 5 instead of 4). That now goes to τ-bench, returns "not found", and is caught by the retry guard (#8) and recovery hint (#9). It costs one extra read-only tool call, which does not change τ-bench's DB-state reward. |
| **8** | `retry_guard.permanent_failure_pattern: '^Error: .*\bnot found\b'`, `scope: rules`. An identical repeat of a call that already failed with "not found" is answered locally with `tool_call_already_failed`. | It matches **τ-bench's exact error text format** (`Error: User … not found`), but "not found" is the most common lookup-failure phrase in any API. The mechanism (don't repeat an identical failed call) is a standard loop guard. Low risk. Mainly a disclosure item, because it means some agent calls never reach τ-bench (visible in trajectories as a call/no-call mismatch only in our own logs). | Make it **case-insensitive and anchor-free**: `(?i)\b(not found\|no such\|does not exist\|unknown)\b`. Change `scope` from `rules` to "any tool whose arguments were normalised or that is read-only" (or simply all tools, since identical-args repeats after a permanent failure are never useful). **Reward:** identical on τ-bench (the same strings match). Disclose. |
| **9** | `result_hints` (I3/I4) are configured with a **hard-coded list of τ-bench identity tools**: `tools: [find_user_id_by_name_zip, find_user_id_by_email, get_user_details]   # retail, airline; not telecom`. On a "not found" result, `tools/result_hints.py:46-67` appends a recovery note to the backend's copy of the tool result (τ-bench's recorded result is unchanged). | Selected by **τ-bench tool names** and deliberately **excluded from telecom** because I1 covered it. That is per-domain behaviour engineered on base-split failures (`tau3-identity-fixes-mapping.csv`: airline 14, 15, 16, 17, 25, 37 → I4; retail 0, 3, 4 → I3). The mechanism (adding a hint to a failed lookup inside our own system) is allowed. The **targeting** is the problem. | **Target by result, not by tool name or argument list.** Fire the hint when a **read** tool (G6) returns a not-found error whose text **names a person-type entity**: a user / customer / member / client / profile **before** a not-found phrase, or `No records found` over a person table (exact regex, plus a test table over all τ-bench not-found strings, in §4.7). The hint is about identifying the user, so the trigger is "the user/customer could not be found", whichever tool reported it. **Why not select by argument names:** `user_id` / `customer_id` also appear in non-identity tools (telecom `get_bills_for_customer`, `get_data_usage`; banking `get_credit_card_transactions_by_user`, `get_referrals_by_user`), so an argument-name selector over-matches. Product, order, line, plan, bill and transaction not-founds do not name a person entity, so they get no hint. **Regex-tested against the real error strings** (§4.7): it matches the user/customer not-found messages in all five domains, including banking's `No records found in 'users'.`, and none of the bill, line, product, card or transaction messages. An unknown user passed to a non-identity tool (e.g. a bad `customer_id` in `get_bills_for_customer`) **will** get the hint. That is intended, since the user wasn't found. **Reward:** intended to cover the original three tools' misses; telecom and banking identity misses are new firing points and require validation. Keep `max_hints: 3` and `count_local_invalid: true`. |
| **10** | I3 hint text (`identity_not_found_hint`): "…Ask about one field at a time, starting with the most doubtful: **a zip code that is not five digits**, a name you only heard as a word, or a field whose versions differ… After two misses with one method, **offer the other method your policy allows**." | "A zip code that is not five digits" is retail-shaped (`find_user_id_by_name_zip`). "The other method your policy allows" assumes the **retail/airline policies' two authentication methods** (email, or name + zip), learned from those policies and failures. It is phrased for the τ-bench identity flow, not for a general lookup failure. | Reword in general terms: *"…starting with the most doubtful: **a number or code with an unusual length for its kind (for example a postal code or phone number)**, a name you only heard as a word, or a field whose versions differ… If several attempts fail and **your instructions allow another way to identify or look up the user, offer it**."* **Reward:** the same guidance for the same cases (a 4- or 6-digit zip is still "unusual length"; the policy still lists the email method). It just isn't spelled out for retail. |
| **11** | I4 hint text (`identity_not_found_hint_words`): word-assisted spelling ("S as in Sam"), "say 'double' for repeated letters", "After two misses with one method, offer the other method your policy allows." | Mostly generic. The NATO-style "S as in Sam" technique is standard call-centre practice. Only the last sentence carries the τ-bench-identity framing (same as #10). Its targeting depends on #9. | Change the last sentence as in #10. Keep the rest. **Reward:** unchanged. |
| **12** | Spelling hold `complete_patterns: ['^[A-Za-z0-9]{6}$']   # airline reservation code (e.g. IFOYYZ, K1NW8N)`. A spelled run matching 6 alphanumerics is treated as complete and not held. | The length **6** matches all 2000 airline reservation IDs. It was chosen for airline and the comment names two real task codes (`IFOYYZ` = tasks 9/37, `K1NW8N` = tasks 14/23). It applies globally but encodes one domain's ID format. | **Derive "complete" patterns from schema examples** (same mechanism as #6), but **only from identifier arguments**: argument names `*_id` / `*_ids` / `id`. The example value itself is not used as a filter. Do **not** derive from every example. τ-bench schemas also contain `'CA'`, `'SFO'`, `'John'`, `'Doe'`, `'12345'`, `'2 + 2'`, `'San Francisco'`, `'2024-01-01'`. A `'CA'` pattern would mark a user spelling "C, A, R, L" as complete after "CA" and split the turn. The filter keeps `ZFA04Y`, `8JX2WO`, `#W0000000`, `6086499569`, `1008292230`, `gift_card_0000000`, `credit_card_7815826`, `sara_doe_496`. Build each pattern from the example's **shape** (length plus character classes). Airline then gets `^[A-Z0-9]{6}$` **from τ-bench's own documented example**, which is legitimate interface information. Retail gets `#W` + 7 digits and 10-digit item IDs automatically. Telecom, banking and mock have no examples, so they get no patterns (the hold then works as it does without patterns). Remove the comment with task codes. **Reward:** identical behaviour on airline (same pattern), and possibly better on retail. Fallback if not implemented: drop `complete_patterns`. This only costs up to 1.5 s of hold latency on codes, with no expected pass^1 impact. |
| **13** | Comments and docstrings quote real task values: `transcript.py:11` (`mia_kim_4397`), `transcript.py:24` ("I, F, O, Y, Y, Z and N, Q, N, U, five, R" → "IFOYYZ and NQNU5R", which is literally airline task 9), `rules.py:243`, `engine/spoken_text.py:12`, `voice/tau3_spelling.yaml:3-5` ("ROSSI, IFOYYZ"), `profiles/tau3_eval.yaml:17` and `tau3_arm_m3_spelling.yaml:17` ("IFOYYZ, K1NW8N"), FBA `profiles/tau3_eval.yaml:79` (`mia_kim_4397` error string), `delegation_agent.yaml:52` ("for airline codes"). | Not sent to any model, so **no effect on a run**. But the code is linked in `references` (required for custom, likely requested for standard), and Sierra will read it. Task values in docstrings undermine the "not tuned on τ-bench data" claim. | Replace with the same invented values used in #1-#3 (`QUENTIN`, `XB7TP2`, `jordan_lee_82`, "X, B, seven, T, P, two" → "XB7TP2"). Update the unit tests that assert on those strings. **Reward:** none (no runtime effect). |
| **14** | Backend prompt blocks: `spelling_v2` (M3.4: on a failed lookup read back and ask to spell; don't re-ask for an ID once the account is found), `<spoken_output>` (G2: ≤2 sentences, "Do not read out product or item IDs unless the user must confirm by them", plus a confirmation override before booking/modification/cancellation/return/exchange), `<consent>` (G4: a yes counts only if the question was heard in full; spell back written names). | The wording is **generic** customer-service voice guidance and does not state benchmark facts. "Booking, modification, cancellation, return or exchange" lists generic CS action types. Only the **provenance** is τ-bench: they were designed from base-split failure analysis (`tau3-failure-fixes-plan.md` G2/G4/M3.4). | No text change is required beyond #1's example. Optionally reword "return or exchange" → "or any other change to an order, booking or account" to make it fully domain-neutral. **Disclose** the provenance. **Reward:** unchanged. |
| **15** | `seed_history_with_client_greeting: "Hi! How can I help you today?"`. τ-bench's text greeting is seeded into both agents' history, and the server's own greeting is disabled. | This is **protocol adaptation**: τ-bench greets the user in text, so our agent must know what was "said". It is the kind of "transport or protocol adapter" the rules allow. The literal string is τ-bench's. | Keep it. It is **uniform across all domains but specific to τ-bench**: the string is τ-bench's own greeting, so it is not generic in the product sense. Treat it as part of the protocol adapter and **disclose** it. Optionally read the greeting from the client (e.g. the first assistant item in the conversation, if τ-bench sends one) instead of hard-coding the string. **Reward:** unchanged. |
| **16** | `turn_detection.silence_duration_ms: 800`, `honor_client_values: false`. The client's 500 ms is ignored. | Not domain-specific, and allowed as an internal voice-system choice. It **affects latency and interaction metrics**, which Sierra recomputes, and it was tuned during the τ³ campaign (V11). | Keep it and **disclose** it in methodology. If you want to honour the client, test 500 ms against spelling turns first, because 800 ms protects spelled IDs and lowering it may cost reward. |
| **17** | Profiles and voice configs are named `tau3_eval`, `tau3_fixes`, `tau3_recovery_hint`, `tau3_spelling`, and the comments call them "tau3 failure fixes". | Cosmetic, but in shared code it advertises benchmark-targeted tuning. More importantly, the **submission must use one frozen profile for all domains**, and per-arm profiles (`tau3_arm_*`) invite swapping. | For submission, create **one** profile (e.g. `realtime_eval.yaml` + `gateway.eval.yaml`) that contains the generic versions of #1-#16. Freeze it at a tagged commit and record `backend_system_sha256` / `backend_catalog_sha256` per session. Keep the `tau3_arm_*` profiles out of the submission tag, or leave them clearly documented as historical experiments. |
| **18** | `backend/agent_like.py:282` skips `transfer_to_human_agents` ("tau2 ends the simulation on that one") when choosing a default tool. | Only in the **fake/stub backend** used for tests and `--stub-backend`. It is not on the eval path (Hermes runs the real tool loop). | No change needed for submission. Optionally drop the comment's τ-bench reference. |
| **19** | `cli/tau2_gates/capture_session_updates.py:27` lists τ-bench domains. | Offline test CLI that imports tau2 to capture fixtures. Not part of the running server. | No change needed. |

---

## 4. Proposed generic replacements (ready to paste)

### 4.1 #1: `prompts.backend.yaml` `spelling_v2`

```yaml
  {% if features.spelling_v2 -%}
  Spoken identifiers and names may arrive spelled out; spelled letters are already joined
  (for example QUENTIN or XB7TP2). If a lookup by an identifier or a name fails, do not retry
  with a guess or a variant: read back what you heard, letter by letter, and ask the user to
  spell it. If you already found the user's account in this conversation, do not ask for
  their identifier again.
```

### 4.2 #2: `prompts.yaml` `normalization_note`

```yaml
normalization_note: |
  Identifiers in user turns may already be written out (for example jordan_lee_82 or a booking code).
  If an earlier user turn gave a complete identifier and a later one gives only part of it, the user
  is continuing or correcting the same identifier: delegate the turn so the backend can use it.
```

### 4.3 #3: FBA `prompts.voice.yaml` `identifier_note_voice`

Replace `mia_kim_4397` → `jordan_lee_82` and `mia_fancy_4739` → `jordan_leigh_28`.

### 4.4 #4: replace I1 with a generic phone rule in `backend_system` (always on)

Behind a new generic feature, e.g. `features.phone_format`:

```yaml
  {%- if features.phone_format %}

  <phone_numbers>
  When you look something up by phone number, write a ten-digit number as XXX-XXX-XXXX unless
  the tool's description shows a different format. If the lookup fails, retry it once with the
  same digits in the other common form (with or without dashes) before asking the user; this is
  a format correction, not a guess. Never add, remove or change digits. If that also fails, read
  the digits back in groups and ask the user to confirm the number.
  When a tool will store or change a phone number, pass it exactly as the user confirmed it or
  as a tool returned it; do not reformat it.
  </phone_numbers>
  {%- endif %}
```

Why lookups and writes are treated differently: banking_knowledge data
contains `0412-555-947` (10 digits, grouped 4-3-3), and banking's
`log_verification(…, phone_number, …)` is a **write** tool. A blanket "ten
digits → XXX-XXX-XXXX" would store `041-255-5947`. For lookups, a wrong first
format costs at most one retry; for writes it changes scored state.

**Unverified:** an earlier draft relied on users' spoken 3-3-4 grouping
reaching the backend. Whether grouping survives ASR + ITN (`555 123 2002` vs
`5551232002`) has **not** been checked. Inspect logged backend inputs from
earlier telecom runs before relying on it. The rule above deliberately does
not depend on it.

Optional code layer (read tools only, G6): when a lookup tool with an
argument whose schema name contains `phone` returns not-found, retry once
with the other format (dashed ↔ digits-only). Never reformat upfront, and
never rewrite write-tool arguments.

Then delete `backend_domain_notes.telecom`, set `domain_notes: false`, and empty
`domains:` in the submission gateway config.

### 4.5 #6 / #7: schema-driven identifier rule (code change)

Sketch for `normalization/arguments.py` plus a new rule builder called at
`session.update`:

```text
READ_PREFIXES = ("get_", "find_", "search_", "list_", "lookup_", "check_", "calculate")

def is_read_tool(tool):                                  # G6: unknown => write (safe default)
    return tool.name.startswith(READ_PREFIXES) or \
           tool.description.lstrip().lower().startswith(("get", "find", "search", "list", "look up"))

for tool in session.tools:
    if not is_read_tool(tool): continue                  # never rewrite write-tool arguments
    for arg, spec in tool.parameters.properties:
        if spec.type != "string": continue
        if not (arg == "id" or arg.endswith("_id")): continue
        example = first_quoted_example(spec.description)   # "such as 'sara_doe_496'"
        if example is None: continue                     # telecom/banking/mock: no examples -> no rule
        rule = Rule(
            tool=tool.name, argument=arg,
            label=humanize(arg),                            # "user id"
            format_hint=shape_of(example),                  # "name_name_digits"
            spoken_form=True,
            strip=chars_absent_from(example, " .-"),        # '#' and '_' kept when the example has them
            collapse_separators=separator_of(example),      # "_" for sara_doe_496, None for ZFA04Y
            case=case_of(example),                          # lower / upper / keep (mixed)
            restore_prefix=literal_symbol_prefix(example),  # "#" for '#W0000000' (symbols only, never letters/digits)
            incomplete=incomplete_check(example),           # fewer segments, empty segment, or missing final digits
            on_invalid="answer_locally",                    # only when incomplete(...) is true
            idempotent=True,                                # a value already matching the shape is never changed
        )
```

`incomplete_check` rejects only **clearly incomplete** values: fewer
separator segments than the example, an empty segment, or no digits where
the example's final segment is digits. It **never** rejects for differing
lengths, letters vs digits, or a missing symbol prefix (that prefix is
restored instead). The strict shape (exact lengths) is used only for the
spelling-hold complete pattern (#12), which never blocks a call.

What this produces on τ-bench (from the actual schemas):

| Domain | Read-tool `*_id` args with an example | Rule |
|---|---|---|
| airline | `get_user_details.user_id` (`sara_doe_496`), `get_reservation_details.reservation_id` (`8JX2WO`) | lower + `_` / upper, no separator |
| retail | `get_user_details.user_id`, `get_order_details.order_id` (`#W0000000`), `get_product_details.product_id`, `get_item_details.item_id` (`6086499569`) | lower + `_` / restore `#`, upper / digits |
| telecom, banking_knowledge, mock | none (no examples in the schemas) | none |

Write tools (`book_reservation`, `cancel_*`, `modify_*`, `update_*`,
`exchange_*`, `return_*`, `send_*`, `log_verification`, `change_user_email`,
…) get no rule.

Plain config fallback, if the code change is not done:

```yaml
      - tool: get_user_details
        argument: user_id
        label: user ID
        format_hint: name_name_digits
        spoken_form: true
        strip: " .-"
        collapse_separators: "_"
        case: lower
        pattern: '^[a-z]+_[a-z]+_\d+$'
        on_invalid: answer_locally
```

This removes the DB fact (`\d{4}`) but is still keyed on a τ-bench tool name,
so the schema-driven version is preferred.

### 4.6 #8: generic retry guard

```yaml
    retry_guard:
      enabled: true
      scope: all
      permanent_failure_pattern: '(?i)\b(not found|no such|does not exist)\b'
```

### 4.7 #9: generic result-hint targeting

```yaml
    result_hints:
      enabled: true
      tools: auto            # new: any read tool (G6); selection is by the result text below
      # person entity BEFORE the failure phrase (so "Bill … not found for customer …" does not match),
      # or an empty query result over a person table ("No records found in 'users'.")
      failure_pattern: "(?i)\\b(users?|customers?|members?|clients?|profiles?)\\b[^.]*\\b(not found|no such|does not exist)\\b|\\bno (records|results|matches) found\\b[^.]*\\b(users?|customers?|members?|clients?|profiles?)\\b"
      count_local_invalid: true
      max_hints: 3
      message_key: lookup_not_found_hint
      escalate_message_key: lookup_not_found_hint_words
```

Selection is by **what was not found**, not by tool or argument names.
An earlier draft selected tools whose arguments included `user_id` /
`customer_id`. That over-matched non-identity tools such as telecom
`get_bills_for_customer` / `get_data_usage` and banking
`get_credit_card_transactions_by_user`, so it was dropped.

Expected behaviour on τ-bench (**to be confirmed by unit tests against the
real error strings**; not yet verified end to end):

- **Hint:** a user/customer lookup that fails, e.g. telecom
  `Customer with phone number … not found`, banking
  `Error: User with ID '…' not found.`, airline/retail user lookups (check the
  exact retail `find_user_id_*` error text). Also any read tool that fails
  *because the user/customer is unknown*. That is intended.
- **No hint:** product, item, order, reservation, line, plan, bill,
  transaction or card not-founds (no person entity in the text), and any
  write tool.

**Pattern test against τ-bench's actual not-found strings.** All
`not found` / `no records found` messages in the five domains' `tools.py` and
banking `db_query.py` were collected, then the pattern above was run on them:

| Domain | Hint | No hint |
|---|---|---|
| airline | `User {user_id} not found` | flight, flight-on-date, payment method, reservation |
| retail | `User not found` (all three user lookups) | item, new item, order, payment method, product, variant |
| telecom | `Customer with ID … not found`, `Customer with phone number … not found`, `Customer … not found` | bill, `Bill … not found for customer …`, device, line, `Line … not found for customer …`, overdue bill, plan |
| banking_knowledge | `Error: User '…' not found.`, `Error: User with ID '…' not found.`, `No records found in 'users'.` (name/email lookups) | account, checking/source/destination account, credit card account, debit card, transaction, `No records found in 'transactions'.` |
| mock | `User … not found` | task |

Known false positive: `Error: Database 'users' not found.` (a configuration
error from banking `query_database_tool`). It is harmless and rare. This was
a regex test on the message templates only; **end-to-end behaviour still
requires validation**. An earlier version of the pattern missed banking's
name/email misses (`No records found in 'users'.`); the second alternative
fixes that.

### 4.8 #10 / #11: hint wording

```yaml
lookup_not_found_hint:
  content: |
    [Not found. At least one value you sent is wrong, possibly one the user spelled: speech
    recognition can drop or change letters even in a spelling. Keep the other fields for the
    next lookup, but none is confirmed until a lookup succeeds. Ask about one field at a time,
    starting with the most doubtful: a number or code with an unusual length for its kind (for
    example a postal code or phone number), a name you only heard as a word, or a field whose
    versions differ. Read back only that field, letter by letter or digit by digit, and ask the
    user to confirm or spell it, saying "double S" for repeated letters. Build the next lookup
    from the latest value of each field, taken from what the user spelled rather than a word you
    heard; if that spelling differs from an earlier version, ask which is right first. Never fill
    in a missing letter or digit. If several attempts fail and your instructions allow another
    way to identify or look up the user, offer it.]

lookup_not_found_hint_words:
  content: |
    [Not found again. Keep the other fields for now; a field spelled before can still be the
    wrong one. Ask the user to spell only the field most likely wrong once more, using a word
    for each letter, for example "S as in Sam, A as in apple", and to say "double" for
    repeated letters. Use the first letter of each word; if a word and the spoken letter
    disagree, ask which is right. Read that field back once before you look it up. If several
    attempts fail and your instructions allow another way to identify or look up the user,
    offer it.]
```

(The "Ana after Anna" example in the original I3 text is fine as generic, but
it was dropped above for brevity; keep it if wanted.)

### 4.9 #12: spelling hold

```yaml
delegation:
  spelling_hold:
    complete_patterns: auto   # new: derived from identifier examples in the session's tool schemas
```

Only examples of **identifier arguments** are used: argument names `*_id` /
`*_ids` / `id`. The example value is **not** used as a filter. An earlier
draft also admitted "no-space values of 5+ characters with a digit", but that
wrongly admits `12345` (zip) and `2024-01-01` (date).

| Kept (→ pattern) | Excluded (would split spelled names/words) |
|---|---|
| `ZFA04Y`, `8JX2WO` → `^[A-Z0-9]{6}$`; `#W0000000` (`#`, `W`, then 7 digits) → `^#?[A-Z]\d{7}$`; `6086499569`, `1008292230` → `^\d{10}$`; `gift_card_0000000`, `credit_card_7815826`, `sara_doe_496` → shape patterns | `CA`, `SFO`, `JFK`, `John`, `Doe`, `12345`, `2 + 2`, `San Francisco`, `123 Main St`, `2024-01-01`, `basic_economy`, `one_way` |

Fallback: `complete_patterns: []`.

---

## 5. Uniform across all domains (no tool-name or domain gating)

**Goal:** one code path and one config in which **every behaviour is active
in every domain**. Nothing is switched on by a τ-bench tool name or a
detected domain label. Today several fixes are selected by name
(`get_user_details`, `find_user_id_by_name_zip`, `find_user_id_by_email`,
`get_customer_by_phone`, `get_reservation_details`) or by the `domains:`
map. A reviewer reads that as "built for τ-bench". It also makes the agent
behave differently per domain for reasons the agent was never told.

**This is possible for every item.** Each name-based trigger can be replaced
by a generic signal that is present in any session.

### 5.1 The three generic trigger types

| Trigger type | What it is | Why it is generic |
|---|---|---|
| **A. Always-on prompt text** | A general instruction in `backend_system` (or the frontend prompt), rendered identically in every session | Same text for every domain and every client |
| **B. Tool schemas from `session.update`** | Argument names, types, and the examples in parameter descriptions (τ-bench's own docs: `'sara_doe_496'`, `'ZFA04Y'`, `'#W0000000'`, `'6086499569'`, `'12345'`) | Every client sends tool schemas; this is the standard agent interface, not benchmark data |
| **C. Tool-result shape** | Generic error language in a result (`not found`, `no such`, `does not exist`) | Any API returns errors like this |

### 5.2 Mapping: name-based today → uniform

| # | Behaviour | Today (name/domain-gated) | Uniform version (all domains) | Trigger type | Code or config |
|---|---|---|---|---|---|
| 4 | Phone-number format + one format retry | `<domain_notes>` for **telecom only**, chosen via `get_customer_by_phone` in `gateway.yaml domains:` | `<phone_numbers>` block in `backend_system`, **always rendered** (§4.4): lookups: XXX-XXX-XXXX for ten digits plus one format retry; writes: pass as confirmed or returned, no reformatting. Optional code layer: on not-found from a **read** tool with a `phone*` argument, retry once in the other format. **No upfront reformatting; never on write tools** (banking `log_verification`; data has `0412-555-947`) | A (+ B + C for the code layer) | Prompt: config only. Code layer: small addition to `normalization/arguments.py` |
| 5 | Domain detection | `domains:` maps 3 τ-bench tool names → `telecom`/`retail`/`airline`; drives `<domain_notes>` and the `backend_domain` log | **Removed.** Nothing depends on a domain any more. Fingerprint sessions by a **new** `session_tools_sha256` (hash of the sorted `session.update` tool schemas; not yet implemented). `backend_catalog_sha256` hashes the prompt catalog, not the tools | — | Config only (empty `domains:`, `domain_notes: false`, delete `backend_domain_notes`) |
| 6 | Identifier canonicalisation (strip, join with `_`, case) | One rule: `tool: get_user_details`, `argument: user_id` | Built at `session.update` for **read tools only** (G6): for each string argument `*_id` / `id` whose description has a quoted example, derive separators, case and shape from that example (§4.5). **Idempotent**; strips only characters absent from the example (`#`, `_` kept). On τ-bench this covers airline `get_user_details.user_id` and `get_reservation_details.reservation_id`, plus retail `get_user_details.user_id`, `get_order_details.order_id`, `get_product_details.product_id` and `get_item_details.item_id`. Telecom, banking and mock have no examples, so the rule has no effect there. Write tools (`book_reservation.user_id`, …) are never rewritten | B | Code (rule builder) |
| 7 | Local "incomplete identifier" read-back | `pattern: '^[a-z]+_[a-z]+_\d{4}$'` on that one argument | Reject locally **only clearly incomplete values** (fewer segments than the example, an empty segment, missing final digits). Never reject over length or a missing symbol prefix: **restore** a literal symbol from the example (`#` for `#W0000000`), never letters or digits. Read tools only | B | Code (same builder); config fallback `\d+` |
| 8 | Retry guard (no identical repeat of a permanently failed call) | `scope: rules` (only the rule tools); pattern anchored on τ-bench's `^Error: …` | `scope: all` tools; case-insensitive generic not-found pattern (§4.6) | C | Config (+ a `scope: all` option if not yet supported) |
| 9 | Recovery hints on failed lookups (I3 → I4 escalation) | `tools: [find_user_id_by_name_zip, find_user_id_by_email, get_user_details]`, **telecom excluded** | `tools: auto` over read tools; fire only when the not-found text **names a person entity** (user / customer / member / client / profile). **Not** selected by argument names: `user_id`/`customer_id` also appear in billing, usage and transaction tools (§4.7) | C (+ B for read/write) | Code (`tools/result_hints.py` selector) + config |
| 10, 11 | Hint wording | Retail/airline framing ("zip code not five digits", "the other method your policy allows") | Generic wording (§4.8), the same text in every domain | A | Config (prompt catalog) |
| 12 | Spelling hold "complete" patterns | `'^[A-Za-z0-9]{6}$'` written for airline codes, applied globally | `complete_patterns: auto` from **identifier arguments only** (`*_id` / `*_ids` / `id`; the example value is not a filter). Airline gets `^[A-Z0-9]{6}$` from `'ZFA04Y'`; retail gets `^#?[A-Z]\d{7}$` and `^\d{10}$`. Telecom, banking and mock have no examples, so no patterns. `'CA'`, `'SFO'`, `'John'`, `'12345'` etc. are **excluded** so spelled names aren't cut off | B | Code (derive at session start) |
| 1-3, 14 | Prompt examples, `spelling_v2`, `<spoken_output>`, `<consent>` | Already rendered in all domains (feature-flagged, not domain-gated) | Unchanged scope; only the wording fixes (#1-#3 invented values, optional neutral wording in #14) | A | Config only |
| 15, 16 | Greeting seed, VAD 800 ms | Already applied in all domains | Unchanged. #15 is uniform across domains but **specific to τ-bench** (its own greeting string): keep it as part of the protocol adapter and disclose it | — | — |

After these changes **no file in the submission config references a τ-bench
tool name or domain name**. Check with:

```bash
grep -rnE "get_user_details|find_user_id|get_customer_by_phone|get_reservation_details|telecom|airline|retail" \
  src/prototypes/voice_delegation_hermes_agent/config \
  src/prototypes/voice_frontend_backend_agent/config/prompts.voice.yaml
```

The only expected hits are in `tau3_arm_*` / historical profiles that are not
part of the submission tag.

### 5.3 What "uniform" does and does not mean

- **Same code and config everywhere; behaviour follows what each session
  supplies.** A rule only *acts* when its trigger appears. The phone rule
  does nothing in a session with no phone arguments, and the identifier rule
  does nothing for arguments without schema examples. That is still generic:
  the agent is not told which domain it is in and nothing is switched per
  domain.
- **No per-domain profiles, flags, env vars or builds.** `FDH_PROFILE` and
  `FDH_GATEWAY_CONFIG` stay fixed for the whole submission.
- **The τ-bench policy still differs per domain**, because τ-bench sends it.
  The agent reacting to the policy text is expected and allowed.

### 5.4 Why the reward is preserved

On τ-bench the generic triggers are **designed** to fire in the same cases as
the name-based ones. This is a mapping to verify, not a result:

| Old trigger | Same case caught by |
|---|---|
| `get_user_details.user_id` rule | Read tool + `user_id` argument + example `'sara_doe_496'` → lowercase, `_`-joined, `name_name_digits` shape |
| Telecom `<domain_notes>` | Always-on `<phone_numbers>` block: lookups use `XXX-XXX-XXXX` for ten digits, plus one format retry |
| Hints on the 3 identity tools | Their "user … not found" errors name a person entity (verify the exact retail `find_user_id_*` error text) |
| 6-char airline hold | Derived from the identifier example `'ZFA04Y'` (`reservation_id`) |

So the cases that produced the reward gains are still covered. The **new**
firing points are the only behaviour change:

- identifier normalisation on other **read-tool** `*_id` arguments that have
  examples (airline `get_reservation_details.reservation_id`; retail
  `get_order_details.order_id`, `get_product_details.product_id`,
  `get_item_details.item_id`). **Write tools stay untouched**, as today;
- hints on any read tool whose not-found text names a user/customer: telecom,
  banking, and non-identity tools called with an unknown user;
- spelling-hold patterns for retail IDs (`#W…`, 10-digit item/product IDs).

Their effect on reward is **unknown until measured**. No write-tool
argument is rewritten by code, so code cannot change scored state directly.
But hints, retry-guard answers, phone formatting and hold timing change what
the backend sees and says. That can change the conversation, which tools are
called later, and therefore the outcome. Treat every new firing point as a
**hypothesis requiring validation** (§5.5, §6).

### 5.5 Risks and checks for the new firing points

| Risk | Example | Mitigation / check |
|---|---|---|
| Case or format change breaks a case-sensitive ID | Lowercasing an ID whose example is mixed-case | Derive case from the example; mixed-case example → `case: keep`. Unit test with mixed-case schemas |
| **Rewriting a write-tool argument changes the scored DB state** | Normalising `book_reservation.user_id` or `log_verification.phone_number` | **G6: never rewrite write-tool arguments.** Read/write is decided from the schema, with unknown tools treated as write. Unit test: the builder returns no rules for any write tool in the five τ-bench schemas |
| Non-idempotent rewrite of an already-correct value | Re-normalising an ID copied from a tool result | Rules are idempotent: a value matching the example's shape is passed through unchanged. Unit test with values taken from tool results |
| Prefix characters stripped | Retail order IDs start with `#` (`'#W0000000'`) | `strip` only removes characters **absent** from the example; `#` is kept because it appears in the example. Unit test |
| **Missing symbol prefix rejected locally** | User says "W one two three…", so the value is `W1234567` without `#` | Never reject over a prefix; **restore** the literal symbol (`#`) from the example. Never add letters or digits. Unit test |
| Hint on a non-identity "not found" | `get_product_details` "Product not found", or a bill/transaction lookup, would get "ask the user to spell it" | Hints fire **only when the not-found text names a person entity** (§4.7). Unit tests against every τ-bench not-found string (collected from `tools.py` of all five domains) |
| Local rejection blocks a valid value | A schema example shorter or longer than real values | Reject only **clearly incomplete** values (missing segment, empty segment, no final digits). Strict shapes are used only for the spelling hold, which never blocks a call |
| **Spelling hold cuts off spelled names** | A `'CA'` pattern from `state` would mark "C, A…" as complete | Derive patterns from **identifier arguments only** (`*_id`/`*_ids`/`id`, §4.9). Unit test: no pattern from `zip`, `state`, `origin`, `date`, `first_name`, … |
| Spelling hold adds latency on new ID types | Retail 10-digit item IDs | A complete pattern *reduces* hold time (complete values are never held). Monitor latency in interaction metrics |
| **Phone reformatting breaks non-3-3-4 numbers** | Banking data has `0412-555-947`; "always XXX-XXX-XXXX" gives `041-255-5947` | Prompt formats **lookups only**; writes pass the number as confirmed or returned; the code layer only **retries** after a failed **lookup**. Whether ASR/ITN keeps the spoken grouping is unverified, and the rule does not depend on it |
| Schema-driven rules are silent in some domains | Telecom, banking and mock schemas have no examples | Expected: same code everywhere, effect only where the interface gives format information. The phone rule (prompt) and the identity hints (argument names) still apply there |
| Banking tools behave differently | banking_knowledge tools have their own schemas | Smoke test `banking_knowledge` (even if not submitted) and `mock` |

Validation follows §6 (unit tests with invented schemas, equivalence replay on
existing logs, smoke tests). Add one more test: **feed the generic builder all
five τ-bench domains' tool schemas** (captured with
`cli/tau2_gates/capture_session_updates.py`) and snapshot the derived rules.
Review the snapshot once by hand. That is the full list of where each
behaviour will fire.

### 5.6 Implementation outline (unimplemented design)

1. **Rule builder** (`normalization/schema_rules.py`, new). Input: the tool
   list from `session.update`. Output:
   - identifier rules (#6/#7);
   - spelling-hold complete patterns (#12);
   - the lookup-tool set for hints (#9);
   - the phone arguments (#4 code layer).

   Pure function, unit-tested.
2. **Wire it in after the tools arrive, not at server start.** Today the
   per-session collaborators are built in `server.py` `parts()` (~L92-129)
   when the turn manager is created. That is **before** the client's
   `session.update` delivers the tools, which happens in
   `engine/turn_manager.py` `on_session_update()` / `_configure()` (~L298-332).
   The plan therefore needs:
   - a per-session **rebuild hook** in `on_session_update`: run the builder on
     the received tools, then construct (or `reconfigure`) the
     `ArgumentNormalizer`, `ResultHints` and `SpellingHoldPredicate` for that
     session, and hand them to the tool relay and turn manager;
   - a defined behaviour for turns or tool calls before the first
     `session.update` (no rules) and for a later `session.update` that
     changes the tools (rebuild; `session.update_after_start: error` currently
     rejects tool changes after the agent exists, so check that path);
   - the new `session_tools_sha256` computed at the same point and logged
     with `backend_configured`.

   Rules from YAML become an **optional override**: empty in the submission
   config.
3. **Config switches:** `rules: auto`, `retry_guard.scope: all`,
   `result_hints.tools: auto`, `spelling_hold.complete_patterns: auto`.
4. **Prompts:** add `<phone_numbers>` (always on); reword I3/I4; replace the
   example values; delete `backend_domain_notes`; empty `domains:`.
5. **One submission profile**: `profiles/realtime_eval.yaml` +
   `gateway.eval.yaml`, frozen at a tag.
6. **Tests:** builder snapshot over the five τ-bench domains plus invented
   schemas; replay equivalence; mock + banking smoke. Also add **G6
   assertions**: no rule on any write tool; every rewrite is idempotent;
   hints only on person-identifying lookups; no spelling-hold pattern from
   non-identifier examples.

### 5.7 Cross-domain review result (schemas of all five domains checked)

The tool schemas τ-bench sends were dumped for mock, airline, retail, telecom
and banking_knowledge (`env.get_tools()` → `openai_schema`), together with
their error strings and phone formats. Each proposal was then checked against
them.

**Schema facts that drive the refinements:**

- **Examples ("such as '…'") exist only in airline and retail:**
  - `sara_doe_496`, `ZFA04Y`, `8JX2WO`, `credit_card_7815826`;
  - `#W0000000`, `6086499569`, `1008292230`, `gift_card_0000000`;
  - `12345`, `John`, `CA`, `SFO`, `2024-01-01`, `2 + 2`.

  Telecom, banking_knowledge and mock have **none**.
- **Error text:** telecom raises `"Customer with phone number … not found"`;
  banking returns `"Error: User with ID '…' not found."`. Both match the
  generic not-found pattern.
- **Phone formats:** telecom DB uses `555-123-XXXX`. Banking data has mostly
  `XXX-555-XXXX` but also **`0412-555-947`**. Banking has a **write** tool
  with a phone argument (`log_verification`).
- **Lookup naming:** almost every telecom/banking tool starts with `get_`,
  including non-identity lookups (`get_bills_for_customer`, `get_data_usage`,
  `get_credit_card_transactions_by_user`).

**Verdict per item:**

| Verdict | Items |
|---|---|
| Generic as originally proposed (**requires validation**) | #1-#3, #13 (invented examples); #5 (domain detection removed); #8 (retry guard on all tools); #10, #11 (hint wording); #14 (prompt blocks); #16 (VAD) |
| Generic design **after the refinements now in this doc** (**requires validation**) | **#4** phone (format lookups only; writes pass as confirmed or returned; lookup-only retry), **#6** identifier normalisation (read tools only, idempotent, strip only absent characters), **#7** local rejection (only clearly incomplete; restore symbol prefix, never letters/digits), **#9** hints (only when the not-found text names a user/customer), **#12** spelling hold (identifier arguments only) |
| Uniform across domains but **specific to τ-bench** | **#15** greeting seed (τ-bench's greeting string): keep as protocol adapter and disclose |
| No change needed | #17 (naming; handled by the single submission profile), #18 (stub backend), #19 (offline CLI) |

**Underlying principle added (G6):** never rewrite the arguments of write
tools, and make every rewrite idempotent. That prevents the *code* from
writing reformatted values into scored state. It does **not** by itself
guarantee unchanged reward, because prompts and hints still shape the
conversation.

**Review corrections applied (2026-10-05):**
- `backend_catalog_sha256` is a prompt-catalog hash, not a tool hash;
  replaced with a proposed new `session_tools_sha256`.
- Hint targeting changed from argument names (over-matched billing,
  usage and transaction tools) to person-entity not-found text.
- The spelling-hold filter now uses argument names only (the value filter
  admitted `12345` / `2024-01-01`). `#W0000000` checked: `#`, `W`, then 7
  digits, so `\d{7}` is correct.
- The phone prompt now formats lookups only; writes are passed through
  unchanged. Reliance on spoken grouping was removed (unverified).
- "Reward preserved" claims were downgraded to hypotheses requiring
  validation.
- §5.6 wiring now rebuilds the rules after `session.update`, not at server
  start.

---

## 6. Validation plan (keep the reward without new test-set tuning)

The aim is to show that the generic versions **behave the same** on the cases
the originals were built for, without another round of tuning on scored
tasks.

1. **Unit tests with invented data.** For each generic rule, write tests
   using made-up tool schemas and values: a phone lookup tool with a
   different name, a `customer_id` argument with example `'ab_cd_12'`, a
   6-character code in a non-airline tool, and so on. This proves the
   behaviour is not keyed on τ-bench names.
2. **Equivalence check on existing logs (non-regression, not tuning).**
   Replay the already-logged tool calls and results from earlier τ³ runs
   through the new screener/hint code (the FBA `cli/` replay tooling). Assert
   that the generic rules fire **at least** wherever the old rules fired, and
   that canonicalised arguments are identical. Report this in methodology as a
   regression check. Do **not** iterate on wording against these logs.
3. **Smoke test on `mock`** (all four τ-bench tools and policies are
   different), to confirm that nothing domain-specific is triggered and that
   the generic rules are harmless where they don't apply.
4. **One frozen config, one official run** per domain on the official user
   simulator (see the companion doc). No re-tuning between domains or after
   seeing scores.
5. **Record fingerprints** (`backend_system_sha256`, `backend_catalog_sha256`
   (prompt catalog), the new `session_tools_sha256` (tool schemas), profile
   path, git commit) in every session log, and include them in the submission
   notes.
6. **Check ASR/ITN phone grouping.** From logged backend inputs of earlier
   telecom runs, record how spoken phone numbers reach the backend (grouped or
   not). This confirms whether the lookup rule is sufficient.
7. **Measure, don't assume.** Before the official runs, do one A/B on
   **non-scored** data: `mock`, plus invented scenarios. Optionally run
   banking_knowledge if banking will not be submitted. Compare the current
   config against the generic config, and look for regressions caused by
   hints, the retry guard and hold timing.

**Hypothesis (unproven):** pass^1 within run-to-run noise of the current
configuration, because the mechanisms are unchanged and the generic triggers
are designed to cover the original cases. Known behaviour changes that could
move the score either way:

- #7: wrong-length user IDs now reach the tool and fail once before the
  read-back;
- #9: hints in new places (telecom, banking, unknown-user errors from
  non-identity tools);
- #8: the broader retry guard;
- #4: lookup-only phone formatting;
- #12: different hold timing on retail IDs.

Report the measured result; do not state equivalence without it.

---

## 7. Disclosure text for `methodology.notes`

Suggested wording, to adapt:

> The agent is a cascaded voice system (Nemotron Speech Streaming ASR,
> Nemotron 3.5 Lightning frontend router, Hermes agent on Nemotron 3 Ultra as
> backend, Magpie TTS) behind an OpenAI-Realtime-compatible endpoint. The
> τ-bench harness, user simulator, prompts, policies, tools and evaluator are
> unmodified. The τ-bench policy is passed verbatim to the backend inside our
> own system prompt.
>
> Internal behaviours: schema-example-driven identifier normalisation of tool
> arguments; a local read-back response for incomplete identifiers; a loop
> guard against repeating an identical failed lookup; recovery guidance
> appended (internally) to failed lookup results; a phone-number formatting
> rule; a spelling hold; an 800 ms end-of-turn silence (the client's VAD
> values are not honoured).
>
> These behaviours were developed after observing failure modes (identifier
> transcription and formatting errors) in earlier runs on τ³ base tasks.
> Before submission they were rewritten to be domain-agnostic and driven by
> the tool schemas the benchmark supplies, with no benchmark values or
> database facts. No model was trained or fine-tuned on τ-bench data.

---

## 8. Checklist

- [ ] #1 Replace `ROSSI` / `IFOYYZ` in `prompts.backend.yaml:28`
- [ ] #2 Replace `mia_kim_4397` in `prompts.yaml:123`
- [ ] #3 Replace `mia_kim_4397` / `mia_fancy_4739` in FBA `prompts.voice.yaml:376-381`
- [ ] #4 Remove I1 telecom note; add the generic `<phone_numbers>` block (format lookups only; writes as confirmed or returned); optional code layer = retry after a failed **read**-tool lookup only; check ASR/ITN grouping in logs
- [ ] #5 Empty `domains:` / disable `domain_notes` in the submission gateway config; add and log a new `session_tools_sha256` (`backend_catalog_sha256` is the prompt catalog)
- [ ] #6 Schema-driven identifier rule: **read tools only**, idempotent, strip only characters absent from the example (or the config fallback)
- [ ] #7 Local rejection only for clearly incomplete values; restore a missing symbol prefix (`#`), never letters or digits; no `\d{4}`
- [ ] #8 Generic, case-insensitive retry-guard pattern
- [ ] #9 Fire result hints only when the not-found text names a user/customer (not by tool or argument names); unit-test against all τ-bench not-found strings
- [ ] #10 Reword I3 (no zip-specific or "other method" retail framing)
- [ ] #11 Reword the last sentence of I4
- [ ] #12 Spelling-hold patterns from **identifier arguments only** (`*_id`/`*_ids`/`id`), or `[]`
- [ ] #13 Clean comments, docstrings and tests of task values
- [ ] #14 Optional neutral wording in `<spoken_output>`; disclose
- [ ] #15 Keep the greeting seed (uniform but τ-bench-specific); disclose as protocol adapter (optionally read it from the client)
- [ ] #16 Disclose the 800 ms VAD
- [ ] #17 One frozen submission profile + gateway config at a tagged commit
- [ ] Rebuild normalizer / hints / spelling hold per session after `session.update` (§5.6)
- [ ] A/B on non-scored data (mock, invented scenarios) before official runs; report measured reward, don't assume it
- [ ] G6 tests: no rule on any write tool; rewrites idempotent; hints only on identity lookups; no hold pattern from non-identifier examples
- [ ] §5 Uniform: no τ-bench tool/domain names in the submission config (grep check); schema-rule snapshot reviewed
- [ ] Validation: unit tests with invented data, log-replay equivalence, mock smoke test
- [ ] `methodology.notes` disclosure (§7) and question to Sierra about the standard label
