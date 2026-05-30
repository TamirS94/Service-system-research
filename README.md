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

### Event Types

| `event_type` | Meaning |
|------|------|
| `1` | Customer message |
| `2` | Agent reply |
| `7` | Customer leaves queue — agent first sees the session |
| `8`, `9` | Internal system events (removed in pipeline) |

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
| **Workload** | Number of concurrent sessions an agent holds at the choice moment, normalized by 11 |

---

## Data Construction Pipeline

The dataset is created through a three-stage ELT process implemented entirely in Python (Pandas-based processing).

---

### 1️⃣ Stage 1 — Cleaning, Sorting, and Unifying Consecutive Agent Messages

**Script:** `Stage 1 - Cleaning, sorting and unifying consecutive agent messages_v2.py`  
**Output:** `df_1_not_merged_2_merged.csv`

- Loads raw `merged_session_events.csv`
- Removes silent abandonments (`outcome = 4`) and internal event types 8 and 9
- Fixes `id_rep = 1` artifacts: early customer messages are recorded with a placeholder agent ID before assignment. The first real agent ID in the session is propagated back to replace these
- **event_type=7 fix:** when a customer leaves the queue (`event_type = 7`), the agent sees the session for the first time at that moment — not at the preceding customer message. Each `event_type = 1` immediately followed by `event_type = 7` in the same session has its `end_time` and `end_date` updated to the type=7 values. All type=7 rows are then dropped
- Merges consecutive same-type agent messages (event_type=2) within the same session: summing `sentiment`, `duration`, `number_words`; keeping first-row values for everything else

---

### 2️⃣ Stage 2 — Creating Concurrencies and Exploded Table

**Script:** `Stage 2 - Creating concurrencies & exploded table.py`  
**Output:** `df_exploded_all_data.csv`

- Loads Stage 1 output and session-level `merged_session.csv`
- For each agent reply (event_type=2), identifies all sessions **active at that moment** for the same agent: `chat_start_time < choice_time < chat_end_time`
- Computes `workload = len(concurrent_sessions) / 11`
- Explodes so each row = one alternative in a choice set; `chosen = 1` if the concurrent session equals the session actually replied to
- Retains only choice sets with more than one concurrent session (genuine choices)

---

### 3️⃣ Stage 3 — Building the Choice Set Regression Table

**Script:** `stage_3_creating_choicesets_from_exploded.py`  
**Output:** `df_choicesets_<date>.csv`

For each alternative in the exploded table, Stage 3 identifies the customer's **pending turn** — all consecutive `event_type = 1` messages sent since the session's last agent reply (`event_type = 2`) and before the choice moment.

If no pending visitor messages exist (the session's last event was an agent reply), the alternative is excluded — the agent has no unanswered message to act on.

**Aggregation of the pending turn:**

| Treatment | Columns |
|------|------|
| **Sum** | `number_words`, `number_chars`, `number_lines`, `duration`, `sentiment` |
| **First** | all other columns (including `end_time`, used for `waiting_time`) |
| **Count** | `n_messages` — number of messages in the pending turn |
| **Dropped** | `read_date`, `read_time`, `accept_date`, `accept_time`, `delay` |

`waiting_time` is computed as `choice_time − end_time` where `end_time` is taken from the **first** message in the pending turn. This reflects the total duration the customer has been waiting since they initiated the current unanswered turn.

Each output row corresponds to one customer alternative in one choice set, with all predictor variables derived from the customer's pending turn.

---

## Final Dataset

Each row corresponds to a candidate customer session within a decision moment.

| `choice_set` | `id_rep` | `id_session` | `waiting_time` | `number_words` | `n_messages` | `workload` | `sentiment` | `chosen` |
|------|------|------|------|------|------|------|------|------|
| 1042 | 30000002 | 100063972 | 149 | 312 | 3 | 0.36 | 2.1 | 1 |
| 1042 | 30000002 | 100151963 | 67 | 104 | 1 | 0.36 | 0.8 | 0 |
| 1042 | 30000002 | 100057443 | 20 | 58 | 1 | 0.36 | 1.2 | 0 |

### Important Property
Within each `choice_set`, **exactly one observation has `chosen = 1`**.

This structure is required for estimation using conditional logistic regression.

---

## Data Quality

The pipeline includes a validation script (`official_checker_18_04.py`) that checks choice sets against the raw data across three dimensions:

- **Message count integrity:** the number of raw customer messages in `[end_time, chosen_time]` matches `n_messages`
- **Chosen flag accuracy:** the session marked `chosen = 1` corresponds to the session with an agent reply at exactly `chosen_time` in the raw data
- **Alternative completeness:** the set of sessions in the choice set matches the set of sessions whose last raw event before `chosen_time` is a customer message

### Known Fixes Applied

| Issue | Root Cause | Fix |
|------|------|------|
| ~51,516 no-chosen choice sets | `event_type = 7` rows appeared as the most recent event before choice time, causing Stage 3 to silently drop those alternatives | Stage 1 v2: propagate type=7 `end_time`/`end_date` to the preceding type=1, then drop type=7 rows |
| Remaining no-chosen choice sets | Sessions whose last event before choice time was an agent reply (no pending customer messages) were included as alternatives by Stage 2 but produced no row in Stage 3 | Stage 3 v2: explicitly skip sessions with no pending type=1 messages; ~4,458 choice sets remain invalid and are excluded before regression |

### Current State
- No-chosen choice sets: **~4,458** (down from ~51,516 before the event_type=7 fix)
- Of multi-alternative no-chosen sets: 100% have the chosen session absent from the output (Stage 3 found no valid pending turn) — zero cases of a present session being mislabeled `chosen = 0`
- `n_messages` distribution is healthy: the vast majority of alternatives have 1–2 messages; extreme values (e.g., `n_messages = 19`) correspond to genuinely neglected sessions that appeared repeatedly as unchosen alternatives

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
Stage 1 - Cleaning, sorting and unifying consecutive agent messages.py
Stage 1 - Cleaning, sorting and unifying consecutive agent messages_v2.py   ← event_type=7 fix applied
Stage 2 - Creating concurrencies & exploded table.py
stage_3_creating_choicesets_from_exploded.py                                 ← consecutive message aggregation
official_checker_18_04.py                                                    ← validation functions
choicests_after_unification_customer_msgs_21_03.py                           ← post-hoc unification reference
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
Place `merged_session_events.csv` and `merged_session.csv` in the repository root.

### 4. Run pipeline
```bash
python "Stage 1 - Cleaning, sorting and unifying consecutive agent messages_v2.py"
python "Stage 2 - Creating concurrencies & exploded table.py"
python stage_3_creating_choicesets_from_exploded.py
```

### Final dataset will appear as:
```
df_choicesets_<date>.csv
```
