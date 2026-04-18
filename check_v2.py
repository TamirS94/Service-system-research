import pandas as pd


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

    df_reg = df_reg.copy()
    df_raw = df_raw.copy()

    df_reg.columns = df_reg.columns.str.strip()
    df_raw.columns = df_raw.columns.str.strip()

    df_raw = df_raw[df_raw["event_type"].isin([1, 2])].copy()

    df_reg["end_time"] = pd.to_datetime(df_reg["end_time"])
    df_raw["end_time"] = pd.to_datetime(df_raw["end_time"])

    if "chosen_time" not in df_reg.columns:
        if "waiting_time" in df_reg.columns:
            df_reg["chosen_time"] = df_reg["end_time"] + pd.to_timedelta(
                df_reg["waiting_time"]
            )
        else:
            raise ValueError(
                "df_reg must contain either 'chosen_time' or 'waiting_time'."
            )

    df_reg["chosen_time"] = pd.to_datetime(df_reg["chosen_time"])

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

            interval_raw = session_raw[
                (session_raw["end_time"] >= row.end_time)
                & (session_raw["end_time"] <= row.chosen_time)
            ].sort_values("end_time")

            raw_message_count = (interval_raw["event_type"] == 1).sum()
            event_type_2_count = (interval_raw["event_type"] == 2).sum()

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

    df_choicesets = df_choicesets.copy()
    raw_df = raw_df.copy()

    df_choicesets.columns = df_choicesets.columns.str.strip()
    raw_df.columns = raw_df.columns.str.strip()

    raw_df = raw_df[raw_df["event_type"].isin([1, 2])].copy()

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
                chosen_session_choicesets.iloc[0] == chosen_session_rawdata.iloc[0]
            )
        else:
            sucsess[key].append(False)

        filt_raw_df_time = filt_raw_df[filt_raw_df["end_time"] > chosen_time]
        if filt_raw_df_time.empty:
            sucsess[key].append(True)
        else:
            sucsess[key].append(False)

        unique_sessions_raw = filt_raw_df["id_session"].unique().tolist()
        unique_sessions_choiceses = filt_choicesets["id_session"].unique().tolist()
        if set(unique_sessions_raw) == set(unique_sessions_choiceses):
            sucsess[key].append(True)
        else:
            sucsess[key].append(False)

    return sucsess


def main():
    df_reg = pd.read_csv(
        r"C:\Users\nadid\OneDrive - Technion\Desktop\Nadav\Studies\Technion\service_systems\project\final_files\choicesets_after_customers_msg_unified_21_03.csv"
    )
    df_raw = pd.read_csv(
        r"C:\Users\nadid\OneDrive - Technion\Desktop\Nadav\Studies\Technion\service_systems\project\final_files\merged_session_events.csv"
    )

    my_result = validate_n_messages(df_reg, df_raw)

    choicesets_list = sorted(df_reg["choice_set"].unique().tolist())
    colleague_result = checker(choicesets_list, df_reg, df_raw)

    print("\nMY FUNCTION RESULT:")
    for choice_set, results in my_result.items():
        if all(results):
            print(f"{choice_set}: SUCCESS -> {results}")
        else:
            print(f"{choice_set}: FAILURE -> {results}")

    print("\nCOLLEAGUE FUNCTION RESULT:")
    for choice_set, results in colleague_result.items():
        if all(results):
            print(f"{choice_set}: SUCCESS -> {results}")
        else:
            print(f"{choice_set}: FAILURE -> {results}")


if __name__ == "__main__":
    main()
