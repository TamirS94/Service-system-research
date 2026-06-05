import pandas as pd
import time
import warnings
from datetime import datetime
import logging

logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore")

COLS_TO_SUM = ["sentiment", "duration", "number_words", "number_chars", "number_lines"]
COLS_TO_DROP = ["read_date", "read_time", "accept_date", "accept_time", "delay"]


"""
A SHORT READ ME BEFORE RUNNING THIS CODE:

1. The files that need to be inputed to this run are: exploded and df_1_not_merged_2_merged_04_1_26.
2. I recommand putting the files in the directory (the pwd that you are working on - git hub folder) with the names founded in the read.csv
3. This code runs with a logger, meaning that logs will not appear in the terminal, but will create a text files with all logs.
4. That way we can look excactly what happend for each run in the code. 
5. IMPORTANT - verify the df_1_not_merged_2_merged.csv (or after stage 1 df) is ordered by the following logic:

id_session desc/asc
end_time ordered asc
event_type desc

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
    merged_df = merged_df.sort_values(["id_session", "end_time", 'event_type'] ascending= [True, True, False])

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

        # Collect all consecutive type=1 messages since the last agent reply
        past_type2 = past_events[past_events["event_type"] == 2]

        if past_type2.empty:
            consecutive_type1 = past_events[past_events["event_type"] == 1]
        else:
            last_agent_reply_time = past_type2["end_time"].max()
            consecutive_type1 = past_events[
                (past_events["event_type"] == 1) &
                (past_events["end_time"] > last_agent_reply_time)
            ]

        if consecutive_type1.empty:
            logger.info("No pending visitor messages since last agent reply, skipping.")
            logger.info("--------------------------------------")
            continue

        consecutive_type1 = consecutive_type1.sort_values("end_time").reset_index(drop=True)
        aggregated_row = consecutive_type1.iloc[[0]].copy()
        for col in COLS_TO_SUM:
            if col in aggregated_row.columns:
                aggregated_row[col] = consecutive_type1[col].sum()
        aggregated_row["n_messages"] = len(consecutive_type1)
        cols_to_drop_present = [c for c in COLS_TO_DROP if c in aggregated_row.columns]
        aggregated_row = aggregated_row.drop(columns=cols_to_drop_present)

        first_end_time = aggregated_row["end_time"].iloc[0]
        aggregated_row = aggregated_row.assign(
            choice_set=row.index,
            chosen=row.chosen,
            waiting_time=row.time - first_end_time,
            workload=row.workload,
        )

        filtered_events_list.append(aggregated_row)
        logger.info(
            f"Enriched filtered events list by 1 row: {row.concurrent_sessions}, {row.chosen}, "
            f"n_messages={aggregated_row['n_messages'].iloc[0]}, waiting_time={row.time - first_end_time}"
        )

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
