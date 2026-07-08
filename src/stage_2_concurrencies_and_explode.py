# ==============================
# Stage 2 - Creating Concurrencies & Exploded Table
# ==============================
import logging
import pandas as pd
import os
import gc
import numpy as np
import time

print("\n==============================")
print("🚀 STARTING STAGE 2 PROCESS")
print("==============================")

# ------------------------------
# 0️⃣ Environment setup
# ------------------------------
print("\n⚙️  Setting environment...")
pd.set_option("display.max_columns", None)
pd.set_option("display.max_rows", 500)
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (script now lives in src/)
print(f"   Working directory: {os.getcwd()}")

# ==============================================================================
# Concurrency is computed from scratch from the Stage 1 output (numpy-optimized
# per-agent masks) and carries `flag`; this is the path used by run_pipeline.py.
# An earlier exploratory version that resumed from a precomputed
# aggregated_df_11_5.csv was removed (see git history if ever needed).
# ==============================================================================


def _build_sessions_by_rep(merged_sessions: pd.DataFrame) -> dict:
    """Pre-group session time-windows per agent for fast concurrency lookup.

    Returns {id_rep: (start_times, end_times, session_ids)} as numpy arrays so
    each agent-message query is a vectorized mask over only that agent's sessions
    (avoids scanning the whole sessions table per message)."""
    by_rep = {}
    for rep, g in merged_sessions.groupby("id_rep"):
        by_rep[rep] = (
            np.asarray(g["chat_start_time"], dtype=float),
            np.asarray(g["chat_end_time"], dtype=float),
            g["id_session"].to_numpy(),
        )
    return by_rep


def main(
    stage1_output="df_1_not_merged_2_merged.csv",
    sessions_path="merged_session.csv",
    exploded_out="df_exploded_all_data.csv",
    before_third_out="before_third_stage_all_data.csv",
):
    # 1. Load Stage 1 output + session-level time windows
    print("\n📥 Loading Stage 1 output and session windows...")
    load_start = time.time()
    merged_df = pd.read_csv(stage1_output)
    merged_df.columns = merged_df.columns.str.strip()
    merged_sessions = pd.read_csv(sessions_path).rename(columns=lambda x: x.strip())
    print(f"   merged_df: {merged_df.shape[0]:,} rows | sessions: {merged_sessions.shape[0]:,} rows")
    print(f"   Load time: {time.time() - load_start:.2f} sec")

    has_flag = "flag" in merged_df.columns
    if not has_flag:
        print("   ⚠️  No 'flag' column in Stage 1 output; defaulting flag=0.")

    # 2. Concurrency per agent reply (type=2)
    print("\n🧠 Computing concurrency per agent message...")
    conc_start = time.time()
    sessions_by_rep = _build_sessions_by_rep(merged_sessions)
    empty = (np.array([]), np.array([]), np.array([]))  # plain ndarrays for reps with no sessions

    agent_msgs = merged_df[merged_df["event_type"] == 2].reset_index(drop=True)
    print(f"   Agent messages (choice moments): {agent_msgs.shape[0]:,}")

    records = []
    for i, row in enumerate(agent_msgs.itertuples(index=False)):
        if i % 50000 == 0:
            print(f"\r   Processing agent message {i:,}/{len(agent_msgs):,}", end="")
        starts, ends, sids = sessions_by_rep.get(row.id_rep, empty)
        # Concurrent = session started before the reply and had not yet ended.
        # Upper bound is >= (not strict >): chat_end_time is whole-second, so a reply can
        # land on the exact second the session's metadata marks it closed. Such a session
        # still had an open visitor turn the agent was answering, so it must count as a
        # competing alternative (recovers ~53 otherwise-dropped chosen sessions — the
        # "closed-but-active" case, CLAUDE.md Known Issue #6). Lower bound stays strict:
        # a session starting at the exact reply second is a proactive opener with no
        # pending customer turn, so it is correctly excluded.
        mask = (starts < row.end_time) & (ends >= row.end_time)
        records.append(
            {
                "id_rep": row.id_rep,
                "event_id": row.event_id,
                "time": [row.end_time],
                "session_id_chosen": [row.id_session],
                "concurrent_sessions": list(sids[mask]),
                "flag": getattr(row, "flag", 0) if has_flag else 0,
            }
        )
    print(f"\r   Concurrency complete in {time.time() - conc_start:.2f} sec" + " " * 20)

    aggregated_df = pd.DataFrame(records)

    # Free the big input tables before the (memory-heavy, row-multiplying) explode,
    # so the explode runs holding only the aggregated/exploded data — same peak-memory
    # benefit the original two-phase (save→reload→explode) design gave.
    del merged_df, merged_sessions, sessions_by_rep, agent_msgs, records
    gc.collect()

    # 3. Workload
    print("\n📈 Creating workload feature (len(concurrent_sessions))...")
    aggregated_df["workload"] = aggregated_df["concurrent_sessions"].apply(len)

    # 4. Keep genuine choices (>1 concurrent session)
    before = aggregated_df.shape[0]
    filtered_agg = aggregated_df[
        aggregated_df["concurrent_sessions"].apply(lambda x: len(x) > 1)
    ].reset_index(drop=True)
    print(f"   Removed rows with <=1 concurrent session: {before - filtered_agg.shape[0]:,}")
    print(f"   Remaining for explode: {filtered_agg.shape[0]:,}")

    # 5. Explode into one row per alternative (lists already real -> no literal_eval)
    print("\n💥 Exploding concurrency lists into alternatives...")
    df_exploded = (
        filtered_agg.explode("concurrent_sessions")
        .explode("time")
        .explode("session_id_chosen")
        .reset_index()  # 'index' column = choice_set id (used by Stage 3 as row.index)
    )
    df_exploded["chosen"] = (
        df_exploded["session_id_chosen"] == df_exploded["concurrent_sessions"]
    ).astype(int)
    print(f"   Exploded table size: {df_exploded.shape[0]:,} rows")

    # 6. Save
    print("\n📤 Saving outputs...")
    aggregated_df.to_csv(before_third_out, index=False)
    df_exploded.to_csv(exploded_out, index=False)
    print(f"   Saved: {before_third_out} ({aggregated_df.shape[0]:,} rows)")
    print(f"   Saved: {exploded_out} ({df_exploded.shape[0]:,} rows)")

    print("\n==============================")
    print("✅ STAGE 2 COMPLETED SUCCESSFULLY")
    print("==============================")
    return exploded_out


if __name__ == "__main__":
    import sys

    s1 = sys.argv[1] if len(sys.argv) > 1 else "df_1_not_merged_2_merged.csv"
    exp = sys.argv[2] if len(sys.argv) > 2 else "df_exploded_all_data.csv"
    bt = sys.argv[3] if len(sys.argv) > 3 else "before_third_stage_all_data.csv"
    main(stage1_output=s1, exploded_out=exp, before_third_out=bt)
