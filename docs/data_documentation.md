# Data Documentation — Online Messaging Database

Source: Technion Center for Service Enterprise Engineering (SEE), March 2018.  
This summarizes the fields and dictionaries relevant to the choice-set analysis pipeline.

---

## Tables Overview

| Table | Used in Pipeline | Purpose |
|---|---|---|
| **Session Events** | Stage 1 (primary input) | One row per event within a session |
| **Sessions** | Stage 2 | Session-level time windows (chat_start_time, chat_end_time) |
| Agent Session | Not used | Agent shift-level status events |
| Agent Wait | Not used | Agent idle time during parallel chats |

---

## Session Events Table — Field Reference

This is the primary input table (`merged_session_events.csv`).

| Field | Description |
|---|---|
| `id_site` | Website ID where the chat was conducted |
| `id_session` | Customer visit number. **Note: same `id_session` can exist across different days — unique only within a single day** |
| `id_visitor` | Visitor identifier |
| `id_rep` | Agent ID. `id_rep = 1` means automatic system response (not a real agent) |
| `start_date` | Event start time, date format |
| `start_time` | Event start time, absolute seconds |
| `end_date` | Event end time, date format |
| `end_time` | Event end time, absolute seconds |
| `duration` | Duration of the event, in seconds |
| `number_words` | Number of words in the message (for event_type=1/2); for inner_wait/other_time: words typed by agent in other parallel chats |
| `number_chars` | Number of characters in the message; same dual meaning as number_words |
| `number_lines` | Number of lines; relevant mainly for inner_wait and other_time events |
| `answer_canned` | 1 = agent used a canned/template response; see Dictionary 7 |
| `sentiment` | Sentiment score of the visitor message based on text analysis. Zero = indifference |
| `sentiment_type` | Grouped sentiment category; see Dictionary 6 |
| `accept_date` / `accept_time` | Timestamp of message acceptance |
| `read_date` / `read_time` | Timestamp of message read |
| `event_type` | Type of event; see Dictionary 2 below |
| `event_type_desc` | Text description of event_type |
| `outcome` | Session termination type; see Dictionary 3 |
| `outcome_desc` | Text description of outcome |
| `subsession` | Subsession identifier, used for sessions transferred to another agent |
| `delay` | Control field: checks gaps/overlaps between queue end time and control line start time |
| `id_rep_code` | Anonymized agent ID code (SEEStat dictionary) |
| `id_agent` | Agent ID for the given subsession |
| `id_agent_code` | Anonymized agent ID code for the subsession |

---

## Sessions Table — Key Fields

Used in Stage 2 to define the active time window per session.

| Field | Description |
|---|---|
| `id_session` | Customer visit number |
| `id_rep` | Agent ID |
| `chat_start_time` | When the chat began (agent assigned, session active) |
| `chat_end_time` | When the chat ended |
| `queue_time` | Customer waiting time before chat started, in seconds |
| `is_interactive` | 1 = visitor participated in the chat (visitor line observed) |
| `transfer_agent` | 1 = chat was reassigned to another agent |
| `outcome` | Session termination type |
| `subsession` | Subsession identifier for transferred sessions |
| `inner_wait` | Customer waiting time during chat (agent handling other chats), in seconds |
| `other_time` | Time agent spent handling other parallel chats, in seconds |
| `average_sent` / `min_sent` / `max_sent` | Aggregate sentiment stats for the session |

---

## Dictionary 2 — event_type

The most important dictionary for the pipeline.

| code | name | description |
|---|---|---|
| `1` | visitor_line | **Customer message** — typing time / customer response |
| `2` | rep_line | **Agent message** — typing time when agent responds |
| `3` | control_line | System event |
| `4` | html_line | Agent response containing a link |
| `5` | unknown | — |
| `7` | queue | **Customer leaves queue** — time between customer submitting the service request and the system assigning an agent. This is the moment the agent first sees the session |
| `8` | other_time | Time agent handles other parallel chats (includes customer inner-wait) |
| `9` | inner_wait | Customer wait during chat while agent handles other sessions |
| `10` | end_time | System response at chat end |

### Pipeline treatment of event types

| event_type | Treatment |
|---|---|
| `1` | Kept — primary predictor source (customer messages) |
| `2` | Kept — defines choice moments (agent replies) |
| `7` | **Fixed in Stage 1 v2**: `end_time`/`end_date` of preceding type=1 is updated to type=7 values, then type=7 rows are dropped. Rationale: type=7 marks when the agent actually first sees the session, so waiting_time should be measured from this moment |
| `8`, `9` | Dropped in Stage 1 |

---

## Dictionary 3 — outcome

| code | name | description |
|---|---|---|
| `1` | served | Interactive chat with visitor and agent participation |
| `2` | served and transferred | Interactive, then transferred to another agent |
| `3` | silent abandonment | Visitor never replied — **removed in Stage 1** |
| `4` | known abandonment | Customer closed window before service started |
| `5` | reject invitation | Visitor did not answer the chat invitation |
| `6` | no survey | Customer submitted invitation but not the service survey |

---

## Dictionary 6 — sentiment_type

| code | name |
|---|---|
| `1` | positive sentiment |
| `2` | negative sentiment |
| `3` | neutral sentiment |
| `4` | not relevant |
| `5` | unknown |

---

## Dictionary 7 — answer_canned

| code | name |
|---|---|
| `1` | canned response (agent used template) |
| `2` | not canned response |
| `3` | not relevant |

---

## Notes for the Pipeline

- **`id_rep = 1`** is a system placeholder, not a real agent. Stage 1 propagates the first real agent ID per session to replace these artifacts.
- **`sentiment`** is visitor-side only (text analysis of customer messages). Zero means indifference/not computed.
- **`number_words` / `number_chars` / `duration`** have dual meanings: for event_type=1/2 they describe the message itself; for event_type=8/9 they describe the agent's activity in other parallel chats. Since types 8/9 are dropped, only the message-level meaning applies in our pipeline.
- **`subsession`** distinguishes segments of a session that were transferred between agents. The `id_session` stays the same across transfers.
- **`delay`** is a control/diagnostic field with no behavioral meaning — dropped in Stage 3.
- **`read_date`/`read_time`/`accept_date`/`accept_time`** are metadata timestamps with no behavioral relevance to the choice model — dropped in Stage 3.
