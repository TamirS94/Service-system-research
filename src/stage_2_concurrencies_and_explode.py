# ==============================
# Stage 2 - Creating Concurrencies & Exploded Table
# ==============================
import logging
import pandas as pd
import glob
import os
import gc
import numpy as np

# import matplotlib.pyplot as plt
import time
from ast import literal_eval

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

# # ------------------------------
# # 1️⃣ Loading input datasets
# # ------------------------------
# print("\n📥 Loading input CSV files...")
# load_start = time.time()

# merged_df = pd.read_csv("df_1_not_merged_2_merged.csv")
# print(f"   Loaded merged events: {merged_df.shape[0]:,} rows")


# merged_sessions = pd.read_csv("merged_session.csv")
# merged_sessions = merged_sessions.rename(columns=lambda x: x.strip())
# print(f"   Loaded merged sessions: {merged_sessions.shape[0]:,} rows")

# print(f"   Load time: {time.time() - load_start:.2f} sec")

# # ------------------------------
# # 2️⃣ Filtering sessions to only those appearing in merged_df
# # ------------------------------
# print("\n🔎 Filtering sessions to only relevant session_ids...")


# def uniqe(merged_df, merged_sessions):
#     uniqe = merged_df["id_session"].unique()
#     filter1 = merged_sessions["id_session"].isin(uniqe)
#     merged_sessions = merged_sessions[filter1]
#     return merged_sessions


# merged_sessions = uniqe(merged_df, merged_sessions).reset_index(drop=True)
# print(f"   Sessions after filtering: {merged_sessions.shape[0]:,} rows")
# print(
#     f"   Unique sessions in merged_sessions: {merged_sessions['id_session'].nunique():,}"
# )
# print(f"   Unique sessions in merged_df: {merged_df['id_session'].nunique():,}")

# # ------------------------------
# # 3️⃣ Merge session-level time window into event-level table
# # ------------------------------
# print("\n🔗 Merging session time windows into events table...")

# columns_to_keep = [
#     "chat_end_time",
#     "chat_start_time",
#     "id_session",
#     "chat_start_date",
#     "chat_end_date",
# ]
# df_columns_to_keep = merged_sessions[columns_to_keep]
# session_level_columns = [
#     "chat_end_time",
#     "chat_start_time",
#     "id_session",
#     "chat_start_date",
#     "chat_end_date",
#     "subsession",
#     "id_rep",
# ]
# df_session_level = merged_sessions[session_level_columns]
# cols = ["id_session", "id_rep"]

# keys_merged_df = set(
#     merged_df[cols].dropna().drop_duplicates().itertuples(index=False, name=None)
# )

# keys_merged_sessions = set(
#     merged_sessions[cols].dropna().drop_duplicates().itertuples(index=False, name=None)
# )

# same_keys = keys_merged_df == keys_merged_sessions

# print("Same session-rep-subsession combinations:", same_keys)

# df_merged = merged_df.merge(df_session_level, on=cols, how="left", indicator=True)
# print(f"   After merge: {df_merged.shape[0]:,} rows")
# print(len(merged_df), len(df_merged))

# # Filter to event end_time inside session time window
# print("\n⏱️  Filtering events within session active time window...")
# before = df_merged.shape[0]
# df_filtered = df_merged[
#     (df_merged["end_time"] >= df_merged["chat_start_time"])
#     & (df_merged["end_time"] <= df_merged["chat_end_time"])
# ]
# after = df_filtered.shape[0]
# print(f"   Filtered out-of-window events: {before - after:,} rows")
# print(f"   Remaining: {after:,} rows")
# bool_filt = (df_merged["end_time"] >= df_merged["chat_start_time"]) & (
#     df_merged["end_time"] <= df_merged["chat_end_time"]
# )
# df_merged["isin_session_timeframe"] = np.where(bool_filt, 1, 0)
# df_after_tag = df_merged.copy()
# print(
#     f"   Tagged out-of-window events: {df_after_tag[df_after_tag['isin_session_timeframe'] == 0].shape[0]} rows"
# )
# print(
#     f"   In-window_events: {df_after_tag[df_after_tag['isin_session_timeframe'] == 1].shape[0],} rows"
# )

# # ------------------------------
# # 4️⃣ Remove duplicates after merge/filter
# # ------------------------------
# print("\n🧽 Removing duplicated event_id rows (post-merge duplicates)...")
# before = df_after_tag.shape[0]

# df_dup_events = df_after_tag[df_after_tag["event_id"].duplicated()]
# df_no_dupes = df_after_tag[~df_after_tag["event_id"].isin(df_dup_events["event_id"])]

# after = df_no_dupes.shape[0]
# print(f"   Removed duplicates: {before - after:,} rows")
# print(f"   Remaining: {after:,} rows")

# # ------------------------------
# # 5️⃣ Keep only agent messages
# # ------------------------------
# print("\n💬 Filtering agent messages only (event_type == 2)...")
# filtered_rows = df_no_dupes[df_no_dupes["event_type"] == 2]
# print(f"   Agent messages rows: {filtered_rows.shape[0]:,}")

# # ------------------------------
# # 6️⃣ Compute concurrent sessions per agent message
# # ------------------------------
# print("\n🧠 Computing concurrency lists per agent message...")
# start_time = time.time()


# def get_data_for_an_identifier(row):
#     # lightweight progress indicator
#     if row["index"] % 5000 == 0:
#         print(f"\r   Processing row index: {row['index']:,}", end="")

#     tmp_df = merged_sessions[merged_sessions["id_rep"] == row["id_rep"]]
#     tmp_df = tmp_df[tmp_df["chat_end_time"] > row["end_time"]]
#     tmp_df = tmp_df[tmp_df["chat_start_time"] < row["end_time"]]

#     return {
#         "id_rep": row["id_rep"],
#         "event_id": row["event_id"],
#         "time": [row["end_time"]],
#         "session_id_chosen": [row["id_session"]],
#         "concurrent_sessions": tmp_df["id_session"].tolist(),
#         "flag": row["flag"],   # choice-set-level: was the chosen reply a flag=1 (re-engagement) decision?
#     }


# print("   Applying concurrency extraction (this may take a while)...")
# result_dicts = filtered_rows.reset_index().apply(
#     lambda row: get_data_for_an_identifier(row), axis=1
# )
# print("\r   Concurrency extraction complete.                    ")

# aggregated_df = pd.DataFrame(list(result_dicts))
# print(f"   Aggregated concurrency df: {aggregated_df.shape[0]:,} rows")

# end_time = time.time()
# print(f"   Concurrency stage time: {end_time - start_time:.2f} seconds")
# ==============================================================================
# ACTIVE PIPELINE (concurrency computed from Stage 1 output; carries `flag`)
# The commented block above is the original exploratory version that resumed
# from a precomputed aggregated_df_11_5.csv. This main() runs concurrency from
# scratch and is the path used by the orchestrator (run_pipeline.py).
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
        mask = (starts < row.end_time) & (ends > row.end_time)
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
