import pandas as pd
import os

pd.set_option("display.max_columns", None)
pd.set_option("display.max_rows", 500)
os.chdir(os.path.dirname(os.path.abspath(__file__)))


def validate_n_messages(df_reg: pd.DataFrame, df_raw: pd.DataFrame) -> dict:
    """
    Choice-set-level validation based on row-level message checks.

    For each row in df_reg:
    - go to df_raw with the same id_session
    - look in the interval [end_time, chosen_time]
    - verify:
        1. the number of raw customer messages (event_type == 1) equals n_messages
        2. if chosen == 1: there is exactly one event_type == 2 in that interval
        3. if chosen == 0: there are zero event_type == 2 in that interval

    Final output is at choice_set level:
    - if all rows in the choice_set are valid -> True
    - otherwise -> False

    Returns a dictionary:
    {
        "choice_set_id": [True/False]
    }
    """
    print("begining validate_n_messages")
    df_reg = df_reg.copy()
    df_raw = df_raw.copy()

    df_reg.columns = df_reg.columns.str.strip()
    df_raw.columns = df_raw.columns.str.strip()

    df_raw = df_raw[df_raw["event_type"].isin([1, 2])].copy()

    if "chosen_time" not in df_reg.columns:
        if "waiting_time" in df_reg.columns:
            df_reg["chosen_time"] = df_reg["end_time"] + df_reg["waiting_time"]
        else:
            raise ValueError(
                "df_reg must contain either 'chosen_time' or 'waiting_time'."
            )

    raw_groups = dict(tuple(df_raw.groupby("id_session")))
    success = {}

    for choice_set, choice_df in df_reg.groupby("choice_set"):
        key = str(choice_set)
        success[key] = []

        all_rows_valid = True

        for row in choice_df.itertuples(index=False):
            session_raw = raw_groups.get(row.id_session, pd.DataFrame())

            if session_raw.empty:
                all_rows_valid = False
                continue

            # Mirror Stage 3 v2 logic: find last type=2 before chosen_time, then
            # count type=1 events after it. This avoids dependence on row.end_time,
            # which was updated to the type=7 timestamp in Stage 1 and no longer
            # matches the original type=1 end_time in the raw data.
            past_type2 = session_raw[
                (session_raw["event_type"] == 2) &
                (session_raw["end_time"] < row.chosen_time)
            ]
            last_type2_time = past_type2["end_time"].max() if not past_type2.empty else -1

            raw_message_count = session_raw[
                (session_raw["event_type"] == 1) &
                (session_raw["end_time"] > last_type2_time) &
                (session_raw["end_time"] < row.chosen_time)
            ].shape[0]

            event_type_2_count = session_raw[
                (session_raw["event_type"] == 2) &
                (session_raw["end_time"] > last_type2_time) &
                (session_raw["end_time"] <= row.chosen_time)
            ].shape[0]

            if row.chosen == 1:
                is_valid = (
                    raw_message_count == row.n_messages and event_type_2_count == 1
                )
            else:
                is_valid = (
                    raw_message_count == row.n_messages and event_type_2_count == 0
                )

            if not is_valid:
                all_rows_valid = False

        success[key].append(all_rows_valid)

    return success


def checker(
    choicesets_list: list, df_choicesets: pd.DataFrame, raw_df: pd.DataFrame
) -> dict:
    """
    Colleague's choice-set-level checker.

    Returns a dictionary:
    {
        "choice_set_id": [check1_bool, check2_bool, check3_bool]
    }
    """
    print("begining Nadav's checker function")

    df_choicesets = df_choicesets.copy()
    raw_df = raw_df.copy()

    df_choicesets.columns = df_choicesets.columns.str.strip()
    raw_df.columns = raw_df.columns.str.strip()

    raw_df = raw_df[raw_df["event_type"].isin([1, 2, 7])].copy()

    df_choicesets["end_time"] = pd.to_datetime(df_choicesets["end_time"])
    raw_df["end_time"] = pd.to_datetime(raw_df["end_time"])

    if "chosen_time" not in df_choicesets.columns:
        if "waiting_time" in df_choicesets.columns:
            df_choicesets["chosen_time"] = df_choicesets["end_time"] + pd.to_timedelta(
                df_choicesets["waiting_time"]
            )
        else:
            raise ValueError(
                "df_choicesets must contain either 'chosen_time' or 'waiting_time'."
            )

    df_choicesets["chosen_time"] = pd.to_datetime(df_choicesets["chosen_time"])

    sucsess = {}

    for choice in choicesets_list:
        filt_choicesets = df_choicesets[df_choicesets["choice_set"] == choice]

        if filt_choicesets.empty:
            continue

        min_time = filt_choicesets["end_time"].min()
        chosen_time = filt_choicesets["chosen_time"].iloc[0]
        id_rep = filt_choicesets["id_rep"].iloc[0]
        chosen_session_choicesets = filt_choicesets[filt_choicesets["chosen"] == 1][
            "id_session"
        ]

        filt_raw_df = raw_df[
            (raw_df["id_rep"] == id_rep)
            & (raw_df["end_time"] >= min_time)
            & (raw_df["end_time"] <= chosen_time)
        ]

        key = str(choice)
        if key not in sucsess:
            sucsess[key] = []

        chosen_session_rawdata = filt_raw_df[filt_raw_df["end_time"] == chosen_time][
            "id_session"
        ]

        if not chosen_session_choicesets.empty and not chosen_session_rawdata.empty:
            sucsess[key].append(
                bool(
                    chosen_session_choicesets.iloc[0] == chosen_session_rawdata.iloc[0]
                )
            )
        else:
            sucsess[key].append(False)

        filt_raw_df_time = filt_raw_df[filt_raw_df["end_time"] > chosen_time]
        if filt_raw_df_time.empty:
            sucsess[key].append(True)
        else:
            sucsess[key].append(False)

        unique_sessions_raw = []
        filt_raw_df = filt_raw_df[~(filt_raw_df["end_time"] == chosen_time)]
        sessions_raw = filt_raw_df["id_session"].unique().tolist()

        for session in sessions_raw:
            df_last = (
                filt_raw_df.sort_values("end_time")
                .groupby("id_session", as_index=False)
                .tail(1)
            )

            df_last = df_last[df_last["id_session"] == session]
            if df_last["event_type"].iloc[0] in [1, 7]:
                unique_sessions_raw.append(df_last["id_session"].iloc[0])

        unique_sessions_choiceses = filt_choicesets["id_session"].unique().tolist()

        if set(unique_sessions_raw) == set(unique_sessions_choiceses):
            sucsess[key].append(True)
        else:
            sucsess[key].append(False)

    return sucsess


