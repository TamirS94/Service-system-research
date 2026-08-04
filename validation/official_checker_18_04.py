# ==============================
# Official checker — validates the Stage 3 choice-set table
# ==============================
"""
Validates `df_choicesets_<date>.csv` (Stage 3 output) against the pipeline's own
inputs, so that a choice set really is "all sessions waiting for one agent at the
moment of an agent reply, exactly one of them chosen".

Reference data (IMPORTANT — this is what changed vs. the 18/04 version)
----------------------------------------------------------------------
The choice-set table is built from the **Stage 1 output** (`df_1_not_merged_2_merged.csv`),
not from the pre-pipeline raw events. Stage 1 rewrites the event stream: it drops
`outcome == 4` and `event_type` 8/9, propagates the real `id_rep` back onto early
customer rows, folds `event_type == 7` timestamps onto the preceding type-1 row (and
drops the type-7 rows), and merges consecutive agent messages from the agent's
perspective. Checking against a raw file therefore produced false failures on every
session Stage 1 touched (CLAUDE.md Known Issue #5). The checker now reads the Stage 1
output by default. Consequence: this validates Stage 2 + Stage 3; Stage 1's own
transformations are out of scope.

The **choice moment** (`T`) — the `end_time` of the agent reply that created the set —
is taken from the Stage 2 exploded table (`df_exploded_all_data.csv`, column `time`).
Every emitted row now carries `chosen_time == T` — Option-3 rows included, since they
borrow an earlier turn but are still measured to the choice moment — so the fallback to
`max(chosen_time)` when the exploded table is missing is exact rather than approximate.
The exploded table is still required for the concurrency lists (`no_extra_alternatives`,
`missing_alternatives_justified`, `workload_matches`), which have no other source.

What is checked
---------------
1. `structural_report()`  — vectorized, over the WHOLE file (cheap, no event lookups):
   one-chosen-per-set, singleton sets, waiting_time identity (bounded on Option-3 rows,
   whose clock starts at the genuine reply), sign/range sanity,
   per-set constancy of workload/flag/id_rep/choice moment, duplicate alternatives.
1b. `exploded_report()`   — vectorized, over the WHOLE file, against the Stage 2 table:
   every emitted alternative really was in the agent's concurrency list, `workload`
   equals that list's length, and `chosen_time` equals the choice moment on every row.
2. `validate_n_messages()` — per-row, event-level, on sampled sets: rebuilds each
   alternative's pending turn from the Stage 1 events using Stage 3's exact rules
   (including the `>=` same-second tie rule and the Option-3 reconstruction) and
   verifies n_messages, first-message end_time, waiting_time, chosen_time, the summed
   covariates, and that the turn was genuinely unanswered.
3. `checker()`            — per-set, on sampled sets: exactly one chosen; the chosen
   session is the one that actually replied at T; the emitted alternatives match the
   Stage 2 concurrency list, with every dropped alternative justified by Stage 3's
   "no pending visitor turn" skip rule; workload == len(concurrent_sessions).

Usage
-----
    python validation/official_checker_18_04.py                 # structural + segmented sample
    python validation/official_checker_18_04.py --sample 300    # + 300 random sets
    python validation/official_checker_18_04.py --choice-sets 3996,756
    python validation/official_checker_18_04.py --structural-only
    python validation/official_checker_18_04.py --choicesets df_choicesets_17_07_2026.csv
    python validation/official_checker_18_04.py --compact       # one line per choice set

Output
------
The whole-file passes mark `[OK]`/`[FAIL]` per invariant, and `[INFO]` for expected
categories that are not defects. The sampled passes print one block per choice set
listing EVERY check as `[TRUE ]` / `[FALSE]` / `[ n-a ]`, with both validators merged, so
one block answers "which checks did this choice set pass?". `n-a` = the check does not
apply to that set. Each segment ends with a per-check TRUE/FALSE/n-a tally.
"""

import argparse
import glob
import os
import sys
from collections import Counter

import numpy as np
import pandas as pd

# Make stdout/stderr UTF-8 so the report never dies on a legacy Windows codepage.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

pd.set_option("display.max_columns", None)
pd.set_option("display.max_rows", 500)
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (script lives in validation/)

# Kept in sync with stage_3_creating_choicesets_from_exploded.py
COLS_TO_SUM = ["sentiment", "duration", "number_words", "number_chars", "number_lines"]

DEFAULT_EVENTS = "df_1_not_merged_2_merged.csv"      # Stage 1 output = the reference event stream
DEFAULT_EXPLODED = "df_exploded_all_data.csv"        # Stage 2 output = choice moments + concurrency lists

REG_COLS = ["id_session", "id_rep", "end_time", "event_type", "flag", "n_messages",
            "choice_set", "chosen", "waiting_time", "chosen_time", "workload"] + COLS_TO_SUM
EVENT_COLS = ["id_session", "id_rep", "end_time", "event_type"] + COLS_TO_SUM
EXPLODED_COLS = ["index", "time", "concurrent_sessions"]   # index = choice_set id

