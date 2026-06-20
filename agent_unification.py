import pandas as pd
import time
import logging

logger = logging.getLogger(__name__)

COLUMNS_TO_SUM = ["sentiment", "duration", "number_words"]


def compute_flag(df: pd.DataFrame) -> pd.Series:
    flag = pd.Series(0, index=df.index, dtype=int)

    # would_merge_old: previous row in (id_session, end_time, event_type desc) sort is same session + type-2
    df_sorted = df.sort_values(
        ["id_session", "end_time", "event_type"], ascending=[True, True, False]
    )
    would_merge_old = (
        (df_sorted["event_type"] == 2)
        & (df_sorted["event_type"].shift(1) == 2)
        & (df_sorted["id_session"] == df_sorted["id_session"].shift(1))
    )

    # would_NOT_merge_new: previous type-2 from same agent is in a different session
    type2 = df.loc[df["event_type"] == 2].sort_values(["id_rep", "end_time"])
    prev_session_agent = type2.groupby("id_rep")["id_session"].shift(1)
    would_not_merge_new = prev_session_agent.notna() & (
        type2["id_session"] != prev_session_agent
    )

    flag_mask = would_merge_old.reindex(df.index, fill_value=False) & (
        would_not_merge_new.reindex(df.index, fill_value=False)
    )
    flag[flag_mask] = 1
    return flag


def _assign_merge_groups(df: pd.DataFrame):
    """Two-step merge group assignment.

    Step A — within-session runs: sort all events by (id_rep, id_session,
    end_time, event_type desc). Within each (id_rep, id_session), a new
    run starts when event_type changes. This means a same-session type-1
    between two type-2 events breaks the run.

    Step B — agent-timeline groups: among type-2 rows sorted by (id_rep,
    end_time), a new group starts when id_rep or id_session changes. This
    means a different-session type-2 between two same-session type-2 events
    breaks the group.

    Final merge group breaks when EITHER step triggers. Type-1 events from
    OTHER sessions are invisible (they don't appear in the within-session
    sort for the session in question).
    """
    # Step A: within-session consecutive-event-type runs
    df_ss = df.sort_values(
        ["id_rep", "id_session", "end_time", "event_type"],
        ascending=[True, True, True, False],
    )
    session_break = (
        (df_ss["id_rep"] != df_ss["id_rep"].shift())
        | (df_ss["id_session"] != df_ss["id_session"].shift())
        | (df_ss["event_type"] != df_ss["event_type"].shift())
    )
    session_run = session_break.cumsum()
    session_run_map = pd.Series(session_run.values, index=df_ss.index)

    # Step B: type-2 rows in agent-timeline order
    type2 = df[df["event_type"] == 2].sort_values(["id_rep", "end_time"]).copy()
    type2["_session_run"] = session_run_map.loc[type2.index].values

    agent_break = (type2["id_rep"] != type2["id_rep"].shift()) | (
        type2["id_session"] != type2["id_session"].shift()
    )
    session_run_break = type2["_session_run"] != type2["_session_run"].shift()

    flag_break = type2["flag"] == 1
    merge_group = (agent_break | session_run_break | flag_break).cumsum()
    return merge_group, type2.index


def _merge_group(group: pd.DataFrame, columns_to_sum: list[str]) -> pd.DataFrame:
    if len(group) == 1:
        return group

    first_row = group.iloc[0].copy()
    for col in columns_to_sum:
        first_row[col] = group[col].sum()
    first_row["flag"] = group["flag"].max()
    return pd.DataFrame([first_row])


def unify_agent_messages(df: pd.DataFrame) -> pd.DataFrame:
    logger.info("\n⚙️  Starting agent-perspective unification...")
    start_time = time.time()

    df = df.copy()
    df["flag"] = compute_flag(df)
    flag_count = df["flag"].sum()
    logger.info(f"   Flagged rows (old-merge, not new-merge): {flag_count:,}")

    type2_mask = df["event_type"] == 2
    df_non_type2 = df[~type2_mask].copy()

    logger.info(f"   Type-2 rows before unification: {type2_mask.sum():,}")
    logger.info(f"   Non-type-2 rows (untouched): {len(df_non_type2):,}")

    merge_groups, type2_idx = _assign_merge_groups(df)
    df_type2 = df.loc[type2_idx].copy()
    df_type2["_merge_group"] = merge_groups.values

    unified_type2 = (
        df_type2.groupby("_merge_group", group_keys=False)
        .apply(lambda g: _merge_group(g, COLUMNS_TO_SUM))
        .reset_index(drop=True)
    )
    unified_type2 = unified_type2.drop(
        columns=["_merge_group", "_session_run"], errors="ignore"
    )

    logger.info(f"   Type-2 rows after unification: {len(unified_type2):,}")
    logger.info(f"   Rows reduced by: {type2_mask.sum() - len(unified_type2):,}")

    df_combined = (
        pd.concat([df_non_type2, unified_type2])
        .sort_values(
            ["id_rep", "end_time", "event_type"], ascending=[True, True, False]
        )
        .reset_index(drop=True)
    )

    logger.info(f"   Final row count: {len(df_combined):,}")
    logger.info(f"   Unification time: {time.time() - start_time:.2f} sec")
    return df_combined
