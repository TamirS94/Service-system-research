# ==============================
# Stage 2 - Creating Concurrencies & Exploded Table
# ==============================

import pandas as pd
import glob
import os
import numpy as np
import matplotlib.pyplot as plt
import time
from ast import literal_eval

print("\n==============================")
print("🚀 STARTING STAGE 2 PROCESS")
print("==============================")

# ------------------------------
# 0️⃣ Environment setup
# ------------------------------
print("\n⚙️  Setting environment...")
pd.set_option('display.max_columns', None)
pd.set_option('display.max_rows', 500)
os.chdir(os.path.dirname(os.path.abspath(__file__)))
print(f"   Working directory: {os.getcwd()}")

# ------------------------------
# 1️⃣ Loading input datasets
# ------------------------------
print("\n📥 Loading input CSV files...")
load_start = time.time()

merged_df = pd.read_csv('df_1_not_merged_2_merged.csv')
print(f"   Loaded merged events: {merged_df.shape[0]:,} rows")

merged_sessions = pd.read_csv('merged_session.csv')
merged_sessions = merged_sessions.rename(columns=lambda x: x.strip())
print(f"   Loaded merged sessions: {merged_sessions.shape[0]:,} rows")

print(f"   Load time: {time.time() - load_start:.2f} sec")

# ------------------------------
# 2️⃣ Filtering sessions to only those appearing in merged_df
# ------------------------------
print("\n🔎 Filtering sessions to only relevant session_ids...")

def uniqe(merged_df, merged_sessions):
    uniqe = merged_df['id_session'].unique()
    filter1 = merged_sessions['id_session'].isin(uniqe)
    merged_sessions = merged_sessions[filter1]
    return merged_sessions

merged_sessions = uniqe(merged_df, merged_sessions).reset_index(drop=True)
print(f"   Sessions after filtering: {merged_sessions.shape[0]:,} rows")
print(f"   Unique sessions in merged_sessions: {merged_sessions['id_session'].nunique():,}")
print(f"   Unique sessions in merged_df: {merged_df['id_session'].nunique():,}")

# ------------------------------
# 3️⃣ Merge session-level time window into event-level table
# ------------------------------
print("\n🔗 Merging session time windows into events table...")

session_level_columns = ['chat_end_time', 'chat_start_time', 'id_session', 'chat_start_date', 'chat_end_date', 'subsession', 'id_rep']
df_session_level = merged_sessions[session_level_columns]
cols = ['id_session', 'id_rep']

keys_merged_df = set(
    merged_df[cols].dropna().drop_duplicates().itertuples(index=False, name=None)
)

keys_merged_sessions = set(
    merged_sessions[cols].dropna().drop_duplicates().itertuples(index=False, name=None)
)

same_keys = keys_merged_df == keys_merged_sessions

print("Same session-rep-subsession combinations:", same_keys)

df_merged = merged_df.merge(df_session_level, on=cols, how='left', indicator=True)
print(f"   After merge: {df_merged.shape[0]:,} rows")
print(len(merged_df), len(df_merged))

# Filter to event end_time inside session time window
print("\n⏱️  Filtering events within session active time window...")
before = df_merged.shape[0]
bool_filt = (df_merged['end_time'] >= df_merged['chat_start_time']) & (df_merged['end_time'] <= df_merged['chat_end_time'])
df_merged['isin_session_timeframe'] = np.where(bool_filt, 1, 0)
df_after_tag = df_merged.copy()
print(f"   Tagged out-of-window events: {df_after_tag[df_after_tag['isin_session_timeframe'] == 0].shape[0]} rows")
print(f"   In-window_events: {df_after_tag[df_after_tag['isin_session_timeframe'] == 1].shape[0],} rows")

# ------------------------------
# 4️⃣ Remove duplicates after merge/filter
# ------------------------------
print("\n🧽 Removing duplicated event_id rows (post-merge duplicates)...")
before = df_after_tag.shape[0]

df_dup_events = df_after_tag[df_after_tag['event_id'].duplicated()]
df_no_dupes = df_after_tag[~df_after_tag['event_id'].isin(df_dup_events['event_id'])]

after = df_no_dupes.shape[0]
print(f"   Removed duplicates: {before - after:,} rows")
print(f"   Remaining: {after:,} rows")

# ------------------------------
# 5️⃣ Keep only agent messages
# ------------------------------
print("\n💬 Filtering agent messages only (event_type == 2)...")
filtered_rows = df_no_dupes[df_no_dupes['event_type'] == 2]
print(f"   Agent messages rows: {filtered_rows.shape[0]:,}")

