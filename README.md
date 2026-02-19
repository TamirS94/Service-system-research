🧠 Agent Response Choice Modeling from Chat Service Logs
Overview

This project constructs an econometric choice dataset from raw operational chat logs of an e-commerce customer service center.

Agents in the service center simultaneously observe multiple waiting customers and decide which customer to answer next.
We transform system event logs into structured data that allows modeling:

What predicts an agent choosing one waiting customer over others?

The output of this ELT process is a dataset where each row represents a customer message competing for the agent’s attention at a specific decision moment.

The dataset can be used for:

Discrete choice models (Conditional Logit / Mixed Logit)

Queue prioritization analysis

Behavioral operations research

Service fairness and workload allocation research

Research Idea

At any moment an agent sends a reply, they implicitly make a selection:

They choose one customer
from
several concurrently waiting customers

We define this moment as a:

Choice Set — the set of customers available to be answered when the agent responded.

Inside each choice set:

One message is chosen

All others are not chosen

This converts operational data into a behavioral decision dataset.

Raw Data (System Logs)

The original database is an event-based messaging system containing:

customer messages

agent responses

assignment events

session transfers

timestamps

Important fields:

session_id

agent_id

event_time

message_sender (agent / customer)

assignment_time

session start / end events

The logs describe what happened operationally —
but not what alternatives the agent had.

The core challenge of this project is reconstructing those alternatives.

Key Definitions
Session

A conversation between a customer and the service center.

Assignment Time

The moment a session becomes available to an agent queue.

Decision Moment

The exact timestamp when an agent sends a reply.

Choice Set

All customers waiting for a response from that agent at the decision moment.

Chosen Observation

The customer message the agent actually replied to.

Non-Chosen Observations

Customers who were waiting but not selected.

Data Construction Pipeline

The ELT process consists of four main stages.

1️⃣ Identify Agent Decision Events

We first locate agent reply messages in the chat logs.

A reply marks a real decision:

agent sends message  →  agent chose a customer


Each such event becomes a choice set anchor time.

We extract:

agent_id

session_id

reply timestamp tᵢ

2️⃣ Reconstruct the Waiting Queue

For every decision moment tᵢ, we identify which customers were waiting for that agent.

A customer is considered waiting if:

customer last message time < tᵢ
AND
agent has not yet replied to that message
AND
session assigned to agent before tᵢ
AND
session not closed before tᵢ


This step recreates the agent’s real operational queue.

3️⃣ Build the Choice Set

For each decision moment:

We gather all sessions satisfying the waiting conditions.

This produces a potential event table:

choice_set_id	agent	session	waiting_start_time

Then we label the chosen customer:

chosen = 1  if session replied at tᵢ
chosen = 0  otherwise


Now each row represents:

a customer the agent could have answered.

4️⃣ Feature Construction

We then compute explanatory variables for each alternative.

Examples:

Waiting Time
wait_time = tᵢ − last_customer_message_time

Queue Position

Relative age in queue within the choice set.

Concurrency

Number of waiting customers at decision time.

Agent Workload

Active concurrent sessions handled by the agent.

Session Characteristics

session length so far

number of customer messages

sentiment (optional)

transfer history

Final Dataset Structure

Each row is a candidate customer inside a decision.

choice_set_id	agent_id	session_id	wait_time	queue_size	workload	chosen
1042	A12	S88	95s	4	3	1
1042	A12	S91	40s	4	3	0
1042	A12	S93	12s	4	3	0
1042	A12	S99	7s	4	3	0

Important property:

Within each choice_set_id, exactly one row has chosen = 1.

This makes the dataset suitable for conditional logit estimation.

Why This Matters

Service centers usually assume agents follow FIFO queues.

This dataset allows testing whether agents actually prioritize:

long waiting customers

complex customers

short interactions

customers close to purchase

low effort conversations

This bridges:

Queueing theory

Human decision making

Behavioral operations

Repository Structure
/sql
    session_reconstruction.sql
    queue_reconstruction.sql
    choice_set_builder.sql

/python
    feature_engineering.py
    workload_metrics.py

/notebooks
    validation.ipynb
    dataset_sanity_checks.ipynb

/output
    final_choice_dataset.parquet

Validation Checks

We verify dataset correctness by:

exactly 1 chosen observation per choice set

no future information leakage

waiting times ≥ 0

session active at decision time

agent assigned before decision

Expected Modeling

The dataset is designed for:

Conditional Logit

Mixed Logit

Hazard models

Service prioritization policy learning

Authors

Research project conducted at [Faculty / University name]
