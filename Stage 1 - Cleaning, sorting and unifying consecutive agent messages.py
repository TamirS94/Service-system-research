# Stage 1 - Cleaning, sorting and unifying consecutive agent messages

### Importing packages and setting work directory
import pandas as pd
import glob
import os
import numpy as np
import matplotlib.pyplot as plt
import time
from pandasql import sqldf
os.chdir(r'C:\Users\Tamir\OneDrive - Technion\current_work_upd\Service_systems_WD')
pd.set_option('display.max_columns', None)
pd.set_option('display.max_rows', 500)



## Sorting by agent (id_rep), time and deleting all id_rep = 1 in big dataframe (session event)
session_events_merged = pd.read_csv('raw_session_events_before_events_8_9_19_07.csv')
session_events_merged['assignment_date'] = session_events_merged['assignment_date'].astype(str)
# Deleting silent abondment
session_events_merged = session_events_merged[session_events_merged[" outcome"] != 4].reset_index(drop = True)
## Deleting 8 and 9 from evet_type columns:
# These variables seems not relevant since they regard to agents handling parallel chats and the time the customer waits in these occasions.
dele = session_events_merged[(session_events_merged[' event_type'] == 8) | (session_events_merged[' event_type'] == 9)].index
session_events_merged.drop(dele , inplace=True)
# Sorting again the data:
df_before_merge_filt = session_events_merged.sort_values(by=[' id_session', ' end_time'])
df_before_merge_filt =  df_before_merge_filt.reset_index(drop=True)
df_before_merge_filt_part1 = df_before_merge_filt.iloc[:1648498]
df_before_merge_filt_part2 = df_before_merge_filt.iloc[1648498:3296996]
df_before_merge_filt_part3 = df_before_merge_filt.iloc[3296996:]

## Handling same and consecutive event types (event type 2 - agent messages):

##### Setting the desired data structure in order to sum some of the variables and keep the the others from the first row (from the same consecutive rows group). 

##### We implemented a boolean condition that assigns TRUE to rows that are different than the next one by EITHER event type or id session.
##### Relying on this condition, we used cumsum and groupby to create the groups with only the SAME id session & event type (FALSE in the condition), cumsum was activated only when condition showed TRUE, therefore, it differentiated between groups only when desired.

##### Finally grouped is a groupby type which is further processed according to our desired outcomes (values are being summed, or kept as the first row).

columns_to_sum = [' sentiment', ' duration', ' number_words']
columns_to_keep_original_val = [col for col in df_before_merge_filt.columns 
                                    if col not in columns_to_sum]

def grouped(df):
    column_to_sum_by = ' event_type'
    # Columns to sum
    # Define the condition for merging based on 'session_id' and 'event_type'
    condition = (df[column_to_sum_by] != df[column_to_sum_by].shift()) | (df[' id_session'] != df[' id_session'].shift())

    # Group consecutive rows based on the defined condition
    grouped = df.groupby(condition.cumsum())
    return grouped
grouped_1 = grouped(df_before_merge_filt_part1)
grouped_2 = grouped(df_before_merge_filt_part2)
grouped_3 = grouped(df_before_merge_filt_part3)

# Define a function to merge consecutive rows and sum values at specific columns

##### Function that first ignores groups that contain only 1 row, and then sums the columns to sum (sentiment, duration, number of words), and then keeps the relevant columns of the first row in the group.

##### It creates different dictionaries for each action, and then merges the dictionaries into one, and then returns it as a dataframe. The function is applied on the 'grouped' groupby type.

# Start timing
start_time = time.time()

def merge_consecutive_rows(group):
    if len(group) == 1:
        return group
    else:
        summed_values = group[columns_to_sum].sum()
        first_row_values = group.iloc[0][columns_to_keep_original_val]
        new_row = {**first_row_values.to_dict(), **summed_values.to_dict()}
        return pd.DataFrame([new_row], columns=group.columns)

merged_parts = []  # will hold 3 dfs in order

for i, gb in enumerate([grouped_1, grouped_2, grouped_3], start=1):
    merged_df = gb.apply(merge_consecutive_rows).reset_index(drop=True)
    merged_df.to_csv(f'part{i}_after_merge.csv', index=False)
    merged_parts.append(merged_df)

merged_df_part1, merged_df_part2, merged_df_part3 = merged_parts
merged_df = pd.concat([merged_df_part1, merged_df_part2, merged_df_part3])

# End timing
end_time = time.time()
print(f"Execution time: {end_time - start_time:.2f} seconds")


# Drop according to event_type = 2, filter merged_df only according to event_type 2, then concat
merged_df_agent = merged_df[merged_df[' event_type'] == 2]
df_before_merge_filt_drop_2 = df_before_merge_filt[~(df_before_merge_filt[' event_type'] == 2)]
df_before_merge_filt_with_2 = pd.concat([df_before_merge_filt_drop_2, merged_df_agent]).sort_values(by=[' id_session', ' end_time']).reset_index(drop= True)
#sort by time:
df_before_merge_filt_with_2 = df_before_merge_filt_with_2.sort_values(by=[' id_rep', ' end_time' ]).reset_index(drop=True)
df_before_merge_filt_with_2.to_csv('df_1_not_merged_2_merged.csv', index= 0)