SEGMENTS = ["high_events", "high_n_messages", "high_waiting_time", "high_workload",
            "low_waiting_time", "low_n_messages", "low_workload",
            "flag_reengagement", "no_chosen"]

# One-line meaning of every check name the two validators can emit (printed as a legend).
CHECK_DOCS = {
    # validate_n_messages (row level)
    "turn_nonempty": "the alternative has a pending visitor turn at T (Stage 3 would not have skipped it)",
    "turn_reconstructable": "Option-3 (flag=1 chosen) turn could be rebuilt from the session timeline",
    "chosen_time": "chosen_time equals the choice moment T",
    "n_messages": "n_messages equals the number of type-1 messages in the rebuilt turn",
    "first_msg_end_time": "the row's end_time is the FIRST pending message's end_time",
    "waiting_time": "waiting_time == T - clock start (first pending message; genuine reply on an Option-3 row)",
    "turn_before_choice": "every message of the turn happened strictly before T",
    "aggregation": "summed covariates equal the sum over the rebuilt turn",
    "turn_unanswered": "no agent reply inside the turn window (exactly one at T if chosen, none if not)",
    # checker (set level)
    "one_chosen": "the set has exactly one chosen == 1 row",
    "chosen_is_replier": "the chosen session is the (only) alternative with an agent reply at T",
    "no_extra_alternatives": "no emitted session is absent from the Stage 2 concurrency list",
    "missing_alternatives_justified": "every concurrency-list session Stage 3 dropped had no pending turn at T",
    "workload_matches": "workload equals len(concurrent_sessions) from Stage 2",
}


# ------------------------------------------------------------------ #
# Loading
# ------------------------------------------------------------------ #
def latest_choicesets_path() -> str:
    """Newest df_choicesets_*.csv in the repo root (the file Stage 3 just wrote)."""
    paths = glob.glob("df_choicesets_*.csv")
    if not paths:
        raise FileNotFoundError(
            "No df_choicesets_*.csv in the repo root — run the pipeline first "
            "(python src/run_pipeline.py) or pass --choicesets."
        )
    return max(paths, key=os.path.getmtime)


def _resolve_cols(path: str, wanted: list) -> tuple:
    """Map wanted (stripped) column names onto the file's actual header names."""
    header = pd.read_csv(path, nrows=0)
    stripped = {c.strip(): c for c in header.columns}
    present = [stripped[c] for c in wanted if c in stripped]
    missing = [c for c in wanted if c not in stripped]
    return present, missing


def read_csv_subset(path: str, wanted: list, key: str = None, keep_values=None,
                    chunksize: int = 1_000_000, label: str = "") -> pd.DataFrame:
    """Read only `wanted` columns, optionally keeping only rows whose `key` is in
    `keep_values`. Chunked so the big pipeline CSVs never land in memory whole."""
    usecols, missing = _resolve_cols(path, wanted)
    if missing:
        print(f"   note: {os.path.basename(path)} has no column(s) {missing} — skipping them")
    parts, total = [], 0
    for chunk in pd.read_csv(path, usecols=usecols, chunksize=chunksize):
        chunk.columns = chunk.columns.str.strip()
        total += len(chunk)
        parts.append(chunk if keep_values is None else chunk[chunk[key].isin(keep_values)])
    df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=usecols)
    print(f"   {label or os.path.basename(path)}: {total:,} rows scanned, {len(df):,} kept")
    return df


# ------------------------------------------------------------------ #
# Structural pass — whole file, vectorized, no event lookups
# ------------------------------------------------------------------ #
# Counts that are expected to be non-zero: they describe known categories of the
# output rather than pipeline defects, so they are reported but never fail the run.
INFORMATIONAL = {"no_chosen_set", "singleton_set", "id_rep_constant_in_set",
                 "id_rep_placeholder"}


def _print_findings(findings: dict, n_sets: int):
    for name, (n_bad, sample) in findings.items():
        if name in INFORMATIONAL:
            tag, pct = "[INFO]", f"  ({100 * n_bad / max(n_sets, 1):.1f}%)"
        else:
            tag, pct = ("[OK]  " if n_bad == 0 else "[FAIL]"), ""
        line = f"{tag} {name:<34} {n_bad:>9,} sets{pct}"
        if n_bad:
            line += f"   e.g. {sample}"
        print(line)


