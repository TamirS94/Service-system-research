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

## Pipeline Overview (3 stages + checker)

Raw input: `merged_session_events.csv` (event-level, ~6M rows) and `merged_session.csv` (session-level time windows).

> **Inter-stage handoff is MANUAL.** Each script writes a dated/named CSV; you rename it by hand to match the next stage's expected input filename. The filenames below do **not** auto-chain — see Known Open Issues.

| Stage | Script | Reads | Writes |
|---|---|---|---|
| 1 | `stage_1.py` | `merged_session_events.csv` | `df_after_stage1_<date>.csv` |
| 2 | `Stage 2 - Creating concurrencies & exploded table.py` | `aggregated_df_11_5.csv` (precomputed) | `df_exploded_all_data.csv`, `before_third_stage_all_data.csv` |
| 3 | `stage_3_creating_choicesets_from_exploded.py` | `df_1_not_merged_2_merged.csv`, `df_exploded_all_data.csv` | `df_choicesets_<date>.csv` |
| ✓ | `official_checker_18_04.py` | `df_choicesets_<date>.csv`, `merged_session_events.csv` | validation report (stdout) |

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

`Stage 2 - Creating concurrencies & exploded table.py`.

- **The concurrency-extraction code is fully commented out.** The script currently resumes from a precomputed `aggregated_df_11_5.csv`. The commented logic: for each agent reply (type=2), find all sessions of the same `id_rep` where `chat_start_time < reply_end_time < chat_end_time` → `concurrent_sessions`.
- `workload = len(concurrent_sessions) / 11`.
- `literal_eval` parses the stringified list columns.
- Keep only rows with **>1 concurrent session** (genuine choices).
- **Explode** `concurrent_sessions` → one row per alternative. `chosen = 1` where `concurrent_sessions == session_id_chosen`.

---

## Stage 3 — Building the choice-set regression table

`stage_3_creating_choicesets_from_exploded.py`. For each alternative (concurrent session) at a choice moment:

- Take that session's events before `row.time` (the choice moment).
- Find the **last agent reply** (type=2). Collect all type=1 messages **after** it = the **pending turn** (consecutive unanswered visitor messages). If no type=2, all type=1 are pending.
- **If no pending type=1 → skip the alternative** (agent had no unanswered message to act on). This is the main reason some alternatives produce no row.
- **Aggregate the pending turn into one row:**
  - SUM: `sentiment, duration, number_words, number_chars, number_lines` (`COLS_TO_SUM`)
  - `n_messages` = count of messages in the turn
  - DROP: `read_date, read_time, accept_date, accept_time, delay` (`COLS_TO_DROP`)
  - FIRST-row value for everything else
- `waiting_time = row.time − end_time of the FIRST pending message`.
- Carries `choice_set` (= exploded row index), `chosen`, `workload`.

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
| `workload` | concurrent sessions / 11 |
| `chosen` | 1 for the replied-to session, 0 otherwise |

**event_type:** 1=visitor msg, 2=agent reply, 7=leaves queue (agent first sees session), 8/9=parallel-chat overhead (dropped).
**outcome:** 1=served, 2=served+transferred, 3=silent abandonment, 4=known abandonment, 5=reject invite, 6=no survey.

---

## Known Open Issues / Decisions Pending

1. **Abandonment filter — DECIDED.** Stage 1 drops `outcome == 4` (known abandonment) and this is the settled approach — known abandonment is excluded from the choice model. (Background: investigation showed both silent (3) and known (4) abandonment sessions can contain full agent–visitor interactions; the notebook explored dropping only known_abandonment sessions with an agent reply, but the decision is to drop all `outcome == 4`.)
2. **`stage_1.py` `__name__` bug — FIXED.** The `if __name__` block was indented inside `main()`. De-indented to module level.
3. **Filename drift / manual chain** — Stage 1 writes `df_after_stage1_<date>.csv` but Stage 3 reads `df_1_not_merged_2_merged.csv`; README cites a stale `..._v2.py` name. Handoff is currently manual renaming.
4. **Stage 2 concurrency code commented out** — relies on precomputed `aggregated_df_11_5.csv`; not runnable end-to-end from raw as-is.
5. **Checker reference-data mismatch** — checker reads raw `merged_session_events.csv`, but choice sets are built from Stage 1 *output* (which has type=7 fix, updated end_times, merged type-2, propagated id_rep). This causes false mismatches when a session was transformed in Stage 1.
6. **Check-3 failures (Stage 2 coverage)** — some sessions with an unanswered visitor message at the choice moment are excluded from the choice set because their session-level `chat_end_time` precedes the choice moment. Open modeling question: should a session that is "closed" in metadata but has an open visitor turn count as a competing alternative?

---

## Repo / Git Notes

- `.gitignore`: `*.csv`, `*.pdf`, `*.ipynb` — data files and notebooks are **not** tracked. Pulling/cloning will not bring CSVs; copy data manually.
- `Choiceset_error_analysis.ipynb` was un-tracked via `git rm --cached` and is now ignored on `main`.
- Remote: `origin` → `https://github.com/TamirS94/Service-system-research.git`.
- Reference docs in repo: `README.md`, `data_documentation.md`, `Algorithem_Checker.txt`, `claude_30_5.md` (earlier session log).
