# CLAUDE.md — Service Systems Research Pipeline

Context for continuing work on the data pipeline. Read this first when resuming.

---

## Research Goal

Model **how customer-service agents prioritize which waiting customer to respond to next**, using **conditional logistic regression** over choice sets.

- Agents handle several chat sessions in parallel. Each time an agent sends a reply, they implicitly **chose** one waiting customer over the others.
- A **choice set** = all sessions waiting for the same agent at the moment of an agent reply. Exactly one alternative is `chosen = 1`; the rest are `chosen = 0`.
- The model tests whether agents deviate from FIFO and instead prioritize by **waiting time, message volume (n_messages), sentiment, workload**, etc.
- Final output: one row per alternative per choice set, suitable for conditional logit estimation.

See `data_documentation.md` for the full field/dictionary reference and `README.md` for the public-facing project description.

---

## Repo layout

Code, docs, and the checker live in subfolders; **data CSVs stay in the repo root** (gitignored). Scripts `chdir` to the repo root at startup, so all the bare data filenames below resolve there regardless of where a script physically sits.

```
src/          stage_1/2/3 + agent_unification.py + run_pipeline.py (orchestrator)
validation/   official_checker_18_04.py + Algorithem_Checker.txt
docs/         data_documentation.md + reference PDFs
notebooks/    Choiceset_error_analysis.ipynb  (left in root historically; reads data from root)
<root>/       README.md, CLAUDE.md, *.csv data (raw / interim / output)
```

## Pipeline Overview (3 stages + checker)

Raw input: `raw_events_17_06.csv` (event-level, clean column names + `silent_abandonment_bool`) and `merged_session.csv` (session-level time windows). `merged_session_events.csv` is the older raw input, superseded.

> **`src/run_pipeline.py` auto-chains all three stages** (Stage 1 → 2 → 3) as subprocesses, streaming output to console + a timestamped log. Run from the repo root: `python src/run_pipeline.py` (or `--start-stage 3` to reuse existing Stage 1/2 outputs). Intermediate CSVs are written with the names the next stage expects, so no manual renaming.

| Stage | Script (`src/`) | Reads | Writes |
|---|---|---|---|
| 1 | `stage_1_cleaning_and_unification.py` | `raw_events_17_06.csv` | `df_after_stage1_<date>.csv` (orchestrator: `df_1_not_merged_2_merged.csv`) |
| 2 | `stage_2_concurrencies_and_explode.py` | `df_1_not_merged_2_merged.csv`, `merged_session.csv` | `df_exploded_all_data.csv`, `before_third_stage_all_data.csv` |
| 3 | `stage_3_creating_choicesets_from_exploded.py` | `df_1_not_merged_2_merged.csv`, `df_exploded_all_data.csv` | `df_choicesets_<date>.csv` |
| ✓ | `validation/official_checker_18_04.py` | `df_choicesets_<date>.csv`, `merged_session_events.csv` | validation report (stdout) |

---

## Stage 1 — Cleaning, sorting, unifying consecutive agent messages

`stage_1.py`. Sequence in `main()`:

1. **`clean_data`** — drops `outcome == 4` (known abandonment) and `event_type` 8/9 (parallel-chat overhead). **Decision: dropping `outcome == 4` is intended and settled — known abandonment is excluded from the choice model.** NOTE: the log line says "Removed silent abandonment" but it is actually removing **known** abandonment (outcome=4); the log message wording should be corrected. `event_type == 7` is NOT dropped here.
2. **`rep_fix`** — early customer messages carry placeholder `id_rep = 1` (before agent assignment). Per `(id_session, subsession)`, the first real (non-1) `id_rep` is propagated back to replace the 1s.
3. **`event_type_7_fix`** — type=7 ("customer leaves queue") marks when the agent *first sees* the session. For each type=1 immediately followed by type=7 in the same session, copy the type=7 `end_time`/`end_date` onto the type=1 row, then **drop all type=7 rows**. This makes Stage 3's `waiting_time` start from the moment the agent could act.
4. **Agent-message unification** — calls `unify_agent_messages()` from `agent_unification.py`. This replaces the old session-perspective merge. See below.
5. **`event_id`** — assigned after unification on the final sorted output (`index + 1`).

**Expected sort for downstream:** `id_rep` asc, `end_time` asc, `event_type` desc. (Stage 3 re-sorts to `id_session, end_time, event_type desc` on load.)

---

