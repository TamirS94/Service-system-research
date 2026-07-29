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
| ✓ | `validation/official_checker_18_04.py` | `df_choicesets_<date>.csv` (newest by default), `df_1_not_merged_2_merged.csv`, `df_exploded_all_data.csv` | validation report (stdout) |

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

- **Concurrency now computed from scratch** (the old precomputed `aggregated_df_11_5.csv` path was removed). For each agent reply (type=2), find all sessions of the same `id_rep` where `queue_exit_time < reply_end_time <= chat_end_time` → `concurrent_sessions`. Implemented with per-agent pre-grouped numpy arrays for speed (~14 sec on full data). **`outcome == 4` (known-abandonment) sessions are dropped from the concurrency pool first** (aligns with Stage 1, which drops them from the events).
  - **Lower bound = `queue_exit_time` (assignment / agent-visible moment), strict `<`.** NOT `chat_start_time` (when the customer *sent* their first message). The pre-assignment queue wait is irrelevant to this agent's choices — the session was not yet on their plate. Empirically `chat_start_time` = the first message's `start_time` (84.8% exact), and `assignment = chat_start_time + queue_time = queue_exit_time` (arithmetic identity, ±1 s). This mirrors Stage 1's type=7 fix (which already pins `waiting_time` to the agent-visible moment) — Stage 2 now agrees. **Verified safe: 0 new no-chosen sets** (an agent is never assigned a session *after* replying to it, so `queue_exit < reply` always holds for the chosen session). Effect on `workload`: mean 6.60 → 6.25 (~5% lower; median/p90 unchanged) — a real covariate shift, so results are **not** comparable across this change. **Sentinel note:** `outcome==4` sessions have `queue_exit_time == 0` (never assigned) — a bijection — so dropping them also removes every 0-sentinel, making `queue_exit_time` universally valid with no fallback needed.
  - **Upper bound `<=` (not strict `<`) — same-second boundary fix.** `chat_end_time` is whole-second, so a reply can land on the exact second the session's metadata marks it closed. That session still had an open visitor turn the agent was answering, so it must count as a competing alternative. Strict `<` dropped it, producing a no-chosen set whose chosen session was excluded from its own concurrent list (Known Issue #6, "closed-but-active"). `<=` recovers ~53 such sessions. Replies landing *hours* after `chat_end` (~8 cases) stay excluded — genuinely stale metadata, not a boundary case.
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

Rewritten to match the current pipeline. Run from the repo root:

```
python validation/official_checker_18_04.py                 # structural + Stage 2 cross-check + segmented sample
python validation/official_checker_18_04.py --sample 300     # + 300 random sets
python validation/official_checker_18_04.py --choice-sets 3996,756
python validation/official_checker_18_04.py --structural-only
```

**Reference data (the key change).** The checker now reads the **Stage 1 output** (`df_1_not_merged_2_merged.csv`) as the event stream, not a pre-pipeline raw file — that is what Stage 3 was actually built from, so the false mismatches of Known Issue #5 are gone. It also reads the **Stage 2 exploded table** (`df_exploded_all_data.csv`) for the **choice moment `T`** and the concurrency lists. `T` cannot be recovered from the choice-set table alone: on a `flag=1` set, Option 3 writes the *genuine reply time* into the chosen row's `chosen_time`, and ~65% of those sets contain nothing but that row. Without the exploded table the checker falls back to `max(chosen_time)` and warns. Choice-set input defaults to the **newest** `df_choicesets_*.csv`.

Four validators:

- **`structural_report`** — whole file, vectorized, no event lookups: one-chosen-per-set, no duplicate alternatives, `waiting_time == chosen_time − end_time`, sign/range sanity, per-set constancy of `workload`/`flag`/`chosen_time`(flag=0), `set size <= workload`.
- **`exploded_report`** — whole file, against Stage 2: every emitted alternative was in the agent's concurrency list, `workload == len(concurrent_sessions)`, `chosen_time == T` (strictly earlier on an Option-3 chosen row).
- **`validate_n_messages`** — sampled sets, event level: rebuilds each alternative's pending turn with Stage 3's exact rules (the `>=` same-second tie rule *and* the Option-3 reconstruction) and verifies `n_messages`, first-pending `end_time`, `waiting_time`, `chosen_time`, the summed covariates (`COLS_TO_SUM`), and that the turn was genuinely unanswered.
- **`checker`** — sampled sets, set level: exactly one chosen; the chosen session is the only alternative with a reply at `T`; emitted alternatives match the concurrency list, with every dropped alternative justified by Stage 3's "no pending visitor turn" skip rule.

