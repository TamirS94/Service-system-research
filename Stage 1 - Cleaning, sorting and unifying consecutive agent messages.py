# ==============================
# Stage 1 - Cleaning, Sorting and Unifying Consecutive Agent Messages
# ==============================

import pandas as pd
import glob
import os
import numpy as np
import matplotlib.pyplot as plt
import time
from pandasql import sqldf

pd.set_option('display.max_columns', None)
pd.set_option('display.max_rows', 500)
os.chdir(os.path.dirname(os.path.abspath(__file__)))

print("\n==============================")
print("🚀 STARTING STAGE 1 PROCESS")
print("==============================")

# ------------------------------
# 1️⃣ Loading Data
# ------------------------------
print("\n📥 Loading raw dataset and fixing column names")
load_start = time.time()

session_events_merged = pd.read_csv('merged_session_events.csv')
session_events_merged = session_events_merged.rename(columns=lambda x: x.strip())


print(f"   Loaded {session_events_merged.shape[0]:,} rows")
print(f"   Time: {time.time() - load_start:.2f} sec")

# ------------------------------
# 2️⃣ Initial Cleaning and rep_id fix
# ------------------------------
print("\n🧹 Cleaning data...")

# Delete silent abandonment
before = session_events_merged.shape[0]
session_events_merged = session_events_merged[
    session_events_merged["outcome"] != 4
].reset_index(drop=True)
after = session_events_merged.shape[0]
print(f"   Removed silent abandonment: {before - after:,} rows")

# Drop event types 8 and 9
before = session_events_merged.shape[0]
dele = session_events_merged[
    (session_events_merged['event_type'] == 8) |
    (session_events_merged['event_type'] == 9)
].index
session_events_merged.drop(dele, inplace=True)
after = session_events_merged.shape[0]
print(f"   Removed event types 8 & 9: {before - after:,} rows")


print("\n Initiating rep_id fix")
## Identified that when customers send the initial message their rep_id (associated agent) shows as 1 before being assigned. That message is relevant for the choice model we intend to create.

## Implementing the following fix: deleting all id_rep values that and re-adding them using join on id_session + subsession
# Identify rows where id_rep is 1
mask = session_events_merged['id_rep'] == 1

# For each session, find the first non-1 id_rep value and use it to replace 1s
def replace_rep_ids(group):
    # Get the first non-1 id_rep value in the session
    first_non_one = group[group['id_rep'] != 1]['id_rep'].iloc[0] if not group[group['id_rep'] != 1].empty else 1
    # Replace all 1s in this session with the first non-1 id_rep
    group.loc[group['id_rep'] == 1, 'id_rep'] = first_non_one
    return group

# Apply the function to each session
session_events_merged = session_events_merged.groupby(['id_session', 'subsession']).apply(replace_rep_ids).reset_index(drop=True)
print("\n rep_id fix implemented")


# ------------------------------
# 3️⃣ Sorting, exporting and Splitting
# ------------------------------
print("\n📊 Sorting and splitting dataset...")

df_before_merge_filt = session_events_merged.sort_values(
    by=['id_session', 'end_time']
).reset_index(drop=True)
df_before_merge_filt['event_id'] = df_before_merge_filt.index + 1
df_before_merge_filt.to_csv('cleaned_raw_data_24_2.csv', index = 0)

third = int(df_before_merge_filt.shape[0] / 3)
third_2 = int(df_before_merge_filt.shape[0] / 3 * 2)

df_before_merge_filt_part1 = df_before_merge_filt.iloc[:third]
df_before_merge_filt_part2 = df_before_merge_filt.iloc[third:third_2]
df_before_merge_filt_part3 = df_before_merge_filt.iloc[third_2:]

print(f"   Total rows after cleaning: {df_before_merge_filt.shape[0]:,}")
print(f"   Part sizes: {len(df_before_merge_filt_part1):,}, "
      f"{len(df_before_merge_filt_part2):,}, "
      f"{len(df_before_merge_filt_part3):,}")

# ------------------------------
# 4️⃣ Grouping Logic
# ------------------------------
print("\n🔁 Preparing grouping logic for consecutive events...")

columns_to_sum = ['sentiment', 'duration', 'number_words']
columns_to_keep_original_val = [
    col for col in df_before_merge_filt.columns
    if col not in columns_to_sum
]

def grouped(df):
    column_to_sum_by = 'event_type'
    condition = (
        (df[column_to_sum_by] != df[column_to_sum_by].shift()) |
        (df['id_session'] != df['id_session'].shift())
    )
    return df.groupby(condition.cumsum())

grouped_1 = grouped(df_before_merge_filt_part1)
grouped_2 = grouped(df_before_merge_filt_part2)
grouped_3 = grouped(df_before_merge_filt_part3)

print("   Grouping objects created.")

# ------------------------------
# 5️⃣ Merging Consecutive Rows
# ------------------------------
print("\n⚙️  Merging consecutive rows...")
start_time = time.time()

def merge_consecutive_rows(group):
    if len(group) == 1:
        return group
    else:
        summed_values = group[columns_to_sum].sum()
        first_row_values = group.iloc[0][columns_to_keep_original_val]
        new_row = {**first_row_values.to_dict(), **summed_values.to_dict()}
        return pd.DataFrame([new_row], columns=group.columns)

merged_parts = []

for i, gb in enumerate([grouped_1, grouped_2, grouped_3], start=1):
    print(f"   🔹 Processing part {i}...")
    part_start = time.time()

    merged_df = gb.apply(merge_consecutive_rows).reset_index(drop=True)
    merged_df.to_csv(f'part{i}_after_merge.csv', index=False)

    print(f"      Saved part{i}_after_merge.csv "
          f"({merged_df.shape[0]:,} rows) "
          f"in {time.time() - part_start:.2f} sec")

    merged_parts.append(merged_df)

merged_df_part1, merged_df_part2, merged_df_part3 = merged_parts
merged_df = pd.concat(
    [merged_df_part1, merged_df_part2, merged_df_part3]
).reset_index(drop=True)

print(f"\n   Total merged rows: {merged_df.shape[0]:,}")
print(f"   Merge stage time: {time.time() - start_time:.2f} sec")

# ------------------------------
# 6️⃣ Final Filtering and Output
# ------------------------------
print("\n📤 Final filtering and exporting...")

merged_df_agent = merged_df[merged_df['event_type'] == 2]
df_before_merge_filt_drop_2 = df_before_merge_filt[
    ~(df_before_merge_filt['event_type'] == 2)
]

df_before_merge_filt_with_2 = pd.concat(
    [df_before_merge_filt_drop_2, merged_df_agent]
).sort_values(
    by=['id_session', 'end_time']
).reset_index(drop=True)

df_before_merge_filt_with_2 = df_before_merge_filt_with_2.sort_values(
    by=['id_rep', 'end_time']
).reset_index(drop=True)

df_before_merge_filt_with_2.to_csv(
    'df_1_not_merged_2_merged.csv',
    index=False
)

print(f"   Final dataset rows: {df_before_merge_filt_with_2.shape[0]:,}")
print("\n==============================")
print("✅ STAGE 1 COMPLETED SUCCESSFULLY")
print("==============================")