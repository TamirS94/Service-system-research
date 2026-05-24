import pandas as pd
import time
import warnings
from datetime import datetime
import logging

logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore")


"""
A SHORT READ ME BEFORE RUNNING THIS CODE:

1. The files that need to be inputed to this run are: exploded and df_1_not_merged_2_merged_04_1_26.
2. I recommand putting the files in the directory (the pwd that you are working on - git hub folder) with the names founded in the read.csv
3. Since we are checking Please verify that both files you input contain the missing row: 
    session 100209964 of in choice-set 1106139, id_rep: 30000683 chosen_date: 28/05/2017 01:48:37
4. This code runs with a logger, meaning that logs will not appear in the terminal, but will create a text files with all logs.
5. That way we can look excactly what happend for each run in the code. 
"""

# Load merged df and exploded here:
merged_df = pd.read_csv(
    "df_1_not_merged_2_merged.csv"
)
df_exploded = pd.read_csv("df_exploded_all_data.csv")
today_str = datetime.today().strftime("%d_%m_%Y")

 
def main(merged_df: pd.DataFrame, df_exploded: pd.DataFrame, today_str: str):
    logging.basicConfig(level=logging.INFO, filename=f"big_code_{today_str}.txt")
    # Start timing
    start_time = time.time()
    # STEP 1: Preprocess merged_df
    # Make sure to strip column names (remove extra spaces)
    merged_df.columns = merged_df.columns.str.strip()
    df_exploded.columns = df_exploded.columns.str.strip()
    # Sort for efficient filtering
    merged_df = merged_df.sort_values(["id_session", "end_time"])

    # Group by session for fast access
    logger.info(
        "Creating a tuple grouped by object of sessions in merged df for faster processing..."
    )
    session_groups = dict(tuple(merged_df.groupby("id_session")))

    # List to collect results
    filtered_events_list = []

    # STEP 2: Iterate over df_exploded
    for row in df_exploded.reset_index().itertuples(index=False):
        logger.info(f"Working on concurrent session: {row.concurrent_sessions}")
        session_events = session_groups.get(row.concurrent_sessions, pd.DataFrame())
        logger.info(f"Found {len(session_events)} events of that session in merged_df")

        if session_events.empty:
            logger.info(f"session has no rows in merged_df, continue!")
            logger.info("--------------------------------------")
            continue

        # Get only events before the choice time
        past_events = session_events[session_events["end_time"] < row.time]

        if past_events.empty:
            logger.info(
                f"session has no rows earlier than the chosen time in merged_df, continue!"
            )
            logger.info("--------------------------------------")
            continue

        # Get the latest event
        closest_time = past_events["end_time"].max()
        filtered_event = past_events[past_events["end_time"] == closest_time]

        # Keep only event_type == 1
        filtered_event = filtered_event[filtered_event["event_type"] == 1]

        if not filtered_event.empty:
            # Enrich the row
            filtered_event = filtered_event.assign(
                choice_set=row.index,
                chosen=row.chosen,
                waiting_time=row.time - filtered_event["end_time"],
                workload=row.workload,
            )

            filtered_events_list.append(filtered_event)
            logger.info(
                f"Enriched filtered events list by 1 row: {row.concurrent_sessions}, {row.chosen}, {row.time}"
            )
        else:
            logger.info("Closet time row is not event_type = 1, continuing.....")

        logger.info("--------------------------------------")

    # STEP 3: Combine and save
    if filtered_events_list:
        all_filtered_events = pd.concat(filtered_events_list, ignore_index=True)
        all_filtered_events.to_csv(f"df_choicesets_{today_str}.csv", index=False)
    else:
        logger.info("No filtered events found.")

    # STEP 4: Execution time
    end_time = time.time()
    logger.info(f"Execution time: {end_time - start_time:.2f} seconds")


if __name__ == "__main__":
    main(merged_df, df_exploded, today_str)