**Report format.** The whole-file passes mark `[OK]`/`[FAIL]` for invariants and `[INFO]` for expected categories (`no_chosen_set`, `singleton_set`, `id_rep_*`). The sampled passes print **one block per choice set listing every check as `[TRUE ]` / `[FALSE]` / `[ n-a ]`**, with both validators merged so a single block answers "which checks did this choice set pass?" — `n-a` means the check does not apply (Option-3 reconstruction on a normal set, or the Stage 2 coverage checks with `--no-exploded`). Each segment ends with a TRUE/FALSE/n-a tally per check, and the run ends with a failure summary and a verdict line. `--compact` collapses each set to one line plus its FALSE list.

Segmented sampling via `choose_choicets` / `segment_choice`, now seeded (`--seed`) and safe when a segment has fewer sets than requested. Nine segments — high/low `waiting_time`/`n_messages`/`workload`, plus `high_events`, `flag_reengagement`, `no_chosen` — at `--n-per-segment` each (default 5, so ~45 sets); `--sample N` adds N uniformly random sets on top.

**Colleague's original 3 checks** — (1) chosen session == session replying at `chosen_time` is *kept* as `chosen_is_replier` (now resolved against the true `T`); (2) "no events after `chosen_time`" was **dropped as vacuous** (the window was already filtered to `<= chosen_time`, so it could never fail); (3) "set of choice-set sessions == raw sessions whose last event is type 1/7" was **replaced** — Stage 2 defines concurrency from the *session metadata* window (`queue_exit_time < T <= chat_end_time`), not from the event stream, so the event-stream reconstruction disagreed with the pipeline by construction. The concurrency list itself is now the reference.

**Validated against injected defects** (n_messages, waiting_time, aggregation sums, chosen flips, alien sessions, workload, first-message end_time, chosen_time, deleted chosen row) — all ten classes are caught.

