# 🧠 Agent Response Choice Modeling from Chat Service Logs

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)]()
[![Pandas](https://img.shields.io/badge/Pandas-Data%20Processing-150458.svg)]()
[![NumPy](https://img.shields.io/badge/NumPy-Numerical%20Computing-013243.svg)]()
[![License](https://img.shields.io/badge/License-Academic-lightgrey.svg)]()

---

## Overview
This project constructs an **econometric choice dataset** from raw operational chat logs of an telecommunication online messeging customer service center using a fully Python-based data pipeline.

Customer service workers (i.e., agents) often observe multiple waiting customers (i.e., visitors) simultaneously and must decide **which customer to respond to next**.  
Operational event logs record system activity, but not the decision itself.

This repository reconstructs the agent decision environment and produces a dataset that allows modeling:

> **What predicts an agent choosing one waiting customer over others?**

The final dataset contains rows representing *competing customer messages at a specific decision moment* -> Choice-set.

---

## Research Motivation

Whenever an agent sends a reply, an implicit prioritization occurs:

**One customer is selected from several waiting customers.**

We formalize this as a **choice set**:

> A *choice set* is the set of customers waiting for an agent at the exact moment the agent responds.

Inside each choice set:
- one alternative (a visitior's messege) is **chosen**
- all other waiting customers are **not chosen**

This converts operational logs into a **behavioral decision dataset** suitable for discrete choice modeling (a type of logistic regression that considers grouping, as in our case, by choice-sets).

---

## Raw Data

The source data is an event-based messaging system containing:

- Customer messages
- Agent replies
- Assignment events
- Session transfers
- Session start and close events
- Timestamps for all actions

### Key Fields

| Field | Description |
|------|------|
| `session_id` | Unique conversation |
| `agent_id` | Assigned service agent |
| `event_time` | Timestamp of action |
| `event_type = 1/2` | Customer or agent |
| `session_start_time` | When session enters the agent queue |

The logs describe **what happened operationally**, but not **what alternatives the agent had**.  
The main contribution of this project is reconstructing those alternatives.

---

## Key Definitions

| Term | Meaning |
|------|------|
| **Session** | A conversation between a customer and the service center |
| **Session_start_time** | When a session becomes available to an agent |
| **Choice Set** | All customers waiting at that decision moment |
| **Chosen Observation** | The session the agent replied to |
| **Non-Chosen Observations** | Waiting sessions not selected |

---

## Data Construction Pipeline

The dataset is created through an ELT process implemented entirely in Python (Pandas-based processing).

### 1️⃣ Identify Decision Events
All agent reply messages are extracted.
agent sends message → a decision occurred


Each reply becomes a choice-set anchor.

Extracted:
- `agent_id`
- `session_id`
- `Waiting_time`

---

### 2️⃣ Reconstruct the Waiting Queue
For each decision moment `tᵢ`, a session is considered waiting if:

- last customer message time < `tᵢ`
- agent has not replied yet
- session assigned before `tᵢ`
- session still open

This recreates the agent’s operational queue at the time of decision.

---

### 3️⃣ Build the Choice Set
For every reply, all waiting sessions are collected:

| choice_set_id | agent | session |
|------|------|------|

We then label the chosen session:
chosen = 1 if replied at tᵢ
chosen = 0 otherwise


Each row now represents a customer the agent *could have responded to*.

---

### 4️⃣ Feature Engineering
For every alternative, explanatory variables are computed.

**Waiting Time**
wait_time = reply_time − last_customer_message_time




**Queue Context**
- queue size
- relative queue position
- concurrency

**Agent Workload**
- number of simultaneous sessions

**Session Characteristics**
- session age
- number of messages
- sentiment (optional)
- transfer history

---

## Final Dataset

Each row corresponds to a candidate customer within a decision.

| choice_set_id | agent_id | session_id | wait_time | queue_size | workload | chosen |
|------|------|------|------|------|------|------|
| 1042 | A12 | S88 | 95s | 4 | 3 | 1 |
| 1042 | A12 | S91 | 40s | 4 | 3 | 0 |
| 1042 | A12 | S93 | 12s | 4 | 3 | 0 |
| 1042 | A12 | S99 | 7s | 4 | 3 | 0 |

### Important Property
Within each `choice_set_id`, **exactly one observation has `chosen = 1`**.

This structure allows estimation using conditional discrete choice models.

---

## Why This Matters
Service systems typically assume FIFO queueing behavior.

This dataset allows testing whether agents actually prioritize:

- long-waiting customers
- complex conversations
- quick interactions
- purchase-oriented customers
- low-effort sessions

The project connects:
- Queueing theory
- Human decision making
- Behavioral operations research

---

## Repository Structure
/python
data_cleaning.py
session_reconstruction.py
choice_set_construction.py
feature_engineering.py

/notebooks
validation.ipynb
dataset_sanity_checks.ipynb

/output
final_choice_dataset.parquet



---

## How to Reproduce

### 1. Clone repository
```bash
git clone https://github.com/<your-username>/<repo-name>.git
cd <repo-name>
### 2. Install dependencies
pip install -r requirements.txt

### 3. Run pipeline

python python/data_cleaning.py
python python/session_reconstruction.py
python python/choice_set_construction.py
python python/feature_engineering.py

### Final dataset will appear in:
/output/final_choice_dataset.parquet

# 🧠 Agent Response Choice Modeling from Chat Service Logs

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)]()
[![Pandas](https://img.shields.io/badge/Pandas-Data%20Processing-150458.svg)]()
[![NumPy](https://img.shields.io/badge/NumPy-Numerical%20Computing-013243.svg)]()
[![License](https://img.shields.io/badge/License-Academic-lightgrey.svg)]()

---

## Overview
This project constructs an **econometric choice dataset** from raw operational chat logs of an e-commerce customer service center using a fully Python-based data pipeline.

Customer service agents often observe multiple waiting customers simultaneously and must decide **which customer to respond to next**.  
Operational event logs record system activity, but not the decision itself.

This repository reconstructs the agent decision environment and produces a dataset that allows modeling:

> **What predicts an agent choosing one waiting customer over others?**

The final dataset contains rows representing *competing customer messages at a specific decision moment*.

---

## Research Motivation

Whenever an agent sends a reply, an implicit prioritization occurs:

**One customer is selected from several waiting customers.**

We formalize this as a **choice set**:

> A *choice set* is the set of customers waiting for an agent at the exact moment the agent responds.

Inside each choice set:
- one alternative is **chosen**
- all other waiting customers are **not chosen**

This converts operational logs into a **behavioral decision dataset** suitable for discrete choice modeling.

---

## Raw Data

The source data is an event-based messaging system containing:

- Customer messages
- Agent replies
- Assignment events
- Session transfers
- Session start and close events
- Timestamps for all actions

### Key Fields

| Field | Description |
|------|------|
| `session_id` | Unique conversation |
| `agent_id` | Assigned service agent |
| `event_time` | Timestamp of action |
| `event_type = 1/2` | Customer or agent |
| `session_start_time` | When session enters the agent queue |

The logs describe **what happened operationally**, but not **what alternatives the agent had**.  
The main contribution of this project is reconstructing those alternatives.

---

## Key Definitions

| Term | Meaning |
|------|------|
| **Session** | A conversation between a customer and the service center |
| **Session_start_time** | When a session becomes available to an agent |
| **Choice Set** | All customers waiting at that decision moment |
| **Chosen Observation** | The session the agent replied to |
| **Non-Chosen Observations** | Waiting sessions not selected |

---

## Data Construction Pipeline

The dataset is created through an ELT process implemented entirely in Python (Pandas-based processing).

### 1️⃣ Identify Decision Events
All agent reply messages are extracted.
agent sends message → a decision occurred


Each reply becomes a choice-set anchor.

Extracted:
- `agent_id`
- `session_id`
- `Waiting_time`

---

### 2️⃣ Reconstruct the Waiting Queue
For each decision moment `tᵢ`, a session is considered waiting if:

- last customer message time < `tᵢ`
- agent has not replied yet
- session assigned before `tᵢ`
- session still open

This recreates the agent’s operational queue at the time of decision.

---

### 3️⃣ Build the Choice Set
For every reply, all waiting sessions are collected:

| choice_set_id | agent | session |
|------|------|------|

We then label the chosen session:
chosen = 1 if replied at tᵢ
chosen = 0 otherwise


Each row now represents a customer the agent *could have responded to*.

---

### 4️⃣ Feature Engineering
For every alternative, explanatory variables are computed.

**Waiting Time**
wait_time = reply_time − last_customer_message_time




**Queue Context**
- queue size
- relative queue position
- concurrency

**Agent Workload**
- number of simultaneous sessions

**Session Characteristics**
- session age
- number of messages
- sentiment (optional)
- transfer history

---

## Final Dataset

Each row corresponds to a candidate customer within a decision.

| choice_set_id | agent_id | session_id | wait_time | queue_size | workload | chosen |
|------|------|------|------|------|------|------|
| 1042 | A12 | S88 | 95s | 4 | 3 | 1 |
| 1042 | A12 | S91 | 40s | 4 | 3 | 0 |
| 1042 | A12 | S93 | 12s | 4 | 3 | 0 |
| 1042 | A12 | S99 | 7s | 4 | 3 | 0 |

### Important Property
Within each `choice_set_id`, **exactly one observation has `chosen = 1`**.

This structure allows estimation using conditional discrete choice models.

---

## Why This Matters
Service systems typically assume FIFO queueing behavior.

This dataset allows testing whether agents actually prioritize:

- long-waiting customers
- complex conversations
- quick interactions
- purchase-oriented customers
- low-effort sessions

The project connects:
- Queueing theory
- Human decision making
- Behavioral operations research

---

## Repository Structure
/python
data_cleaning.py
session_reconstruction.py
choice_set_construction.py
feature_engineering.py

/notebooks
validation.ipynb
dataset_sanity_checks.ipynb

/output
final_choice_dataset.parquet



---

## How to Reproduce

### 1. Clone repository
```bash
git clone https://github.com/<your-username>/<repo-name>.git
cd <repo-name>
### 2. Install dependencies
pip install -r requirements.txt

### 3. Run pipeline

python python/data_cleaning.py
python python/session_reconstruction.py
python python/choice_set_construction.py
python python/feature_engineering.py

### Final dataset will appear in:
/output/final_choice_dataset.parquet