## Agent-message unification — `agent_unification.py`

Replaces the old session-perspective merge (which sorted by `id_session, end_time` and merged consecutive same-type rows within a session). The old approach was wrong because agents handle multiple sessions concurrently — two agent messages consecutive within a session may have an intervening reply to a different session, representing a separate agent decision.

### New logic (agent-perspective)

Two type-2 rows merge if and only if:
1. Same `id_rep` (same agent)
2. Same `id_session` (same session)
3. No **same-session type-1** between them (visitor replied — new turn)
4. No **different-session type-2** between them (agent chose to reply elsewhere)

Type-1 events from **other** sessions are ignored — they are not agent actions.

**Implementation (two-step merge groups):**
- **Step A — within-session runs:** Sort all events by `(id_rep, id_session, end_time, event_type desc)`. Within each `(id_rep, id_session)`, a new run starts when `event_type` changes. This catches same-session type-1 breaks. Other-session events are invisible here (different `id_session` group in the sort).
- **Step B — agent-timeline groups:** Among type-2 rows sorted by `(id_rep, end_time)`, a new group starts when `id_rep` or `id_session` changes. This catches different-session type-2 breaks.
- **Final group** breaks when either step triggers.

**Tie-breaking:** `event_type desc` ensures type-2 sorts before type-1 at the same `end_time` (agent was already sending when visitor message arrived simultaneously).

**Merge operation:** SUM `sentiment, duration, number_words`; keep first-row values for everything else. MAX of `flag`.

### `flag` column