**Current status on `df_choicesets_17_07_2026.csv`:** all whole-file invariants hold (1,872,830 sets); 322/322 sampled sets pass `validate_n_messages`; the only `checker` failures are the deliberately sampled `no_chosen` sets.

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
5. **Checker reference-data mismatch — RESOLVED.** The checker read raw `merged_session_events.csv`, but choice sets are built from Stage 1 *output* (type=7 fix, updated end_times, merged type-2, propagated id_rep), so every session Stage 1 transformed produced a false mismatch. `official_checker_18_04.py` now reads `df_1_not_merged_2_merged.csv` (Stage 1 output) plus `df_exploded_all_data.csv` (Stage 2). Consequence: the checker validates **Stage 2 + Stage 3**; Stage 1's own transformations are out of its scope.
6. **Concurrency bounds — RESOLVED (lower bound → `queue_exit_time`/assignment; upper bound → `<=`).** The mask is `(queue_exit_time < T) & (chat_end_time >= T)` for choice moment `T`. Deep investigation of what these metadata fields are and which bound (if any) wrongly excludes waiting sessions:
   - **What the fields are (proved empirically):** `chat_start_time` **is** the session's **first-message `start_time`** (84.8% exact; diff = 0 at 25/50/75 pct) — i.e. when the customer *sent*, not "agent assigned" as the docs claim. `queue_exit_time` = `chat_start_time + queue_time` (arithmetic identity, ±1 s) = the **assignment / agent-visible** moment. `chat_end_time` is **NOT** the last-message end — only 0.1% match; it's a **late session-close/timeout stamp**, median ~**1,192 s (20 min)** *after* the last event (99.8% > last activity) — a generous marker.
   - **Lower bound — switched to assignment time `queue_exit_time` (advisor decision).** The pre-assignment queue wait (`chat_start → queue_exit`) is irrelevant to the agent's choices: the session wasn't on their plate yet. This aligns Stage 2 with Stage 1's type=7 fix (which already measures `waiting_time` from the agent-visible moment). **Verified safe: 0 new no-chosen sets** across 1,912,783 chosen sessions (an agent is never assigned a session *after* replying, so `queue_exit < reply` always holds for the chosen one). Effect on `workload`: mean **6.60 → 6.25** (~5% lower; median 6, p90 10 unchanged) — a genuine covariate shift, so results are **not** comparable across this change. **`outcome==4` (known-abandonment) sessions are dropped from the pool first** (customer gave up in queue, never assigned; bijection with `queue_exit_time==0`), which both matches Stage 1 and removes every 0-sentinel so no fallback is needed. *(Prior analysis of keeping `chat_start` as a strict `<` lower bound — the 375 early-message sessions, 152 excluded pending alternatives, the 696 proactive openers — is superseded by this switch but was the evidence that the lower bound wasn't wrongly excluding *served* customers.)*
   - **Upper bound — loosen to `<=` (same-second only).** `chat_end_time` is whole-second; a reply can land on the exact second of the metadata close while the visitor turn is open. Strict `>` dropped **53** genuinely closed-but-active *chosen* sessions (+~228 good-set alternatives; **281** total). `ends > reply` → `ends >= reply` recovers them (reply coincides with close = agent actively answering).
   - **The "35k dropped waiting alternatives" scare — a mirage, no further change.** Because `chat_end` is a *late* stamp, ~**35,167** sessions have a pending `type=1` at some same-agent reply `T ≥ chat_end` (within 1 h). Classified: **47%** served + short single trailing message (courtesy/closing, e.g. "thanks"), **12.4%** abandonment (`outcome=3`, customer already left), **40.6%** served + substantive trailing message. All occur *after* an already-generous close, so the customer is almost certainly gone — none are clear live waiting customers. The strict-ish upper bound (with the `<=` same-second fix) is therefore correct; **do not** switch to event-stream-based concurrency to "recover" them.
   - **Net change to Stage 2:** lower bound → `queue_exit_time` + drop `outcome==4`; upper bound `<=`. **Takes effect on the next Stage 2 re-run.**
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
9. **Singleton choice sets — 65.4% of the output — OPEN (policy).** Surfaced by the rewritten checker's structural pass on `df_choicesets_17_07_2026.csv`: **1,225,607 of 1,872,830** sets contain exactly **one** alternative (mean alternatives/set = **1.40**). Not a bug — Stage 2 only emits choice moments with >1 concurrent session, and Stage 3 then skips every alternative with no pending visitor turn, so these are sets where all competitors had already been answered. But a one-alternative set contributes **zero information to a conditional logit** (its likelihood contribution is identically 1). Decide whether to drop them before estimation and, more importantly, whether they indicate the concurrency window is too generous (a session counts as "competing" from assignment to `chat_end` even while it has nothing pending). Related to the open policy question in #8.
10. **Unresolved `id_rep = 1` placeholder — 3,567 sets (0.2%) — OPEN (minor).** Stage 1's `rep_fix` propagates the first real `id_rep` back over the pre-assignment placeholder `1` per `(id_session, subsession)`; when a subsession contains **no** real agent id at all, the placeholder survives into the choice-set table. This is most of the 4,169 sets where `id_rep` varies *within* a set (the rest are transferred sessions, which appear in `merged_session.csv` under more than one agent). Harmless for estimation as long as `id_rep` is not used as a fixed effect on the choice-set table — Stage 2's concurrency uses the *session metadata* `id_rep`, not the event row's — but worth a fallback in `rep_fix` (e.g. propagate across subsessions) if agent-level covariates are ever added.

---

## Repo / Git Notes

- `.gitignore`: `*.csv`, `*.pdf`, `*.ipynb`, `*.log`, `big_code_*.txt`, `__pycache__/`, `*.pyc`, `.Rhistory` — data files, notebooks, logs, and cruft are **not** tracked. Pulling/cloning will not bring CSVs; copy data manually.
- `Choiceset_error_analysis.ipynb` was un-tracked via `git rm --cached` and is ignored on `main`. It lives in the **repo root** (not `notebooks/`) because it reads ~14 data CSVs by bare filename from the root.
- Remote: `origin` → `https://github.com/TamirS94/Service-system-research.git`.
- Reference docs: `README.md` + `CLAUDE.md` (root), `docs/data_documentation.md`, `validation/Algorithem_Checker.txt`.
