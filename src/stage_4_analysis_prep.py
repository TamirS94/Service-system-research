# ==============================
# Stage 4 - Analysis prep: filter the choice-set table & build the choice covariates
# ==============================
"""
Turns the Stage 3 choice-set table (`df_choicesets_<date>.csv`) into a
regression-ready table for the conditional logit (`clogit(chosen ~ ... +
strata(choice_set))`).

What it does
------------
1. **Drops uninformative choice sets** - keeps only sets with **more than one
   alternative row**. A one-alternative stratum contributes a likelihood of
   exactly 1 to a conditional logit, i.e. zero information (CLAUDE.md Known
   Issue #9: ~65% of the raw table). Vectorized equivalent of::

       df_filt = df.groupby('choice_set').agg({'id_session':'count'})
       df_filt = df_filt[df_filt['id_session'] > 1]
       df_reg  = df[df['choice_set'].isin(df_filt.index)]

2. **`stickiness`** - inertia / state dependence. `1` on the alternative that is
   the session the agent replied to at their **previous reply**, whether or not
   they stay with it now. Defined for *every* alternative (not only the chosen
   one) - a covariate that only ever marked the chosen row would predict the
   outcome perfectly and be useless in a clogit. Companion columns:
   `stickiness_streak` (how many consecutive replies the agent had already made
   to that session - "and so on and on") and `prev_reply_gap` (seconds since the
   agent's previous reply, so a "previous" reply from a shift two days ago can be
   excluded).

   The lag comes from the **event stream**, not from the choice-set table: the
   agent's previous *reply* may have been a reply that Stage 2 never turned into
   a choice moment (only one session concurrent), or the chosen row of a
   no-chosen set. Post-unification, two agent-timeline-consecutive type-2 rows to
   the same session are always separated by a visitor turn (Stage 1 would have
   merged them otherwise), so a streak really is "the agent kept coming back to
   this session turn after turn".

3. **`FCFS`** - first come, first served. `1` on the alternative whose customer
   has been waiting longest at the choice moment, i.e. the one FIFO says to
   answer. Basis is `waiting_time` (max), which on a normal alternative is
   exactly `argmin(end_time)` = the customer who sent first; it differs only on
   Option-3 rows (`flag == 1`, chosen), where the pipeline deliberately measures
   from the genuine reply rather than from an already-answered message. Use
   `--fcfs-basis end_time` for the literal earliest-message version.
   `fcfs_rank` = 1 for that alternative, 2 for the next longest wait, etc.

4. **`session_progress`** - how many **customer** messages that session still has
   left after this turn, until the session ends. `0` = this turn contains the
   session's last customer message. Companions: `session_msgs_total` (customer
   messages in the whole session) and `session_progress_pct` (share of the
   session's customer messages completed as of this turn).
   NOTE: this looks into the future of the choice moment - the agent cannot know
   it. Treat it as a descriptive / segmentation variable, not as a clean control.

Inputs (all read from the repo root, like every other stage)
------------------------------------------------------------
    df_choicesets_<date>.csv       Stage 3 output (newest by default)
    df_exploded_all_data.csv       Stage 2 - maps choice_set -> the reply event_id
    df_1_not_merged_2_merged.csv   Stage 1 - the event stream (lag + session length)

Usage
-----
    python src/stage_4_analysis_prep.py
    python src/stage_4_analysis_prep.py --choicesets df_choicesets_17_08_2026.csv
    python src/stage_4_analysis_prep.py --out df_reg_20_08_2026.csv
    python src/stage_4_analysis_prep.py --drop-no-chosen        # strata with no chosen row
    python src/stage_4_analysis_prep.py --fcfs-basis end_time
"""

import argparse
import glob
import os
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

# Make stdout/stderr UTF-8 so the report never dies on a legacy Windows codepage.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (script lives in src/)

DEFAULT_EVENTS = "df_1_not_merged_2_merged.csv"    # Stage 1 output = the event stream
DEFAULT_EXPLODED = "df_exploded_all_data.csv"      # Stage 2 output = choice moments
SHIFT_GAP_SEC = 3600                               # a gap this long in an agent's replies ends a shift


def log(msg=""):
    print(msg, flush=True)


def newest_choicesets() -> str:
    files = sorted(glob.glob("df_choicesets_*.csv"), key=os.path.getmtime)
    if not files:
        sys.exit("No df_choicesets_*.csv found in the repo root.")
    return files[-1]