Binary column added to every row (0 for non-type-2). For type-2 rows:
- `flag = 1` when the row **would have been merged** under the old session-perspective logic (consecutive type-2 in session order) but is **NOT merged** under the new agent-perspective logic (agent's previous type-2 action was in a different session).
- Computed as: `would_merge_old AND would_NOT_merge_new`.

### Functions

| Function | Role |
|---|---|
| `compute_flag(df)` | Computes flag column before unification |
| `_assign_merge_groups(df)` | Two-step group assignment (returns groups + type-2 index) |
| `_merge_group(group, cols)` | Collapses one group into one row |
| `unify_agent_messages(df)` | Main entry point called from `stage_1.py` |

---

## Stage 2 — Concurrencies & exploded table

`stage_2_concurrencies_and_explode.py`.

- **Concurrency now computed from scratch** (the old precomputed `aggregated_df_11_5.csv` path is kept only as a commented reference block). For each agent reply (type=2), find all sessions of the same `id_rep` where `chat_start_time < reply_end_time <= chat_end_time` → `concurrent_sessions`. Implemented with per-agent pre-grouped numpy arrays for speed (~14 sec on full data); verified identical to the original brute-force filter (0 mismatches on 300 samples).
  - **Upper bound `<=` (not strict `<`) — same-second boundary fix.** `chat_end_time` is whole-second, so a reply can land on the exact second the session's metadata marks it closed. That session still had an open visitor turn the agent was answering, so it must count as a competing alternative. Strict `<` dropped it, producing a no-chosen set whose chosen session was excluded from its own concurrent list (Known Issue #6, "closed-but-active"). `<=` recovers ~53 such sessions. The **lower** bound stays strict: a session starting at the exact reply second is a proactive agent opener with no pending customer turn, so it is correctly excluded (a lower-bound `<=` was investigated and resolves 0 no-chosen sets — see Known Issue #6). Replies landing *hours* after `chat_end` (~8 cases) stay excluded — genuinely stale metadata, not a boundary case.
- `workload = len(concurrent_sessions)` (raw concurrent-session count). **The earlier `/ 11` divisor was a mistake and has been removed.**
- Concurrency lists are kept as real Python lists in memory, so **no `literal_eval`** is needed before the explode.
- Keep only rows with **>1 concurrent session** (genuine choices).
- **Explode** `concurrent_sessions` → one row per alternative. `chosen = 1` where `concurrent_sessions == session_id_chosen`.
- **`flag` carried through:** the chosen agent reply's `flag` is carried onto the choice set so it survives the explode (replicated across all alternatives — it's a choice-set-level attribute of the agent's decision).

---

## Stage 3 — Building the choice-set regression table

`stage_3_creating_choicesets_from_exploded.py`. For each alternative (concurrent session) at a choice moment:

- Take that session's events before `row.time` (the choice moment).
- Find the **last agent reply** (type=2). Collect all type=1 messages with `end_time >= last_reply_time` = the **pending turn** (consecutive unanswered visitor messages). If no type=2, all type=1 are pending. The `>=` (not strict `>`) honors Stage 1's canonical sort on same-second ties: a visitor message sharing the reply's exact second sorts *after* the type=2, so it is pending; a strict `>` dropped it (this is what made choice_set 3996 a no-chosen set). Option 3 uses the same `>=` on its turn lower bound. (This is the "Problem 1" fix in Known Issues #7; the tie-break *ordering* itself — "Problem 2" — is still open.)
- **If no pending type=1 → skip the alternative** (agent had no unanswered message to act on). This is the main reason some alternatives produce no row.
- **Aggregate the pending turn into one row:**
  - SUM: `sentiment, duration, number_words, number_chars, number_lines` (`COLS_TO_SUM`)
  - `n_messages` = count of messages in the turn
  - DROP: `read_date, read_time, accept_date, accept_time, delay` (`COLS_TO_DROP`)
  - FIRST-row value for everything else
- `waiting_time = choice_time − end_time of the FIRST pending message` (`choice_time` = `row.time` normally; see Option 3 for the flag=1 exception).
- `chosen_time` = `choice_time` (i.e. `first pending end_time + waiting_time`) — emitted as a column for the checker / sanity reconstruction.
- Carries `choice_set` (= exploded row index), `chosen`, `chosen_time`, `workload`, `flag`.

### Option 3 — flag=1 re-engagement injection

A `flag=1` event is a choice moment whose **chosen** session has no pending visitor turn (the agent replied again to a session whose visitor said nothing new — see Agent-message unification). Left alone, Stage 3 would skip the chosen alternative and produce a **no-chosen choice set**. Instead, for the chosen alternative of a `flag=1` set (`row.flag == 1 and row.chosen == 1`), we **reconstruct the visitor turn from the session's genuine reply** and inject it as the `chosen=1` row:

- `last_type1_time` = end_time of the most recent type-1 in the session before `row.time`.
- `genuine_reply_time` = first type-2 **after** `last_type1_time` (the agent's first reply to that turn).
- turn = type-1 messages between the type-2 before the turn and `last_type1_time`.
- `n_messages` + summed covariates come from that turn (identical to what the session produced at its genuine reply).
- **`waiting_time = genuine_reply_time − first pending message`** — response latency of the turn, NOT measured to the re-engagement moment. (Decision: the customer's wait ends at the first reply; later re-engagement messages don't extend it. Cost: within a flag=1 set the chosen row's waiting_time reference differs from the non-chosen alternatives, which use `row.time`. The `flag` column keeps these rows isolable in the regression.)
- `workload` still comes from the current choice moment (`row.workload`) — it's a property of this choice set.
- Chained flag=1 events auto-resolve: all point at the same most-recent visitor turn, so no cross-choice-set caching is needed.
- Edge case: a chosen session with no type-1 at all before `row.time` cannot be reconstructed → stays no-chosen (rare).

---

## Checker — `official_checker_18_04.py`

Two validations, both at choice-set level:

- **`validate_n_messages`** — mirrors Stage 3 v2 logic: find last type=2 before `chosen_time`, count type=1 after it; verify it equals `n_messages`, and that there is exactly one (chosen) / zero (non-chosen) type=2 at the choice moment.
- **`checker`** (colleague's) — 3 checks: (1) chosen session matches the session with a reply at exactly `chosen_time`; (2) no events after `chosen_time` in window; (3) set of choice-set sessions == set of raw sessions whose last event before `chosen_time` is type 1 or 7.

Both filter raw events to `event_type in [1, 2]` (validate) / `[1, 2, 7]` (checker). Segmented sampling via `choose_choicets` / `segment_choice` (high/low waiting_time, n_messages, workload).

---

## Key Definitions

| Term | Meaning |
|---|---|
| Choice set | All sessions waiting for one agent at the moment of an agent reply |
| Choice moment | `end_time` of an agent reply (type=2) |
| Pending turn | Consecutive type=1 messages since the session's last type=2 |
| `n_messages` | Count of messages in the pending turn |
| `waiting_time` | choice_time − end_time of the **first** pending message |
| `workload` | concurrent session count (raw `len(concurrent_sessions)`) |
| `chosen` | 1 for the replied-to session, 0 otherwise |

**event_type:** 1=visitor msg, 2=agent reply, 7=leaves queue (agent first sees session), 8/9=parallel-chat overhead (dropped).
**outcome:** 1=served, 2=served+transferred, 3=silent abandonment, 4=known abandonment, 5=reject invite, 6=no survey.

---

## Known Open Issues / Decisions Pending

1. **Abandonment filter — DECIDED.** Stage 1 drops `outcome == 4` (known abandonment) and this is the settled approach — known abandonment is excluded from the choice model. (Background: investigation showed both silent (3) and known (4) abandonment sessions can contain full agent–visitor interactions; the notebook explored dropping only known_abandonment sessions with an agent reply, but the decision is to drop all `outcome == 4`.)
2. **`stage_1.py` `__name__` bug — FIXED.** The `if __name__` block was indented inside `main()`. De-indented to module level.
3. **Filename drift / manual chain — RESOLVED.** `src/run_pipeline.py` now auto-chains the stages, passing each stage's output as the next stage's input (`df_1_not_merged_2_merged.csv` etc.), so no manual renaming. (Stage 1's standalone default output name still differs; only matters if a stage is run by hand outside the orchestrator.)
4. **Stage 2 concurrency code — RE-ACTIVATED.** Concurrency is now computed from scratch in `stage_2_concurrencies_and_explode.py` (numpy-optimized, verified against the original). The old precomputed `aggregated_df_11_5.csv` is no longer used (it was stale anyway). Carries `flag`.
5. **Checker reference-data mismatch** — checker reads raw `merged_session_events.csv`, but choice sets are built from Stage 1 *output* (which has type=7 fix, updated end_times, merged type-2, propagated id_rep). This causes false mismatches when a session was transformed in Stage 1.
6. **Concurrency bounds — RESOLVED (upper bound → `<=`, lower bound stays strict `<`).** The concurrency mask is `(chat_start < T) & (chat_end > T)` for choice moment `T`. Deep investigation of what those metadata fields actually are, and which bound (if any) wrongly excludes waiting sessions:
   - **What the fields are (proved empirically):** `chat_start_time` **is** essentially the session's **first-message `start_time`** (84.8% exact match; diff = 0 at the 25/50/75 percentiles). `chat_end_time` is **NOT** the last-message end — only 0.1% match; it is a **late session-close/timeout stamp**, a median of ~**1,192 s (20 min)** *after* the last event (99.8% of the time `chat_end` > last activity). So the lower bound rests on a reliable marker; the upper bound rests on a *generous* one.
   - **Lower bound — keep strict `<`.** Exhaustive search (the only sessions that can be wrongly excluded are the **375** with a `type=1` before their own `chat_start`) found just **152** genuine excluded pending alternatives across *all* choice moments: **2** at `chat_start == T` (a `<=` would capture) and **150** at `chat_start > T` (a `<=` would *not* fix — these are `chat_start`-lag anomalies). All 152 are non-chosen alternatives (fix 0 no-chosen sets), and capturing them via `<=` would also admit ~3,263 just-born sessions into concurrent lists, inflating `workload` on ~3,266 sets. Not worth it. Separately, the 696 `reply == chat_start` no-chosen sets are **proactive openers** (0 have a waiting `type=1`; 545 silent-abandonment with no `type=1` at all, 152 served where the customer's message arrives *after* the reply; 0 have a `type=7`) — correctly excluded.
   - **Upper bound — loosen to `<=` (same-second only).** `chat_end_time` is whole-second; a reply can land on the exact second of the metadata close while the visitor turn is open. Strict `>` dropped **53** genuinely closed-but-active *chosen* sessions (+~228 good-set alternatives; **281** total across all choice moments). `ends > reply` → `ends >= reply` recovers them (the reply coincides with close = agent actively answering). 
   - **The "35k dropped waiting alternatives" scare — a mirage, no further change.** Because `chat_end` is a *late* stamp, ~**35,167** sessions have a pending `type=1` at some same-agent reply `T ≥ chat_end` (within 1 h). But classified: **47%** are served + short single trailing message (courtesy/closing, e.g. "thanks"), **12.4%** are abandonment (`outcome=3`, customer already left), and **40.6%** are served + a substantive trailing message. All occur *after* an already-generous close, so the customer is almost certainly gone — none are clear live waiting customers. The strict upper bound (with the `<=` same-second fix) is therefore correct; **do not** switch to event-stream-based concurrency to "recover" them.
   - **Net change to Stage 2:** upper bound `<=` only. **Takes effect on the next Stage 2 re-run.**
7. **Same-second tie — two separate problems.** When a visitor (type=1) and an agent reply (type=2) share the exact same whole-second `end_time`, two distinct issues arise:
   - **Problem 1 — Stage 3 ignored Stage 1's sort order — FIXED (`>` → `>=`).** Stage 1 sorts ties by `event_type desc` (so a same-second visitor message sorts *after* the reply = pending), but Stage 3 used a strict `end_time > last_reply`, which drops the tied message — so the sort's decision never reached the result (e.g. choice_set 3996 became a spurious no-chosen set). Changed to `end_time >= last_reply` in `stage_3` (main path + Option 3 turn lower bound) and in the checker's `validate_n_messages`. (`>=` is exactly equivalent to "type=1 positioned after the last type=2" given the canonical sort — a simpler way to honor the same ordering; chosen over an explicit position-based form per advisor preference.) Verified: 3996 now yields `chosen=1` (`100071283`, `n_messages=1`, `waiting_time=28`); 756 stays no-chosen (true opener); flag=1 sets unchanged; checker validates all three True. **Takes effect only on the next full re-run** (current `df_choicesets_23_06_2026.csv` still reflects the old `>` behaviour).
   - **Problem 2 — the tie-break *ordering* is wrong ~60% of the time — OPEN.** The `event_type desc` rule *assumes* the agent reply preceded the visitor message on a tie. Full-data check: at the **12,003** tie points (0.40% of all agent replies), `start_time` **and** raw file order agree the **visitor message actually came first in ~60%** of cases. The correct fix is to break `end_time` ties by **true chronology** (`start_time` / file order) instead of `event_type`, in **Stage 1's canonical sort** — which also drives agent-message merging (3996's two replies would then merge into one well-formed choice moment). Cross-stage change, deferred pending advisor consultation. NOTE: with Problem 1 fixed but Problem 2 open, Stage 3 now faithfully reproduces a tie-break that is itself backwards ~60% of the time — i.e. **consistent, not yet correct**. The checker's `validate_n_messages` was aligned to the same `>=` rule, so it agrees with Stage 3 on tie cases.
8. **No-chosen choice sets — full breakdown.** After the `>=` tie fix (#7), the current table (`df_choicesets_28_06_2026.csv`) has **2,260** visible no-chosen sets (down from ~3,197 before the fix). Full trace of all 2,260 — every one is the agent acting with **no waiting customer turn to choose**, except one real gap:
   - **696 — proactive opener, reply == chat_start** (chosen session excluded from its own concurrent list at Stage 2; the reply *is* the session's first event). Genuine; not resolvable by a lower-bound tweak (verified 0 flip).
   - **1,181 — proactive opener, no events at all before the choice moment** (chosen alt present in exploded, skipped at Stage 3 for no pending type=1). Genuine.
   - **316 — `flag=1` re-engagement with no type=1 ever in the session** (Option 3 cannot reconstruct a turn without a visitor message). Genuine edge.
   - **6 — reply ≥ chat_end but no pending turn** (opener/already-answered). Genuine.
   - **61 — "closed-but-active" (the only real defect):** chosen session had a pending visitor turn but was excluded because the reply landed at/after `chat_end`. **53 fixed** by the Stage 2 upper-bound `<=` change (#6); **8** are hours-late replies left open.
   - So **2,199 of 2,260 are correct behavior** (proactive openers / re-engagements — open *policy* question whether Stage 2 should emit a choice set at all for an agent reply that answers no waiting customer), and only the 61 were a coverage bug (53 now addressed).

---

## Repo / Git Notes

- `.gitignore`: `*.csv`, `*.pdf`, `*.ipynb`, `*.log`, `big_code_*.txt`, `__pycache__/`, `*.pyc`, `.Rhistory` — data files, notebooks, logs, and cruft are **not** tracked. Pulling/cloning will not bring CSVs; copy data manually.
- `Choiceset_error_analysis.ipynb` was un-tracked via `git rm --cached` and is ignored on `main`. It lives in the **repo root** (not `notebooks/`) because it reads ~14 data CSVs by bare filename from the root.
- Remote: `origin` → `https://github.com/TamirS94/Service-system-research.git`.
- Reference docs: `README.md` + `CLAUDE.md` (root), `docs/data_documentation.md`, `validation/Algorithem_Checker.txt`.