def structural_report(df_reg: pd.DataFrame) -> dict:
    """Invariants that must hold for EVERY choice set, checked without the event stream.

    Vectorized, so unlike the two event-level validators this runs over the whole file
    rather than a sample. Returns {check_name: (n_bad, [sample bad choice_set ids])}.
    """
    print("\n" + "=" * 70)
    print("STRUCTURAL PASS  (whole file)")
    print("=" * 70)

    g = df_reg.groupby("choice_set")
    n_sets, n_rows = df_reg["choice_set"].nunique(), len(df_reg)
    print(f"rows: {n_rows:,}   choice sets: {n_sets:,}   "
          f"mean alternatives/set: {n_rows / max(n_sets, 1):.2f}")

    chosen_per_set = g["chosen"].sum()
    size_per_set = g.size()
    findings = {}

    def record(name, bad_sets):
        bad_sets = pd.Index(bad_sets)
        findings[name] = (len(bad_sets), bad_sets[:5].tolist())

    # --- conditional-logit prerequisites -----------------------------------
    record("no_chosen_set", chosen_per_set.index[chosen_per_set == 0])
    record("multi_chosen_set", chosen_per_set.index[chosen_per_set > 1])
    # A single-alternative set carries no information for a conditional logit (its
    # likelihood contribution is identically 1). Not a defect, but it must be visible:
    # Stage 2 only emits sets with >1 concurrent session, so these are sets whose other
    # alternatives were all skipped by Stage 3 for having no pending visitor turn.
    record("singleton_set", size_per_set.index[size_per_set == 1])
    record("duplicate_alternative",
           df_reg.loc[df_reg.duplicated(["choice_set", "id_session"]), "choice_set"].unique())

    # --- per-row identities -------------------------------------------------
    # waiting_time = chosen_time - end_time holds for every normal alternative, whose
    # clock starts at its first pending message. It does NOT hold on an Option-3 row
    # (flag=1, chosen=1, no pending turn): there the clock starts at the genuine reply
    # instead, which is strictly later than the turn's first message, so the row's
    # waiting_time is strictly SHORTER than the identity would give. The whole-file pass
    # cannot see the genuine reply — validate_n_messages checks the exact value against
    # the events — so here we only bound it. flag=1 chosen rows that took the main path
    # (their session did have a pending turn) still satisfy the identity, hence the OR.
    span = df_reg["chosen_time"] - df_reg["end_time"]
    identity_ok = df_reg["waiting_time"] == span
    option3_shaped = (df_reg["flag"] == 1) & (df_reg["chosen"] == 1)
    bounded_ok = option3_shaped & (df_reg["waiting_time"] >= 0) & (df_reg["waiting_time"] < span)
    record("waiting_time_identity",
           df_reg.loc[~(identity_ok | bounded_ok), "choice_set"].unique())
    record("waiting_time_negative", df_reg.loc[df_reg["waiting_time"] < 0, "choice_set"].unique())
    record("n_messages_positive", df_reg.loc[df_reg["n_messages"] < 1, "choice_set"].unique())
    if "event_type" in df_reg.columns:
        # Every emitted row is an aggregated visitor turn, so it must be a type-1 row.
        record("row_is_visitor_msg", df_reg.loc[df_reg["event_type"] != 1, "choice_set"].unique())

    # --- per-set constancy --------------------------------------------------
    # workload and flag are choice-set-level attributes replicated onto every alternative
    # by Stage 2, so any variation inside a set means the explode or the carry-through
    # went wrong. id_rep is different: it comes from the visitor message's own row, so it
    # can legitimately vary (see below) — informational only.
    for col in ("workload", "flag", "id_rep"):
        if col in df_reg.columns:
            record(f"{col}_constant_in_set", g[col].nunique().pipe(lambda s: s.index[s > 1]))
    # Rows still carrying the pre-assignment placeholder id_rep == 1: Stage 1's rep_fix
    # found no real agent id for that (id_session, subsession). Explains most of the
    # id_rep variation above; the remainder are transferred sessions, which appear in
    # merged_session.csv under more than one agent.
    record("id_rep_placeholder", df_reg.loc[df_reg["id_rep"] == 1, "choice_set"].unique())

    # chosen_time is the choice moment T, written identically onto every alternative —
    # including the Option-3 chosen row of a flag=1 set, which borrows the earlier turn
    # but is still measured to T. So it must be constant within EVERY set, no exception.
    ct_var = g["chosen_time"].nunique()
    flag_of_set = g["flag"].max()
    record("chosen_time_constant_in_set", ct_var.index[ct_var > 1])

    # Surviving alternatives are a subset of the concurrency list, so the set can never
    # be larger than the workload it was built from.
    record("size_le_workload", size_per_set.index[size_per_set > g["workload"].max()])

    _print_findings(findings, n_sets)
    n_flag = int(flag_of_set.sum())
    print(f"\n  flag=1 (re-engagement) sets: {n_flag:,} ({100 * n_flag / max(n_sets, 1):.1f}%)")
    print("  [INFO] rows are expected categories, not defects: see CLAUDE.md 'Known Open")
    print("         Issues' #8 for the no-chosen breakdown. Only [FAIL] means a bug.")
    return findings