def segment_choice(
    df: pd.DataFrame, segment: str, quantile: int, prefix: str
) -> pd.DataFrame:
    try:
        threshold = df[segment].quantile(quantile)
        low_list = ["low_waiting_time", "low_workload", "low_n_messages"]
        if prefix.lower() in low_list:
            top_ids = df.loc[df[segment] < threshold, "choice_set"].unique()
            if len(top_ids) == 0:
                top_ids = df.loc[df[segment] == threshold, "choice_set"].unique()
        else:
            top_ids = df.loc[df[segment] > threshold, "choice_set"].unique()
            if len(top_ids) == 0:
                top_ids = df.loc[df[segment] == threshold, "choice_set"].unique()
        sampled_choicesets = pd.Series(top_ids).sample(n=5)
        filtered_df = df[df["choice_set"].isin(sampled_choicesets)]
        return filtered_df
    except Exception as e:
        print(f"Failed at segment_choice : {e}")


def choose_choicets(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    """
    This function filters df_choicesets acording to desired group of choicsets

    """
    print("begining choose_choicets function ")

    try:
        # high number of events (3 with 7 messages, 3 with 6, 1 with 3):
        if prefix.lower() == "high_events":
            df_grouped = df.groupby("choice_set").size().reset_index(name="count")

            grouped_filt = df_grouped[df_grouped["count"] >= 5]
            sampled_choicesets = grouped_filt["choice_set"].sample(n=5)
            filtered_df = df[df["choice_set"].isin(sampled_choicesets)].reset_index(
                drop=True
            )

        # Choice sets in which one of the events has a high number of n-messages (number of sequential messages in given turn, percentile 90+).
        elif prefix.lower() == "high_n_messages":
            filtered_df = segment_choice(df, "n_messages", 0.85, "high_n_messages")
        elif prefix.lower() == "high_waiting_time":
            filtered_df = segment_choice(df, "waiting_time", 0.85, "high_waiting_time")
        elif prefix.lower() == "high_workload":
            filtered_df = segment_choice(df, "workload", 0.85, "high_workload")
        elif prefix.lower() == "low_waiting_time":
            filtered_df = segment_choice(df, "waiting_time", 0.1, "low_waiting_time")
        elif prefix.lower() == "low_n_messages":
            filtered_df = segment_choice(df, "n_messages", 0.1, "low_n_messages")
        elif prefix.lower() == "low_workload":
            filtered_df = segment_choice(df, "workload", 0.1, "low_workload")
        else:
            return

        return filtered_df

    except Exception as e:
        print(f"Failed at choose_choicets : {e}")


def main(specific_check=True):
    try:
        print("reading reg...")

        df_reg = pd.read_csv(r"E:\github\df_choicesets_06_05_2026.csv")
        print("reading df_raw...")
        cols = ["end_time", "event_type", "id_session", "end_date", "id_rep"]
        # define specific columns since reading the df takes time:
        df_raw = pd.read_csv(
            r"E:\github\agents_merged_fixed_28_5.csv",
            usecols=cols,
        )
        df_raw.columns = df_raw.columns.str.replace(" ", "")
        print("read both dfs")
        prefix_list = [
            "high_n_messages",
            "high_waiting_time",
            "high_workload",
            "low_waiting_time",
            "low_n_messages",
            "low_workload",
        ]
        for prefix in prefix_list:
            print("")
            print(f"Current segmentation: {prefix}")
            needed_choicesets = prefix
            # Insert a logic that fetches and cleans df_choicests acording to selected choicests
            #### SPECIFIC CHECKER: #####
            if specific_check:
                df_reg_filt = df_reg[df_reg["choice_set"] == 302733]
            else:
                df_reg_filt = choose_choicets(df_reg, needed_choicesets)

            my_result = validate_n_messages(df_reg_filt, df_raw)

            choicesets_list = sorted(df_reg_filt["choice_set"].unique().tolist())
            colleague_result = checker(choicesets_list, df_reg_filt, df_raw)

            print("\n FIRST FUNCTION RESULT:")
            for choice_set, results in my_result.items():
                if all(results):
                    print(f"{choice_set}: SUCCESS -> {results}")
                else:
                    print(f"{choice_set}: FAILURE -> {results}")

            print("\n SECOND FUNCTION RESULT:")
            for choice_set, results in colleague_result.items():
                if all(results):
                    print(f"{choice_set}: SUCCESS -> {results}")
                else:
                    print(f"{choice_set}: FAILURE -> {results}")

    except Exception as e:
        print(f"Failed at main function: {e}")


if __name__ == "__main__":
    main()
