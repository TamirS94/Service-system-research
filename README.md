# 🧠 Agent Response Choice Modeling from Chat Service Logs

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)]()
[![Pandas](https://img.shields.io/badge/Pandas-Data%20Processing-150458.svg)]()
[![NumPy](https://img.shields.io/badge/NumPy-Numerical%20Computing-013243.svg)]()
[![License](https://img.shields.io/badge/License-Academic-lightgrey.svg)]()

---

## Overview
This project constructs an **econometric choice dataset** from raw operational chat logs of a telecommunication online messaging customer service center using a fully Python-based data pipeline.

Customer service workers (i.e., agents) often observe multiple waiting customers (i.e., visitors) simultaneously and must decide **which customer to respond to next**.  
Operational event logs record system activity, but not the decision itself.

This repository reconstructs the agent decision environment and produces a dataset that allows modeling:

> **What customer behavior signals predict an agent choosing one waiting customer over others?**

The final dataset contains rows representing *competing customer messages at a specific decision moment* → Choice-set.

---

## Research Motivation

Whenever an agent sends a reply, an implicit prioritization occurs:

**One customer is selected from several waiting customers.**

We formalize this as a **choice set**:

> A *choice set* is the set of customers waiting for an agent at the exact moment the agent responds.

Inside each choice set:
- one alternative (a visitor's message turn) is **chosen**
- all other waiting customers are **not chosen**

This converts operational logs into a **behavioral decision dataset** suitable for conditional discrete choice modeling (conditional logistic regression). The model estimates what customer-side signals — waiting time, message volume, sentiment — drive agent prioritization decisions.

---

## Raw Data

The source data is an event-based messaging system containing:

- Customer messages
- Agent replies
- Queue assignment events
- Session transfers
- Session start and close events
- Timestamps for all actions

### Key Fields

| Field | Description |
|------|------|
| `id_session` | Unique conversation identifier |
| `id_rep` | Assigned service agent |
| `end_time` | Timestamp of event completion |
| `event_type` | Type of system event (see below) |
| `chat_start_time` | When session enters the agent queue |
| `chat_end_time` | When session closes |

The pipeline reads two raw inputs from the repository root:

- `raw_events_17_06.csv` — event-level log (clean column names, one row per message/event)
- `merged_session.csv` — session-level time windows (`chat_start_time` / `chat_end_time`)

> The older `merged_session_events.csv` is superseded by `raw_events_17_06.csv` and is retained only as a reference for the validation checker.

### Event Types

| `event_type` | Meaning |
|------|------|
| `1` | Customer message |
| `2` | Agent reply |
| `7` | Customer leaves queue — agent first sees the session |
| `8`, `9` | Parallel-chat overhead (removed in pipeline) |

The logs describe **what happened operationally**, but not **what alternatives the agent had**.  
The main contribution of this project is reconstructing those alternatives.

---

## Key Definitions

| Term | Meaning |
|------|------|
| **Session** | A conversation between a customer and the service center |
| **Choice moment** | The `end_time` of an agent reply — the moment a decision occurred |
| **Choice Set** | All customer sessions waiting for the same agent at a choice moment |
| **Chosen Observation** | The session the agent replied to (`chosen = 1`) |
| **Non-Chosen Observations** | Concurrent sessions not selected (`chosen = 0`) |
| **Pending turn** | All consecutive customer messages sent since the agent's last reply |
| **Waiting time** | Time elapsed from the first message in a pending turn to the choice moment |
| **Workload** | Number of concurrent sessions an agent holds at the choice moment (raw count) |
| **`flag`** | Marks an agent reply that is a re-engagement of a session the agent had already answered (see Stage 1) |

---

## Data Construction Pipeline

The dataset is created through a three-stage ELT process implemented entirely in Python (Pandas-based processing). All scripts live in `src/` and `chdir` to the repository root at startup, so the bare data filenames below always resolve there.

> **Orchestrator:** `src/run_pipeline.py` auto-chains all three stages as subprocesses, streaming output to the console and a timestamped log, passing each stage's output as the next stage's input. Run `python src/run_pipeline.py` from the repository root (or `--start-stage 3` to reuse existing Stage 1/2 outputs).

---

### 1️⃣ Stage 1 — Cleaning, Sorting, and Unifying Consecutive Agent Messages

**Script:** `src/stage_1_cleaning_and_unification.py`  
**Output:** `df_1_not_merged_2_merged.csv`

- Loads raw `raw_events_17_06.csv`
- Removes **known abandonment** sessions (`outcome = 4`) and parallel-chat overhead event types 8 and 9
- Fixes `id_rep = 1` artifacts: early customer messages are recorded with a placeholder agent ID before assignment. The first real agent ID in the session is propagated back to replace these
- **event_type=7 fix:** when a customer leaves the queue (`event_type = 7`), the agent sees the session for the first time at that moment — not at the preceding customer message. Each `event_type = 1` immediately followed by `event_type = 7` in the same session has its `end_time` and `end_date` updated to the type=7 values. All type=7 rows are then dropped
- **Agent-perspective unification:** consecutive agent replies (`event_type = 2`) are merged only when they represent the *same uninterrupted agent action*. Two type-2 rows merge iff they share the same `id_rep` **and** `id_session`, with no same-session customer message between them (a new turn) **and** no reply to a *different* session between them (a separate decision). This replaces the older session-perspective merge, which incorrectly fused replies that had an intervening reply to another concurrent session. Merging sums `sentiment`, `duration`, `number_words`; keeps first-row values otherwise.
- **`flag` column:** a type-2 row gets `flag = 1` when the old session-perspective logic *would* have merged it but the new agent-perspective logic correctly does *not* — i.e. the agent re-engaged a session it had already answered. This marks re-engagement replies for special handling in Stage 3.

---

### 2️⃣ Stage 2 — Creating Concurrencies and Exploded Table

**Script:** `src/stage_2_concurrencies_and_explode.py`  
**Output:** `df_exploded_all_data.csv`

- Loads Stage 1 output and session-level `merged_session.csv`
- For each agent reply (event_type=2), computes from scratch all sessions **active at that moment** for the same agent: `chat_start_time < choice_time < chat_end_time` → `concurrent_sessions`
- Computes `workload = len(concurrent_sessions)` (raw concurrent-session count)
- Retains only choice sets with more than one concurrent session (genuine choices)
- Explodes so each row = one alternative in a choice set; `chosen = 1` if the concurrent session equals the session actually replied to
- Carries the chosen reply's `flag` onto every alternative in the set (a choice-set-level attribute of the decision)

---

### 3️⃣ Stage 3 — Building the Choice Set Regression Table

**Script:** `src/stage_3_creating_choicesets_from_exploded.py`  
**Output:** `df_choicesets_<date>.csv`

For each alternative in the exploded table, Stage 3 identifies the customer's **pending turn** — all consecutive `event_type = 1` messages with `end_time >= ` the session's last agent reply (`event_type = 2`) and before the choice moment. (The `>=` lower bound honors Stage 1's canonical sort: a customer message sharing the exact whole-second of the last reply sorts *after* it and is therefore pending.)

If no pending visitor messages exist (the session's last event was an agent reply), the alternative is excluded — the agent has no unanswered message to act on.

**Aggregation of the pending turn:**

| Treatment | Columns |
|------|------|
| **Sum** | `number_words`, `number_chars`, `number_lines`, `duration`, `sentiment` |
| **First** | all other columns (including `end_time`, used for `waiting_time`) |
| **Count** | `n_messages` — number of messages in the pending turn |
| **Dropped** | `read_date`, `read_time`, `accept_date`, `accept_time`, `delay` |

`waiting_time` is computed as `choice_time − end_time` where `end_time` is taken from the **first** message in the pending turn. This reflects the total duration the customer has been waiting since they initiated the current unanswered turn. `chosen_time` (= `choice_time`) is emitted as a column for validation and sanity reconstruction.

**Re-engagement injection (`flag = 1`):** a re-engagement reply targets a session with no pending turn, which would otherwise produce a no-chosen choice set. For the chosen alternative of such a set, Stage 3 reconstructs the customer turn from the session's *genuine* reply and injects it as the `chosen = 1` row, measuring `waiting_time` to the genuine reply (the true response latency) rather than to the re-engagement moment.

Each output row corresponds to one customer alternative in one choice set, with all predictor variables derived from the customer's pending turn.

---

## Final Dataset

Each row corresponds to a candidate customer session within a decision moment.

| `choice_set` | `id_rep` | `id_session` | `waiting_time` | `number_words` | `n_messages` | `workload` | `sentiment` | `chosen` |
|------|------|------|------|------|------|------|------|------|
| 1042 | 30000002 | 100063972 | 149 | 312 | 3 | 4 | 2.1 | 1 |
| 1042 | 30000002 | 100151963 | 67 | 104 | 1 | 4 | 0.8 | 0 |
| 1042 | 30000002 | 100057443 | 20 | 58 | 1 | 4 | 1.2 | 0 |

### Important Property
Within each `choice_set`, **exactly one observation has `chosen = 1`**.

This structure is required for estimation using conditional logistic regression.

---

## Data Quality

The pipeline includes a validation script (`validation/official_checker_18_04.py`) that checks choice sets against the raw data across three dimensions:

- **Message count integrity:** the number of raw customer messages in `[end_time, chosen_time]` matches `n_messages`
- **Chosen flag accuracy:** the session marked `chosen = 1` corresponds to the session with an agent reply at exactly `chosen_time` in the raw data
- **Alternative completeness:** the set of sessions in the choice set matches the set of sessions whose last raw event before `chosen_time` is a customer message (or a type=7 queue-leave)

### Known Fixes Applied

| Issue | Root Cause | Fix |
|------|------|------|
| ~51,516 no-chosen choice sets | `event_type = 7` rows appeared as the most recent event before choice time, causing Stage 3 to silently drop those alternatives | Stage 1: propagate type=7 `end_time`/`end_date` to the preceding type=1, then drop type=7 rows |
| No-chosen sets from re-engagement | Agent re-engaged a session with no new pending customer turn → chosen alternative had no row | Stage 3 `flag = 1` re-engagement injection: reconstruct the chosen row from the session's genuine reply |
| Same-second tie drops | A customer message sharing the exact whole-second of the agent's last reply was excluded by a strict `>` comparison, creating spurious no-chosen sets | Stage 3 (and the checker) use `end_time >= last_reply`, consistent with Stage 1's `event_type desc` sort |

### Known Limitations
- **Tie-break ordering** (open): when a customer message and an agent reply share the exact whole-second `end_time` (~0.4% of choice moments), Stage 1's `event_type desc` rule assumes the reply came first. A `start_time` / file-order check suggests the customer message actually came first in ~60% of these ties. The pipeline is internally consistent but this convention is not yet corrected — deferred pending advisor consultation.
- **Proactive openers** (open decision): some no-chosen sets remain because the chosen session has no pending customer turn at the choice moment (the agent sent an opener the customer never answered). These are not yet filtered — an open modeling question of whether such replies should generate a choice set at all.

---

## Why This Matters

Service systems typically assume FIFO queueing behavior.

This dataset allows testing whether agents actually prioritize:

- long-waiting customers
- customers who sent many messages
- customers with high-sentiment messages
- quick, low-effort interactions
- sessions with low current workload

The project connects:
- Queueing theory
- Human decision making
- Behavioral operations research

---

## Repository Structure

```
src/
  stage_1_cleaning_and_unification.py        ← cleaning, rep_id fix, event_type=7 fix
  agent_unification.py                       ← agent-perspective merge + flag column
  stage_2_concurrencies_and_explode.py       ← concurrency extraction, exploded choice-set table
  stage_3_creating_choicesets_from_exploded.py ← pending-turn aggregation, final choice-set table
  run_pipeline.py                            ← orchestrator: chains Stage 1 → 2 → 3
validation/
  official_checker_18_04.py                  ← validation functions (validate_n_messages, checker)
  Algorithem_Checker.txt                     ← algorithm documentation
docs/
  data_documentation.md                      ← full field/dictionary reference
  *.pdf                                       ← reference documents
<root>/
  README.md, CLAUDE.md                       ← project documentation
  *.csv                                       ← raw / interim / output data (gitignored)
```

---

## How to Reproduce

### 1. Clone repository
```bash
git clone https://github.com/<your-username>/<repo-name>.git
cd <repo-name>
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Place input files
Place `raw_events_17_06.csv` and `merged_session.csv` in the repository root. (Data CSVs are gitignored and must be copied in manually.)

### 4. Run pipeline
Run the orchestrator from the repository root:
```bash
python src/run_pipeline.py
```
Or run a single stage (re-using existing earlier outputs):
```bash
python src/run_pipeline.py --start-stage 3
```
Stages can also be run individually:
```bash
python src/stage_1_cleaning_and_unification.py
python src/stage_2_concurrencies_and_explode.py
python src/stage_3_creating_choicesets_from_exploded.py
```

### Final dataset will appear as:
```
df_choicesets_<date>.csv
```