def exploded_report(df_reg: pd.DataFrame, exploded: pd.DataFrame) -> dict:
    """Whole-file cross-check of the choice-set table against the Stage 2 exploded table.

    Upgrades three of the strongest checks from "sampled" to "every choice set": the
    emitted alternatives really were in the agent's concurrency list, `workload` really
    is that list's length, and `chosen_time` really is the choice moment.
    """
    print("\n" + "=" * 70)
    print("STAGE 2 CROSS-CHECK  (whole file)")
    print("=" * 70)

    n_sets = df_reg["choice_set"].nunique()
    moment = exploded.groupby("index")["time"].first()
    n_alts = exploded.groupby("index").size()
    findings = {}

    def record(name, bad_sets):
        bad_sets = pd.Index(pd.unique(pd.Series(bad_sets)))
        findings[name] = (len(bad_sets), bad_sets[:5].tolist())

    # Every (choice_set, id_session) must exist as (index, concurrent_sessions).
    # Composite int64 key + sorted membership test — far cheaper than a 2.6M x 11.7M merge.
    shift = 1 << 32
    emitted_key = df_reg["choice_set"].to_numpy() * shift + df_reg["id_session"].to_numpy()
    conc_key = np.sort(exploded["index"].to_numpy() * shift + exploded["concurrent_sessions"].to_numpy())
    known = np.isin(emitted_key, conc_key, assume_unique=False)
    record("alternative_in_concurrency_list", df_reg.loc[~known, "choice_set"])

    # workload == len(concurrent_sessions), compared row by row so a single corrupted
    # row is caught even when the rest of the set is right.
    expected_wl = df_reg["choice_set"].map(n_alts)
    record("workload_matches_concurrency",
           df_reg.loc[(df_reg["workload"] != expected_wl) | expected_wl.isna(), "choice_set"])

    # chosen_time == the choice moment, on every row without exception. Option-3 rows
    # borrow an earlier turn but are still measured to T (they used to carry the genuine
    # reply time here, which made waiting_time incommensurable inside the set).
    T = df_reg["choice_set"].map(moment)
    is_option3_row = (df_reg["flag"] == 1) & (df_reg["chosen"] == 1)
    record("chosen_time_is_choice_moment",
           df_reg.loc[df_reg["chosen_time"] != T, "choice_set"])

    _print_findings(findings, n_sets)
    print(f"\n  flag=1 chosen rows (Option-3 candidates): {int(is_option3_row.sum()):,}")
    return findings


# ------------------------------------------------------------------ #
# Turn reconstruction — mirrors Stage 3 exactly
# ------------------------------------------------------------------ #
def pending_turn(session_events: pd.DataFrame, choice_moment) -> tuple:
    """Stage 3 main path: the consecutive type-1 messages since the session's last reply.

    Lower bound is `>=` last reply, not `>`: end_time is whole-second, so a visitor
    message can share the exact second of the agent's last reply and Stage 1's canonical
    sort (end_time asc, event_type desc) treats it as pending. Returns (turn, last_reply).
    """
    past = session_events[session_events["end_time"] < choice_moment]
    if past.empty:
        return past, None
    past_type2 = past[past["event_type"] == 2]
    if past_type2.empty:
        return past[past["event_type"] == 1], None
    last_reply = past_type2["end_time"].max()
    turn = past[(past["event_type"] == 1) & (past["end_time"] >= last_reply)]
    return turn, last_reply


def option3_turn(session_events: pd.DataFrame, choice_moment) -> tuple:
    """Stage 3 Option 3: rebuild the visitor turn a flag=1 re-engagement reply answered.

    The chosen session of a flag=1 set has nothing pending (the agent replied again to a
    session whose visitor said nothing new), so Stage 3 reuses the turn from that
    session's *genuine* reply. chosen_time is still T, like every other alternative, but
    the waiting clock STARTS at that genuine reply rather than at the turn's first message:
    nothing is pending, so the interval being measured is how long the session sat
    untouched since the agent last dealt with it.
    Returns (turn, genuine_reply_time) or (None, None) when it cannot be rebuilt.
    """
    past = session_events[session_events["end_time"] < choice_moment]
    type1_past = past[past["event_type"] == 1]
    if type1_past.empty:
        return None, None
    past_type2 = past[past["event_type"] == 2]
    last_type1_time = type1_past["end_time"].max()
    type2_after = past_type2[past_type2["end_time"] > last_type1_time]
    if type2_after.empty:
        return None, None
    genuine_reply_time = type2_after["end_time"].min()
    type2_before = past_type2[past_type2["end_time"] < last_type1_time]
    lower = type2_before["end_time"].max() if not type2_before.empty else float("-inf")
    turn = past[(past["event_type"] == 1)
                & (past["end_time"] >= lower)
                & (past["end_time"] <= last_type1_time)]
    return turn, genuine_reply_time


def _as_session_groups(events) -> dict:
    """Accept either an events DataFrame or an already-grouped dict."""
    if isinstance(events, dict):
        return events
    ev = events.copy()
    ev.columns = ev.columns.str.strip()
    # Same canonical order Stage 1 emits and Stage 3 relies on.
    ev = ev.sort_values(["id_session", "end_time", "event_type"], ascending=[True, True, False])
    return dict(tuple(ev.groupby("id_session")))