# ------------------------------
# 6️⃣ Compute concurrent sessions per agent message
# ------------------------------
print("\n🧠 Computing concurrency lists per agent message...")
start_time = time.time()

def get_data_for_an_identifier(row):
    # lightweight progress indicator
    if row['index'] % 5000 == 0:
        print(f"\r   Processing row index: {row['index']:,}", end="")

    tmp_df = merged_sessions[merged_sessions['id_rep'] == row['id_rep']]
    tmp_df = tmp_df[tmp_df['chat_end_time'] > row['end_time']]
    tmp_df = tmp_df[tmp_df['chat_start_time'] < row['end_time']]

    return {
        'id_rep': row['id_rep'],
        'event_id': row['event_id'],
        'time': [row['end_time']],
        'session_id_chosen': [row['id_session']],
        'concurrent_sessions': tmp_df['id_session'].tolist()
    }

print("   Applying concurrency extraction (this may take a while)...")
result_dicts = filtered_rows.reset_index().apply(lambda row: get_data_for_an_identifier(row), axis=1)
print("\r   Concurrency extraction complete.                    ")

aggregated_df = pd.DataFrame(list(result_dicts))
print(f"   Aggregated concurrency df: {aggregated_df.shape[0]:,} rows")

end_time = time.time()
print(f"   Concurrency stage time: {end_time - start_time:.2f} seconds")
##### Analyzing incident of missing chosen event of id_session: 100144015, id_rep: 30000683 chosen_date:  28/05/2017 01:48:58
# ------------------------------
# 7️⃣ Workload variable
# ------------------------------
print("\n📈 Creating workload feature...")
aggregated_df['workload'] = aggregated_df['concurrent_sessions'].apply(lambda x: len(x) / 11)
print("   Workload created: workload = len(concurrent_sessions) / 11")

# ------------------------------
# 8️⃣ Convert stringified lists into python lists (literal_eval)
# ------------------------------
print("\n🧩 Parsing list-like columns (literal_eval)...")
parse_start = time.time()

aggregated_df['concurrent_sessions'] = aggregated_df['concurrent_sessions'].apply(literal_eval)
aggregated_df['time'] = aggregated_df['time'].apply(literal_eval)
aggregated_df['session_id_chosen'] = aggregated_df['session_id_chosen'].apply(literal_eval)

print(f"   Parsed list columns in {time.time() - parse_start:.2f} sec")

# ------------------------------
# 9️⃣ Filter empty/small concurrency lists
# ------------------------------
print("\n🚦 Filtering rows with concurrency list length > 1...")
before = aggregated_df.shape[0]
filtered_agg = aggregated_df[aggregated_df['concurrent_sessions'].apply(lambda x: len(x) > 1)].reset_index(drop=True)
after = filtered_agg.shape[0]
print(f"   Removed rows with <=1 concurrent session: {before - after:,}")
print(f"   Remaining for explode: {after:,}")

# ------------------------------
# 🔟 Explode to create choice-set alternatives table
# ------------------------------
print("\n💥 Exploding concurrency lists into row-level alternatives...")
explode_start = time.time()

df_exploded = filtered_agg.explode('concurrent_sessions')
df_exploded = df_exploded.explode('time')
df_exploded = df_exploded.explode('session_id_chosen')
df_exploded = df_exploded.reset_index()  # keep index for choice_set_id

print(f"   Exploded table size: {df_exploded.shape[0]:,} rows")
print(f"   Explode time: {time.time() - explode_start:.2f} sec")

# ------------------------------
# 1️⃣1️⃣ Add chosen flag
# ------------------------------
print("\n✅ Creating chosen indicator...")
df_exploded['chosen'] = (df_exploded['session_id_chosen'] == df_exploded['concurrent_sessions']).astype(int)
print("   Chosen column created (1 if chosen session == concurrent session).")

# ------------------------------
# 1️⃣2️⃣ Save outputs
# ------------------------------
print("\n📤 Saving outputs to CSV...")
save_start = time.time()

aggregated_df.to_csv('before_third_stage_all_data.csv', index=0)
df_exploded.to_csv('df_exploded_all_data.csv', index=0)

print(f"   Saved: before_third_stage_all_data.csv ({aggregated_df.shape[0]:,} rows)")
print(f"   Saved: df_exploded_all_data.csv ({df_exploded.shape[0]:,} rows)")
print(f"   Save time: {time.time() - save_start:.2f} sec")

print("\n==============================")
print("✅ STAGE 2 COMPLETED SUCCESSFULLY")
print("==============================")