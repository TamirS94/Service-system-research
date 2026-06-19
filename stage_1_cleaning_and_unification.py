# ==============================
# Stage 1 - Cleaning, Sorting and Unifying Consecutive Agent Messages
# ==============================

# Imports and enviorments set up:
import pandas as pd
import os
import time
import logging
from datetime import date
from agent_unification import unify_agent_messages

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


def rep_fix(df: pd.DataFrame) -> pd.DataFrame:
    """
    Replaces placeholder id_rep=1 with the actual assigned agent ID per (id_session, subsession).
    Early customer messages arrive before agent assignment and carry id_rep=1; this propagates
    the first real agent ID back to those rows.
    """
    logger.info("\n Initiating rep_id fix")
    real_reps = (
        df[df["id_rep"] != 1]
        .groupby(["id_session", "subsession"])["id_rep"]
        .first()
        .rename("_real_rep")
    )
    df = df.merge(real_reps, on=["id_session", "subsession"], how="left")
    mask = (df["id_rep"] == 1) & df["_real_rep"].notna()
    df.loc[mask, "id_rep"] = df.loc[mask, "_real_rep"]
    df = df.drop(columns=["_real_rep"])
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




def main():
    # Assigining a dynamic str variable for today:
    today_str = date.today().strftime("%Y-%m-%d")

    logger.info("\n Loading raw dataset and fixing column names")
    load_start = time.time()

    # Reading data:
    session_events_merged = pd.read_csv("df_raw_filtered_test_19_6.csv")
    session_events_merged = session_events_merged.rename(columns=lambda x: x.strip())
    logger.info(f"   Loaded {session_events_merged.shape[0]:,} rows")
    logger.info(f"   Time: {time.time() - load_start:.2f} sec")

    # Cleaning Data:
    session_events_merged = clean_data(session_events_merged)
    # Fixing id_rep:
    session_events_merged = rep_fix(session_events_merged)
    # Fixing evet type = 7 error - TODO - CHECK IF WORKS PROPERLY - CRITICAL
    session_events_merged = event_type_7_fix(session_events_merged)

    # Agent-perspective unification (replaces old session-perspective merge):
    df_combined = unify_agent_messages(session_events_merged)
    df_combined["event_id"] = df_combined.index + 1

    logger.info(f"   Final dataset rows: {df_combined.shape[0]:,}")
    df_combined.to_csv(f"df_after_stage1_{today_str}_test.csv", index=False)

    logger.info("\n==============================")
    logger.info("✅ STAGE 1 COMPLETED SUCCESSFULLY")
    logger.info("==============================")



if __name__ == "__main__":
    main()