# ------------------------------------------------------------------ #
# Validator 1 — row-level (n_messages, waiting_time, aggregation)
# ------------------------------------------------------------------ #
def validate_n_messages(df_reg: pd.DataFrame, events, choice_moments: dict = None) -> dict:
    """Rebuild every alternative's pending turn from the Stage 1 events and compare it
    to the emitted row.

    `events` is the Stage 1 output (DataFrame or {id_session: events} dict).
    `choice_moments` maps choice_set -> T (from the Stage 2 exploded table); when it is
    None, T falls back to max(chosen_time) within the set, which is exact — every row,
    Option-3 included, is written with chosen_time == T.

    Returns {choice_set: {check_name: bool}} — a set passes when all its values are True.
    """
    print("\nrunning validate_n_messages (row-level, event-based)...")
    session_groups = _as_session_groups(events)
    results = {}

    for choice_set, choice_df in df_reg.groupby("choice_set"):
        T = (choice_moments or {}).get(choice_set)
        if T is None:
            T = choice_df["chosen_time"].max()
        checks = {}

        def fail(name):
            checks[name] = False

        def note(name, ok):
            checks[name] = bool(checks.get(name, True) and ok)

        for row in choice_df.itertuples(index=False):
            sess = session_groups.get(row.id_session)
            if sess is None or sess.empty:
                fail("turn_nonempty")
                continue

            # Mirror Stage 3's own branch order: try the main path, and fall back to the
            # Option-3 reconstruction exactly where Stage 3 does — the chosen row of a
            # flag=1 set whose pending turn came back empty. (This used to be detected via
            # `chosen_time != T`, which no longer works now that Option-3 rows also carry
            # T; keying off the branch condition itself is what Stage 3 actually does.)
            turn, _ = pending_turn(sess, T)
            used_option3 = False

            waiting_start = None            # None -> first pending message (set below)

            if turn.empty and row.chosen == 1 and getattr(row, "flag", 0) == 1:
                turn, genuine_reply = option3_turn(sess, T)
                if turn is None or turn.empty:
                    fail("turn_reconstructable")
                    continue
                used_option3 = True
                # The Option-3 clock starts at the genuine reply, not at the turn's first
                # message: nothing is pending, so what is being measured is how long the
                # session sat untouched since the agent last dealt with it.
                waiting_start = genuine_reply
                note("turn_reconstructable", True)
            elif turn.empty:
                fail("turn_nonempty")
                continue
            else:
                note("turn_nonempty", True)

            turn = turn.sort_values("end_time")
            first_end = turn["end_time"].iloc[0]
            if waiting_start is None:
                waiting_start = first_end

            # Measured TO T on every row, Option-3 included; only the start differs.
            note("chosen_time", row.chosen_time == T)
            note("n_messages", row.n_messages == len(turn))
            note("first_msg_end_time", row.end_time == first_end)
            note("waiting_time", row.waiting_time == T - waiting_start)
            note("turn_before_choice", bool((turn["end_time"] < T).all()))

            # Criterion 6 of Algorithem_Checker.txt — the aggregation itself.
            agg_ok = True
            for col in COLS_TO_SUM:
                if col in turn.columns and hasattr(row, col):
                    agg_ok = agg_ok and getattr(row, col) == turn[col].sum()
            note("aggregation", agg_ok)

            # The turn must be genuinely unanswered: no reply between its first message
            # and T. Exactly one reply lands AT T for the chosen session (that reply is
            # the choice moment); a non-chosen alternative must have none.
            # Strict `>` first_end mirrors Stage 3: a reply sharing the first message's
            # second IS the previous turn boundary, not an answer to this turn.
            replies_in_window = sess[(sess["event_type"] == 2)
                                     & (sess["end_time"] > first_end)
                                     & (sess["end_time"] <= T)]
            if row.chosen == 1:
                at_T = int((replies_in_window["end_time"] == T).sum())
                if used_option3:
                    # The genuine reply also sits in this window, by design.
                    note("turn_unanswered", at_T == 1)
                else:
                    note("turn_unanswered", len(replies_in_window) == 1 and at_T == 1)
            else:
                note("turn_unanswered", replies_in_window.empty)

        results[choice_set] = checks or {"turn_nonempty": False}
    return results