# ------------------------------------------------------------------
# 1. size filter
# ------------------------------------------------------------------
def filter_multi_alternative(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only choice sets with >1 alternative row (informative strata)."""
    sizes = df.groupby("choice_set")["id_session"].transform("size")
    df["set_size"] = sizes.astype("int32")
    keep = df["set_size"] > 1

    n_sets_before = df["choice_set"].nunique()
    out = df.loc[keep].copy()
    n_sets_after = out["choice_set"].nunique()
    log(f"   sets  : {n_sets_before:,} -> {n_sets_after:,} "
        f"({n_sets_before - n_sets_after:,} singletons dropped)")
    log(f"   rows  : {len(df):,} -> {len(out):,}")
    log(f"   mean alternatives/set: {len(out) / max(n_sets_after, 1):.2f}, "
        f"max {out['set_size'].max()}")
    return out


# ------------------------------------------------------------------
# 2. FCFS
# ------------------------------------------------------------------
def add_fcfs(df: pd.DataFrame, basis: str = "waiting_time") -> pd.DataFrame:
    """FCFS = 1 on the longest-waiting alternative of the set (FIFO's pick).

    `waiting_time` basis: rank by -waiting_time (longest wait first). On a normal
    alternative waiting_time = T - first pending message, so this is identical to
    ranking by earliest end_time; it differs only on Option-3 rows, where the
    pipeline deliberately measures from the genuine reply instead of from an
    already-answered message.
    """
    df["_fcfs_key"] = -df["waiting_time"] if basis == "waiting_time" else df["end_time"]
    g = df.groupby("choice_set")["_fcfs_key"]
    df["FCFS"] = (df["_fcfs_key"] == g.transform("min")).astype("int8")
    df["fcfs_rank"] = g.rank(method="dense").astype("int32")
    df.drop(columns="_fcfs_key", inplace=True)

    per_set = df.groupby("choice_set")["FCFS"].sum()
    ties = int((per_set > 1).sum())
    chosen = df["chosen"] == 1
    log(f"   FCFS basis: {basis}")
    log(f"   sets with a tied first arrival (>1 FCFS row): {ties:,} "
        f"({100 * ties / max(len(per_set), 1):.2f}%)")
    log(f"   chosen rows that are the FCFS alternative : "
        f"{int(df.loc[chosen, 'FCFS'].sum()):,} / {int(chosen.sum()):,} "
        f"({100 * df.loc[chosen, 'FCFS'].mean():.1f}%)  <- FIFO compliance rate")
    return df


# ------------------------------------------------------------------
# 3. stickiness
# ------------------------------------------------------------------
def build_reply_lag(events: pd.DataFrame) -> pd.DataFrame:
    """For every agent reply, what that same agent's PREVIOUS reply was.

    Returns a frame indexed by the reply's `event_id` with:
        prev_session  session of the agent's previous reply (NaN = first ever)
        prev_streak   how many consecutive replies the agent had just made to
                      prev_session (1 = they had only just switched to it)
        prev_time     end_time of that previous reply
    """
    ev2 = events.loc[events["event_type"] == 2, ["event_id", "id_session", "id_rep", "end_time"]]
    ev2 = ev2.sort_values(["id_rep", "end_time", "event_id"], kind="mergesort").reset_index(drop=True)

    same_agent = ev2["id_rep"] == ev2["id_rep"].shift(1)
    # Shift = a block of one agent's replies with no gap longer than SHIFT_GAP_SEC.
    gap = ev2["end_time"].diff()
    new_shift = (~same_agent) | (gap > SHIFT_GAP_SEC)
    shift_id = new_shift.cumsum()
    shift_start = ev2.groupby(shift_id)["end_time"].transform("min")

    prev_session = ev2["id_session"].shift(1).where(same_agent)
    prev_time = ev2["end_time"].shift(1).where(same_agent)

    # A "run" = consecutive replies by one agent to one session.
    new_run = ~(same_agent & (ev2["id_session"] == ev2["id_session"].shift(1)))
    run_id = new_run.cumsum()
    within_run = ev2.groupby(run_id).cumcount()                  # 0-based position in the run
    run_len = ev2.groupby(run_id)["event_id"].transform("size")
    # mid-run       -> the streak so far is the position within the run
    # start of a run -> the streak is the FULL length of the previous run
    prev_streak = np.where(
        within_run > 0,
        within_run,
        run_len.shift(1).where(same_agent).fillna(0),
    )

    return pd.DataFrame(
        {
            "prev_session": prev_session.to_numpy(),
            "prev_streak": prev_streak.astype("int32"),
            "prev_time": prev_time.to_numpy(),
            "shift_id": shift_id.to_numpy(),
            "time_in_shift": (ev2["end_time"] - shift_start).to_numpy(),
        },
        index=pd.Index(ev2["event_id"].to_numpy(), name="event_id"),
    )


def add_stickiness(df: pd.DataFrame, events: pd.DataFrame, exploded_path: str) -> pd.DataFrame:
    lag = build_reply_lag(events)

    # choice_set -> the event_id of the agent reply that created it. Stage 2's
    # `index` column is the pre-explode row id, i.e. exactly Stage 3's choice_set.
    expl = pd.read_csv(exploded_path, usecols=["index", "event_id"])
    expl = expl.drop_duplicates("index").set_index("index")["event_id"]

    reply_id = df["choice_set"].map(expl)
    missing = int(reply_id.isna().sum())
    if missing:
        log(f"   [WARN] {missing:,} rows whose choice_set is not in the exploded table")

    prev_session = reply_id.map(lag["prev_session"])
    df["stickiness"] = (df["id_session"] == prev_session).astype("int8")
    df["stickiness_streak"] = np.where(
        df["stickiness"] == 1, reply_id.map(lag["prev_streak"]).fillna(0), 0
    ).astype("int32")
    df["prev_reply_gap"] = (df["chosen_time"] - reply_id.map(lag["prev_time"])).astype("Int64")
    # Set-level shift context (constant within a stratum -> interactions / splits only).
    df["shift_id"] = reply_id.map(lag["shift_id"]).astype("Int32")
    df["time_in_shift"] = reply_id.map(lag["time_in_shift"]).astype("Int32")

    chosen = df["chosen"] == 1
    per_set = df.groupby("choice_set")["stickiness"].max()
    log(f"   sets where the agent's previous session is still an alternative: "
        f"{int(per_set.sum()):,} / {len(per_set):,} ({100 * per_set.mean():.1f}%)")
    log(f"   chosen rows that are the sticky alternative: "
        f"{int(df.loc[chosen, 'stickiness'].sum()):,} / {int(chosen.sum()):,} "
        f"({100 * df.loc[chosen, 'stickiness'].mean():.1f}%)  <- raw stay rate")
    gap = df.loc[chosen & (df["stickiness"] == 1), "prev_reply_gap"].dropna()
    if len(gap):
        log(f"   gap to the previous reply on sticky+chosen rows (sec): "
            f"median {int(gap.median()):,}, p90 {int(gap.quantile(0.9)):,}, max {int(gap.max()):,}")
    log(f"   shifts (reply gap > {SHIFT_GAP_SEC // 60} min): {df['shift_id'].nunique():,} distinct; "
        f"time_in_shift median {df.loc[chosen, 'time_in_shift'].median() / 3600:.1f}h, "
        f"p90 {df.loc[chosen, 'time_in_shift'].quantile(0.9) / 3600:.1f}h")
    return df


# ------------------------------------------------------------------
# 5. session history at T (alternative-varying, no look-ahead)
# ------------------------------------------------------------------
def add_session_history(df: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """How much the agent has already invested in each alternative, as of T.

    `n_prior_agent_replies` - agent replies in that session strictly before T
                              (conversation depth; the clean, no-look-ahead
                              counterpart of session_progress).
    `time_since_agent_last_replied` - T minus the last of those replies
                              ("neglect time"; the continuous generalisation of
                              stickiness and of the Option-3 clock). NA when the
                              agent has not replied in that session yet.

    Both are computed with one global searchsorted: sessions are factorised to
    dense codes and packed into a single sortable key `code << 31 | end_time`, so
    a row's position inside its own session's block is a subtraction away.
    """
    ev2 = events.loc[events["event_type"] == 2, ["id_session", "end_time"]]
    codes, _ = pd.factorize(pd.concat([ev2["id_session"], df["id_session"]]), sort=True)
    ev_code = codes[: len(ev2)]
    row_code = codes[len(ev2):]

    SHIFT = np.int64(1) << 31                       # end_time (< 2^31) never collides with the code
    key_ev = np.sort(ev_code.astype("int64") * SHIFT + ev2["end_time"].to_numpy("int64"))
    base = row_code.astype("int64") * SHIFT         # start of this session's block

    pos = np.searchsorted(key_ev, base + df["chosen_time"].to_numpy("int64"), side="left")
    start = np.searchsorted(key_ev, base, side="left")
    n_prior = pos - start

    last_reply = np.where(n_prior > 0, key_ev[np.maximum(pos - 1, 0)] - base, np.nan)
    since = np.where(n_prior > 0, df["chosen_time"].to_numpy("int64") - last_reply, np.nan)

    df["n_prior_agent_replies"] = n_prior.astype("int32")
    df["time_since_agent_last_replied"] = pd.array(since, dtype="Float64").astype("Int64")

    log(f"   n_prior_agent_replies: mean {n_prior.mean():.2f}, median {np.median(n_prior):.0f}, "
        f"max {n_prior.max()}; {100 * (n_prior == 0).mean():.1f}% of rows are the session's first turn")
    ok = ~np.isnan(since)
    log(f"   time_since_agent_last_replied (sec): median {np.median(since[ok]):.0f}, "
        f"p90 {np.quantile(since[ok], 0.9):.0f}, NA {100 * (~ok).mean():.1f}%")
    return df


# ------------------------------------------------------------------
# 6. time of day (set-level)
# ------------------------------------------------------------------
def add_time_of_day(df: pd.DataFrame) -> pd.DataFrame:
    """Clock context of the choice moment. Constant within a stratum, so these
    are for interactions and sample splits only - never a clogit main effect.

    The epoch columns are the local wall clock stored as UTC (`end_date` renders
    exactly as `end_time` in UTC), so the hour is a plain modulo. Whether that
    wall clock is the contact centre's local time is a question for the data
    provider - it decides what "night" means here.
    """
    hour = (df["chosen_time"] // 3600) % 24
    df["hour"] = hour.astype("int8")
    df["day_band"] = pd.cut(
        hour, bins=[-1, 5, 11, 17, 23],
        labels=["night", "morning", "afternoon", "evening"],
    ).astype("str")
    df["is_night"] = hour.isin([22, 23, 0, 1, 2, 3, 4, 5]).astype("int8")
    df["dow"] = (((df["chosen_time"] // 86400) + 4) % 7).astype("int8")   # 0 = Monday

    sets = df[df["chosen"] == 1]
    log(f"   day_band (choice moments): "
        + ", ".join(f"{k} {100 * v:.0f}%" for k, v in sets["day_band"].value_counts(normalize=True).items()))
    log(f"   is_night: {100 * sets['is_night'].mean():.1f}%")
    return df


# ------------------------------------------------------------------
# 4. session_progress
# ------------------------------------------------------------------
def add_session_progress(df: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Customer messages left in the session after this alternative's turn.

    The row's `event_id` is the FIRST message of the pending turn (Stage 3 keeps
    the first row's values) and the turn is `n_messages` consecutive customer
    messages, so
        session_progress = total_type1 - index_of_first - n_messages
    """
    ev1 = events.loc[events["event_type"] == 1, ["event_id", "id_session", "end_time"]]
    ev1 = ev1.sort_values(["id_session", "end_time", "event_id"], kind="mergesort")
    msg_idx = ev1.groupby("id_session").cumcount()
    msgs_total = ev1.groupby("id_session")["event_id"].transform("size")
    pos = pd.DataFrame(
        {"msg_idx": msg_idx.to_numpy(), "session_msgs_total": msgs_total.to_numpy()},
        index=pd.Index(ev1["event_id"].to_numpy(), name="event_id"),
    )

    idx = df["event_id"].map(pos["msg_idx"])
    total = df["event_id"].map(pos["session_msgs_total"])
    unmatched = int(idx.isna().sum())
    if unmatched:
        log(f"   [WARN] {unmatched:,} rows whose event_id is not a type-1 event in the event stream")

    progress = total - idx - df["n_messages"]
    negatives = int((progress < 0).sum())
    if negatives:
        # Only possible if the same-second ordering of a turn differs from Stage 3's.
        log(f"   [WARN] {negatives:,} rows with a negative count (same-second ordering) - clamped to 0")
        progress = progress.clip(lower=0)

    df["session_msgs_total"] = total.astype("Int32")
    df["session_progress"] = progress.astype("Int32")
    df["session_progress_pct"] = ((total - progress) / total).astype("float32")

    log(f"   session_progress: mean {progress.mean():.2f}, median {progress.median():.0f}, "
        f"p90 {progress.quantile(0.9):.0f}, max {progress.max():.0f}")
    log(f"   last-turn-of-session rows (session_progress == 0): "
        f"{int((progress == 0).sum()):,} ({100 * (progress == 0).mean():.1f}%)")
    return df


# ------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Stage 4 - prepare the choice-set table for the conditional logit"
    )
    ap.add_argument("--choicesets", default=None, help="Stage 3 table (default: newest df_choicesets_*.csv)")
    ap.add_argument("--events", default=DEFAULT_EVENTS, help="Stage 1 event stream")
    ap.add_argument("--exploded", default=DEFAULT_EXPLODED, help="Stage 2 exploded table")
    ap.add_argument("--out", default=None, help="output CSV (default: df_reg_ready_<today>.csv)")
    ap.add_argument("--fcfs-basis", choices=["waiting_time", "end_time"], default="waiting_time")
    ap.add_argument("--drop-no-chosen", action="store_true",
                    help="also drop strata with no chosen row (they carry no information either)")
    ap.add_argument("--nrows", type=int, default=None, help="debug: read only the first N choice-set rows")
    args = ap.parse_args()

    t0 = time.time()
    cs_path = args.choicesets or newest_choicesets()
    out_path = args.out or f"df_reg_ready_{datetime.today().strftime('%d_%m_%Y')}.csv"
    for p in (cs_path, args.events, args.exploded):
        if not os.path.exists(p):
            sys.exit(f"Missing input: {p}")

    log("\n==============================")
    log("STAGE 4 - ANALYSIS PREP")
    log("==============================")
    log(f"choice sets : {cs_path}")
    log(f"events      : {args.events}")
    log(f"exploded    : {args.exploded}")
    log(f"output      : {out_path}\n")

    log("[1/7] loading the choice-set table...")
    df = pd.read_csv(cs_path, nrows=args.nrows)
    df.columns = df.columns.str.strip()
    log(f"   {len(df):,} rows x {df.shape[1]} cols  ({time.time() - t0:.0f}s)")

    log("\n[2/7] dropping single-alternative choice sets...")
    df = filter_multi_alternative(df)

    no_chosen = df.groupby("choice_set")["chosen"].sum()
    n_no_chosen = int((no_chosen == 0).sum())
    if args.drop_no_chosen and n_no_chosen:
        bad = no_chosen.index[no_chosen == 0]
        df = df[~df["choice_set"].isin(bad)].copy()
        log(f"   dropped {n_no_chosen:,} no-chosen strata ({len(df):,} rows remain)")
    elif n_no_chosen:
        log(f"   [INFO] {n_no_chosen:,} strata still have no chosen row - clogit drops them "
            f"anyway; use --drop-no-chosen to remove them here")

    log("\n[3/7] FCFS (first come, first served)...")
    df = add_fcfs(df, args.fcfs_basis)

    log("\n[4/7] loading the event stream + stickiness...")
    events = pd.read_csv(
        args.events, usecols=["event_id", "id_session", "id_rep", "event_type", "end_time"]
    )
    log(f"   {len(events):,} events")
    df = add_stickiness(df, events, args.exploded)

    log("\n[5/7] session_progress...")
    df = add_session_progress(df, events)

    log("\n[6/7] session history at T (n_prior_agent_replies, time_since_agent_last_replied)...")
    df = add_session_history(df, events)

    log("\n[7/7] time of day...")
    df = add_time_of_day(df)

    log(f"\nwriting {out_path} ...")
    df.to_csv(out_path, index=False)
    log(f"   {len(df):,} rows x {df.shape[1]} cols, {df['choice_set'].nunique():,} choice sets")
    log(f"\nStage 4 done in {(time.time() - t0) / 60:.1f} min")
    log("\nNew columns")
    log("  alternative-varying (usable as clogit main effects):")
    log("      FCFS, fcfs_rank, stickiness, stickiness_streak, n_prior_agent_replies,")
    log("      time_since_agent_last_replied, session_progress, session_msgs_total,")
    log("      session_progress_pct")
    log("  set-level - CONSTANT within a stratum, so interactions / sample splits only:")
    log("      set_size, prev_reply_gap, shift_id, time_in_shift, hour, day_band, is_night, dow")


if __name__ == "__main__":
    main()
