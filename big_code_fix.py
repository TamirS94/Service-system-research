import pandas as pd
import time
import warnings


#Load merged df and exploded here:
merged_df = pd.read_csv(r"C:\Users\nadid\OneDrive - Technion\Desktop\Nadav\merged_sliced.csv")
df_exploded = pd.read_csv(r"C:\Users\nadid\OneDrive - Technion\Desktop\Nadav\exploded_sliced.csv")

warnings.filterwarnings('ignore')

# Start timing
start_time = time.time()

# STEP 1: Preprocess merged_df
# Make sure to strip column names (remove extra spaces)
merged_df.columns = merged_df.columns.str.strip()
df_exploded.columns = df_exploded.columns.str.strip()

# Sort for efficient filtering
merged_df = merged_df.sort_values(['id_session', 'end_time'])

# Group by session for fast access
session_groups = dict(tuple(merged_df.groupby('id_session')))

# List to collect results
filtered_events_list = []

# STEP 2: Iterate over df_exploded
for row in df_exploded.reset_index().itertuples(index=False):
    print(row.concurrent_sessions)
    session_events = session_groups.get(row.concurrent_sessions, pd.DataFrame())

    if session_events.empty:
        continue

    # Get only events before the choice time
    past_events = session_events[session_events['end_time'] < row.time]

    if past_events.empty:
        continue

    # Get the latest event
    closest_time = past_events['end_time'].max()
    filtered_event = past_events[past_events['end_time'] == closest_time]

    # Keep only event_type == 1
    filtered_event = filtered_event[filtered_event['event_type'] == 1]

    if not filtered_event.empty:
        # Enrich the row
        filtered_event = filtered_event.assign(
            choice_set=row.choice_set_id,
            chosen=row.chosen,
            waiting_time=row.time - filtered_event['end_time'],
            workload=row.workload
        )

        filtered_events_list.append(filtered_event)

# STEP 3: Combine and save
if filtered_events_list:
    all_filtered_events = pd.concat(filtered_events_list, ignore_index=True)
    all_filtered_events.to_csv('all_filtered_events_3m_f.csv', index=False)
    print(all_filtered_events.head())
else:
    print("No filtered events found.")

# STEP 4: Execution time
end_time = time.time()
print(f"Execution time: {end_time - start_time:.2f} seconds")