# ------------------------------------------------------------------ #
# Validator 2 — set-level (chosen identity, alternative coverage)
# ------------------------------------------------------------------ #
def checker(choicesets_list, df_choicesets: pd.DataFrame, events,
            exploded: pd.DataFrame = None) -> dict:
    """Set-level checks: is this really *the* agent's decision at T, over *all* the
    sessions that were waiting?

    Replaces the original 3 checks:
      1. chosen session == session that replied at T           -> kept (`chosen_is_replier`),
         now resolved against the choice moment from the Stage 2 exploded table.
      2. "no events after chosen_time in the window"           -> dropped: the window was
         already filtered to <= chosen_time, so the check could never fail.
      3. set(choice-set sessions) == set(sessions whose last raw event is type 1/7)
                                                               -> replaced by
         `no_extra_alternatives` + `missing_alternatives_justified`. Stage 2 defines
         concurrency from the SESSION metadata window (queue_exit_time < T <= chat_end_time),
         not from the event stream, so the old event-stream reconstruction disagreed with
         the pipeline by construction. The concurrency list itself is now the reference,
         and every alternative Stage 3 dropped from it must be explained by the documented
         skip rule (no pending visitor turn at T).
    """
    print("\nrunning checker (set-level)...")
    session_groups = _as_session_groups(events)
    exploded_by_set = {}
    if exploded is not None and len(exploded):
        exploded_by_set = dict(tuple(exploded.groupby("index")))

    results = {}
    for choice_set in choicesets_list:
        rows = df_choicesets[df_choicesets["choice_set"] == choice_set]
        if rows.empty:
            continue
        checks = {}
        exp = exploded_by_set.get(choice_set)
        T = exp["time"].iloc[0] if exp is not None and len(exp) else rows["chosen_time"].max()

        # 1. exactly one chosen alternative
        chosen_rows = rows[rows["chosen"] == 1]
        checks["one_chosen"] = len(chosen_rows) == 1

        # 2. the chosen session is the one that actually replied at T
        repliers = [s for s in rows["id_session"].unique()
                    if _replied_at(session_groups.get(s), T)]
        checks["chosen_is_replier"] = (
            len(chosen_rows) == 1 and repliers == [chosen_rows["id_session"].iloc[0]]
        )

        # 3. the emitted alternatives vs. the Stage 2 concurrency list
        if exp is None or not len(exp):
            checks["no_extra_alternatives"] = None          # exploded table unavailable
            checks["missing_alternatives_justified"] = None
            checks["workload_matches"] = None
        else:
            concurrent = set(exp["concurrent_sessions"])
            emitted = set(rows["id_session"])
            checks["no_extra_alternatives"] = not (emitted - concurrent)
            # Stage 3 may legitimately drop an alternative only when it had no pending
            # visitor turn at T; anything else dropped is lost signal.
            unjustified = []
            for sid in concurrent - emitted:
                sess = session_groups.get(sid)
                if sess is None or sess.empty:
                    continue
                turn, _ = pending_turn(sess, T)
                if not turn.empty:
                    unjustified.append(sid)
            checks["missing_alternatives_justified"] = not unjustified
            checks["workload_matches"] = int(rows["workload"].iloc[0]) == len(concurrent)

        results[choice_set] = checks
    return results


def _replied_at(sess: pd.DataFrame, T) -> bool:
    """Did this session receive an agent reply at exactly the choice moment?"""
    if sess is None or sess.empty:
        return False
    return bool(((sess["event_type"] == 2) & (sess["end_time"] == T)).any())


# ------------------------------------------------------------------ #
# Sampling
# ------------------------------------------------------------------ #
def segment_choice(df: pd.DataFrame, segment: str, quantile: float, prefix: str,
                   n: int = 5, rng: np.random.Generator = None) -> pd.DataFrame:
    """Sample `n` choice sets from the high/low tail of `segment`."""
    rng = rng or np.random.default_rng(0)
    threshold = df[segment].quantile(quantile)
    low = prefix.lower().startswith("low")
    ids = df.loc[df[segment] < threshold, "choice_set"].unique() if low else \
        df.loc[df[segment] > threshold, "choice_set"].unique()
    if len(ids) == 0:
        ids = df.loc[df[segment] == threshold, "choice_set"].unique()
    return _sample_sets(df, ids, n, rng)


def _sample_sets(df: pd.DataFrame, ids, n: int, rng: np.random.Generator) -> pd.DataFrame:
    """Take up to n ids (never raises when the pool is smaller than n) and slice df."""
    ids = np.asarray(ids)
    if len(ids) == 0:
        return df.iloc[0:0]
    picked = rng.choice(ids, size=min(n, len(ids)), replace=False)
    return df[df["choice_set"].isin(picked)]


def choose_choicets(df: pd.DataFrame, prefix: str, n: int = 5,
                    rng: np.random.Generator = None) -> pd.DataFrame:
    """Slice df_choicesets down to an interesting group of choice sets."""
    rng = rng or np.random.default_rng(0)
    prefix = prefix.lower()

    if prefix == "high_events":                       # sets with many alternatives
        sizes = df.groupby("choice_set").size()
        return _sample_sets(df, sizes.index[sizes >= 5], n, rng)
    if prefix == "flag_reengagement":                 # Option-3 path
        return _sample_sets(df, df.loc[df["flag"] == 1, "choice_set"].unique(), n, rng)
    if prefix == "no_chosen":                         # sets the model cannot use
        chosen = df.groupby("choice_set")["chosen"].sum()
        return _sample_sets(df, chosen.index[chosen == 0], n, rng)
    if prefix in ("high_n_messages", "high_waiting_time", "high_workload"):
        return segment_choice(df, prefix[len("high_"):], 0.85, prefix, n, rng)
    if prefix in ("low_n_messages", "low_waiting_time", "low_workload"):
        return segment_choice(df, prefix[len("low_"):], 0.10, prefix, n, rng)
    raise ValueError(f"unknown segment '{prefix}' (expected one of {SEGMENTS})")


