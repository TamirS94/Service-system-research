# ==============================
# Stage 1 - Cleaning, Sorting and Unifying Consecutive Agent Messages
# ==============================

# Imports and enviorments set up:
import pandas as pd
import glob
import os
import numpy as np
import time
import logging
from datetime import date

pd.set_option("display.max_columns", None)
pd.set_option("display.max_rows", 500)
os.chdir(os.path.dirname(os.path.abspath(__file__)))
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# -----------------------FUNCTIONS--------------------------------------#


def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Drops rows irrelevant to the choice model:
      - outcome=4 (known abandonment): customer closed window before service started.
      - event_type 8 (other_time) / 9 (inner_wait): agent parallel-chat overhead, no customer content.

    Note: event_type 7 is NOT dropped here — see event_type_7_fix().
    """
    logger.info("\n🧹 Cleaning data...")
    before = df.shape[0]
    df = df[df["outcome"] != 4].reset_index(drop=True)
    after = df.shape[0]
    logger.info(f"   Removed silent abandonment: {before - after:,} rows")

    # Drop event types 8 and 9
    before = df.shape[0]
    df = df[~df["event_type"].isin([8, 9])].reset_index(drop=True)
    after = df.shape[0]
    logger.info(f"   Removed event types 8 & 9: {before - after:,} rows")
    return df


# For each session, find the first non-1 id_rep value and use it to replace 1s
def _replace_rep_ids(group: pd.DataFrame) -> pd.DataFrame:
    """
    This is a helper function for entire rep_fix one function below....

    Context: We Identified that when customers send the initial message their rep_id (associated agent) shows as 1 before being assigned.
    However,That message is relevant for the choice model we intend to create.
    Get the first non-1 id_rep value in the session
    """
    logger.info("\n Initiating rep_id fix")
    # Run on the session and find the earliest non 1 id_rep:
    first_non_one = (
        group[group["id_rep"] != 1]["id_rep"].iloc[0]
        if not group[group["id_rep"] != 1].empty
        else 1
    )
    # Replace all 1s in this session with the first non-1 id_rep
    group.loc[group["id_rep"] == 1, "id_rep"] = first_non_one
    return group


def rep_fix(df: pd.DataFrame) -> pd.DataFrame:
    """
    Applying the helper function: _replace_rep_ids
    """
    # Apply the function to each session:
    df = (
        df.groupby(["id_session", "subsession"])
        .apply(_replace_rep_ids)
        .reset_index(drop=True)
    )
    logger.info("\n rep_id fix implemented")
    return df


def event_type_7_fix(df: pd.DataFrame) -> pd.DataFrame:
    """
    event_type=7 marks when a customer leaves the queue and the agent first sees their message.
    The end_time of the type=1 event is therefore updated to the type=7 end_time so that
    waiting_time in Stage 3 reflects the true moment the agent could act on the session.

    """
    logger.info("\n⚙️  Applying event_type=7 fix...")

    df = df.sort_values(["id_session", "end_time"]).reset_index(drop=True)

    df["_next_event_type"] = df.groupby("id_session")["event_type"].shift(-1)
    df["_next_end_time"] = df.groupby("id_session")["end_time"].shift(-1)
    df["_next_end_date"] = df.groupby("id_session")["end_date"].shift(-1)

    type7_mask = (df["event_type"] == 1) & (df["_next_event_type"] == 7)
    df.loc[type7_mask, "end_time"] = df.loc[type7_mask, "_next_end_time"]
    df.loc[type7_mask, "end_date"] = df.loc[type7_mask, "_next_end_date"]

    df = df.drop(columns=["_next_event_type", "_next_end_time", "_next_end_date"])

    before = len(df)
    df = df[df["event_type"] != 7].reset_index(drop=True)
    logger.info(
        f"   Updated {type7_mask.sum():,} type=1 rows with type=7 end_time/end_date"
    )
    logger.info(f"   Dropped {before - len(df):,} type=7 rows. Remaining: {len(df):,}")
    return df.reset_index(drop=True)


def splitting_data_for_rep_unification(
    df,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    WE NEED TO WRITE A PROPER DOCUMANTATION FOR THIS FUNCTION
    """

    third = int(df.shape[0] / 3)
    third_2 = int(df.shape[0] / 3 * 2)

    df_part1 = df.iloc[:third]
    df_part2 = df.iloc[third:third_2]
    df_part3 = df.iloc[third_2:]

    logger.info(f"   Total rows after cleaning: {df.shape[0]:,}")
    logger.info(
        f"   Part sizes: {len(df_part1):,}, "
        f"{len(df_part2):,}, "
        f"{len(df_part3):,}"
    )
    return df_part1, df_part2, df_part3