# ------------------------------------------------------------------ #
# Reporting
# ------------------------------------------------------------------ #
CHECK_ORDER = list(CHECK_DOCS)          # stable column/line order across every report


def _fmt(value) -> str:
    """TRUE / FALSE / n-a (a check that does not apply to this choice set)."""
    return "TRUE " if value is True else "FALSE" if value is False else " n-a "


def _ordered(checks: dict) -> list:
    """Checks in canonical order, so every choice set prints the same way."""
    known = [(k, checks[k]) for k in CHECK_ORDER if k in checks]
    extra = [(k, v) for k, v in checks.items() if k not in CHECK_DOCS]
    return known + extra


def report_segment(row_results: dict, set_results: dict,
                   fail_rows: Counter, fail_sets: Counter, compact: bool = False) -> tuple:
    """Print every check, TRUE or FALSE, for every choice set in the segment.

    Both validators are merged into one block per choice set so the log answers
    "which checks did THIS choice set pass?" directly. `n-a` marks a check that does
    not apply (e.g. Option-3 reconstruction on a normal set, or the Stage 2 coverage
    checks when the exploded table is unavailable).
    """
    all_sets = sorted(set(row_results) | set(set_results))
    ok_rows = ok_sets = 0
    tally = {}                                    # check -> [n_true, n_false, n_na]

    for choice_set in all_sets:
        blocks = [("validate_n_messages (row level)", row_results.get(choice_set)),
                  ("checker (set level)", set_results.get(choice_set))]
        bad, lines = [], []
        for label, checks in blocks:
            if not checks:
                continue
            lines.append(f"    {label}")
            for name, value in _ordered(checks):
                lines.append(f"      [{_fmt(value)}] {name}")
                slot = tally.setdefault(name, [0, 0, 0])
                slot[0 if value is True else 1 if value is False else 2] += 1
                if value is False:
                    bad.append(name)

        n_true = sum(1 for _, c in blocks if c for v in c.values() if v is True)
        n_all = sum(len(c) for _, c in blocks if c)
        status = "PASS" if not bad else "FAIL"
        print(f"\n  choice_set {choice_set}  ->  {status}  ({n_true}/{n_all} checks TRUE)")
        if not compact:
            print("\n".join(lines))
        elif bad:
            print(f"      FALSE: {', '.join(bad)}")

        rows, sets_ = row_results.get(choice_set), set_results.get(choice_set)
        if rows is not None:
            failed = [k for k, v in rows.items() if v is False]
            fail_rows.update(failed)
            ok_rows += not failed
        if sets_ is not None:
            failed = [k for k, v in sets_.items() if v is False]
            fail_sets.update(failed)
            ok_sets += not failed

    print("\n  check tally for this segment (TRUE / FALSE / n-a):")
    for name in [k for k in CHECK_ORDER if k in tally] + [k for k in tally if k not in CHECK_DOCS]:
        t, f, na = tally[name]
        mark = " " if f == 0 else "<"
        print(f"    {name:<34} {t:>4} / {f:>4} / {na:>4}  {mark}")
    print(f"\n  validate_n_messages: {ok_rows}/{len(row_results)} sets fully TRUE"
          f"   |   checker: {ok_sets}/{len(set_results)} sets fully TRUE")
    return ok_rows, len(row_results), ok_sets, len(set_results)


def print_legend(failures: Counter):
    if not failures:
        print("\nNo failing checks.")
        return
    print("\nFailure summary (check -> number of choice sets):")
    for name, count in failures.most_common():
        print(f"  {name:<34} {count:>6}   {CHECK_DOCS.get(name, '')}")


def _verdict(findings: dict) -> bool:
    """Print the whole-file verdict. Informational categories never fail the run."""
    broken = {k: v[0] for k, v in findings.items() if k not in INFORMATIONAL and v[0]}
    print("\n" + "=" * 70)
    if broken:
        print("VERDICT: whole-file invariants VIOLATED -> " + ", ".join(
            f"{k} ({v:,} sets)" for k, v in broken.items()))
    else:
        print("VERDICT: all whole-file invariants hold.")
    print("=" * 70)
    return not broken


# ------------------------------------------------------------------ #
# Main
# ------------------------------------------------------------------ #
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--choicesets", default=None,
                   help="Stage 3 output to validate (default: newest df_choicesets_*.csv)")
    p.add_argument("--events", default=DEFAULT_EVENTS,
                   help=f"reference event stream = Stage 1 output (default: {DEFAULT_EVENTS})")
    p.add_argument("--exploded", default=DEFAULT_EXPLODED,
                   help=f"Stage 2 exploded table, source of the choice moment (default: {DEFAULT_EXPLODED})")
    p.add_argument("--no-exploded", action="store_true",
                   help="run without the Stage 2 table (coverage checks are skipped)")
    p.add_argument("--choice-sets", default=None,
                   help="comma-separated choice_set ids to validate instead of sampling")
    p.add_argument("--sample", type=int, default=0,
                   help="additionally validate N choice sets drawn uniformly at random")
    p.add_argument("--n-per-segment", type=int, default=5,
                   help="choice sets per segment (default: 5)")
    p.add_argument("--structural-only", action="store_true",
                   help="run only the whole-file passes (structural + Stage 2 cross-check), no event lookups")
    p.add_argument("--compact", action="store_true",
                   help="one line per choice set instead of listing every check TRUE/FALSE")
    p.add_argument("--seed", type=int, default=0, help="sampling seed (default: 0)")
    args = p.parse_args(argv)

    reg_path = args.choicesets or latest_choicesets_path()
    print("=" * 70)
    print("OFFICIAL CHECKER")
    print("=" * 70)
    print(f"choice sets : {reg_path}")
    print(f"events      : {args.events}   (Stage 1 output — the table Stage 3 was built from)")
    print(f"exploded    : {'(disabled)' if args.no_exploded else args.exploded}")

    print("\nloading choice-set table...")
    df_reg = read_csv_subset(reg_path, REG_COLS, label="choice sets")

    findings = structural_report(df_reg)

    # The exploded table is loaded whole (3 int columns) so the Stage 2 cross-check can
    # cover every choice set; it is sliced down to the sample afterwards.
    exploded_full = pd.DataFrame()
    if not args.no_exploded:
        if os.path.exists(args.exploded):
            print("\nloading Stage 2 exploded table...")
            exploded_full = read_csv_subset(args.exploded, EXPLODED_COLS, label="exploded")
            findings.update(exploded_report(df_reg, exploded_full))
        else:
            print(f"\n   {args.exploded} not found — Stage 2 cross-check skipped.")

    if args.structural_only:
        _verdict(findings)
        return findings

    # ---- pick the choice sets to inspect deeply --------------------------
    rng = np.random.default_rng(args.seed)
    groups = {}
    if args.choice_sets:
        ids = [int(x) for x in args.choice_sets.split(",") if x.strip()]
        groups["explicit"] = df_reg[df_reg["choice_set"].isin(ids)]
        missing = set(ids) - set(groups["explicit"]["choice_set"])
        if missing:
            print(f"\n  warning: choice_set(s) {sorted(missing)} not present in {reg_path} "
                  f"(a set with no surviving alternative emits no rows)")
    else:
        for seg in SEGMENTS:
            sel = choose_choicets(df_reg, seg, args.n_per_segment, rng)
            if len(sel):
                groups[seg] = sel
        if args.sample:
            groups["random"] = _sample_sets(df_reg, df_reg["choice_set"].unique(), args.sample, rng)

    selected = pd.concat(groups.values()).drop_duplicates()
    set_ids = set(selected["choice_set"].unique())
    print(f"\nselected {len(set_ids):,} choice sets for event-level validation "
          f"({', '.join(f'{k}:{v.choice_set.nunique()}' for k, v in groups.items())})")

    # ---- load only what those sets need ----------------------------------
    exploded = exploded_full[exploded_full["index"].isin(set_ids)].copy() if len(exploded_full) \
        else pd.DataFrame()
    del exploded_full
    choice_moments = exploded.groupby("index")["time"].first().to_dict() if len(exploded) else None
    if choice_moments is None:
        print("\n   NOTE: without the exploded table, T is taken from chosen_time (exact, but")
        print("         self-referential) and the concurrency-coverage checks report n-a.")

    # Every session the sets touch: the emitted alternatives plus the ones Stage 3 dropped.
    sessions = set(selected["id_session"])
    if len(exploded):
        sessions |= set(exploded["concurrent_sessions"])
    print("\nloading reference events (Stage 1 output)...")
    events = read_csv_subset(args.events, EVENT_COLS, key="id_session",
                             keep_values=sessions, label="events")
    session_groups = _as_session_groups(events)

    # ---- run both validators, per group ----------------------------------
    fail_rows, fail_sets = Counter(), Counter()
    tot_ok_rows = tot_rows = tot_ok_sets = tot_sets = 0
    for name, sel in groups.items():
        print("\n" + "=" * 70)
        print(f"SEGMENT: {name}   ({sel['choice_set'].nunique()} choice sets)")
        print("=" * 70)
        ids = sorted(sel["choice_set"].unique())
        row_results = validate_n_messages(sel, session_groups, choice_moments)
        set_results = checker(ids, sel, session_groups, exploded if len(exploded) else None)
        ok_r, n_r, ok_s, n_s = report_segment(row_results, set_results, fail_rows, fail_sets,
                                              compact=args.compact)
        tot_ok_rows += ok_r
        tot_rows += n_r
        tot_ok_sets += ok_s
        tot_sets += n_s

    print("\n" + "=" * 70)
    print("TOTALS")
    print("=" * 70)
    print(f"validate_n_messages : {tot_ok_rows}/{tot_rows} choice sets fully valid")
    print(f"checker             : {tot_ok_sets}/{tot_sets} choice sets fully valid")
    print_legend(fail_rows + fail_sets)
    _verdict(findings)
    return findings


if __name__ == "__main__":
    main()