def _grouped(df):
    """
    helper function for actual rep unification function
    """
    column_to_sum_by = "event_type"
    condition = (df[column_to_sum_by] != df[column_to_sum_by].shift()) | (
        df["id_session"] != df["id_session"].shift()
    )
    return df.groupby(condition.cumsum())


def _merge_consecutive_rows(group, columns_to_sum):
    """
    helper function for actual rep unification function
    """
    columns_to_sum = ["sentiment", "duration", "number_words"]
    columns_to_keep_original_val = [
        col for col in group.columns if col not in columns_to_sum
    ]
    if len(group) == 1:
        return group
    else:
        summed_values = group[columns_to_sum].sum()
        first_row_values = group.iloc[0][columns_to_keep_original_val]
        new_row = {**first_row_values.to_dict(), **summed_values.to_dict()}
        return pd.DataFrame([new_row], columns=group.columns)


def actual_rep_unification_function(df_1, df_2, df_3):
    grouped_1 = _grouped(df_1)
    grouped_2 = _grouped(df_2)
    grouped_3 = _grouped(df_3)

    logger.info("   Grouping objects created.")

    logger.info("\n⚙️  Merging consecutive rows...")
    start_time = time.time()

    merged_parts = []

    for i, gb in enumerate([grouped_1, grouped_2, grouped_3], start=1):
        logger.info(f"   🔹 Processing part {i}...")
        part_start = time.time()

        merged_df = gb.apply(_merge_consecutive_rows).reset_index(drop=True)
        merged_df.to_csv(f"part{i}_after_merge.csv", index=False)

        logger.info(
            f"      Saved part{i}_after_merge.csv "
            f"({merged_df.shape[0]:,} rows) "
            f"in {time.time() - part_start:.2f} sec"
        )

        merged_parts.append(merged_df)

    merged_df_part1, merged_df_part2, merged_df_part3 = merged_parts
    merged_df = pd.concat(
        [merged_df_part1, merged_df_part2, merged_df_part3]
    ).reset_index(drop=True)

    logger.info(f"\n   Total merged rows: {merged_df.shape[0]:,}")
    logger.info(f"   Merge stage time: {time.time() - start_time:.2f} sec")
    return merged_df


def combine_dfs(merged_df, df_before_merge):
    merged_df_agent = merged_df[merged_df["event_type"] == 2]
    df_before_merge_drop_2 = df_before_merge[~(df_before_merge["event_type"] == 2)]

    df_combined = (
        pd.concat([df_before_merge_drop_2, merged_df_agent])
        .sort_values(by=["id_rep", "end_time"])
        .reset_index(drop=True)
    )
    return df_combined


def main():
    # Assigining a dynamic str variable for today:
    today_str = date.today().strftime("%Y-%m-%d")

    logger.info("\n Loading raw dataset and fixing column names")
    load_start = time.time()

    # Reading data:
    session_events_merged = pd.read_csv("merged_session_events.csv")
    session_events_merged = session_events_merged.rename(columns=lambda x: x.strip())
    logger.info(f"   Loaded {session_events_merged.shape[0]:,} rows")
    logger.info(f"   Time: {time.time() - load_start:.2f} sec")

    # Cleaning Data:
    session_events_merged = clean_data(session_events_merged)
    # Fixing id_rep:
    session_events_merged = rep_fix(session_events_merged)
    # Fixing evet type = 7 error - TODO - CHECK IF WORKS PROPERLY - CRITICAL
    session_events_merged = event_type_7_fix(session_events_merged)

    session_events_merged = session_events_merged.sort_values(
        by=["id_session", "end_time"]
    ).reset_index(drop=True)
    # Creating event id column:
    session_events_merged["event_id"] = session_events_merged.index + 1
    # session_events_merged.to_csv(f"cleaned_raw_data_{today_str}.csv", index=False)

    # Begining agent unification process:
    session_events_merged_1, session_events_merged_2, session_events_merged_3 = (
        splitting_data_for_rep_unification(session_events_merged)
    )
    # Applying the unification:
    merged_df = actual_rep_unification_function(
        session_events_merged_1, session_events_merged_2, session_events_merged_3
    )
    # Combining dataframes:
    df_combined = combine_dfs(merged_df, session_events_merged)

    logger.info(f"   Final dataset rows: {df_combined.shape[0]:,}")
    df_combined.to_csv(f"df_after_stage1_{today_str}.csv", index=False)

    logger.info("\n==============================")
    logger.info("✅ STAGE 1 COMPLETED SUCCESSFULLY")
    logger.info("==============================")

    if __name__ == "__main__":
        main()
