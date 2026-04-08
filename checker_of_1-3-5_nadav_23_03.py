import pandas as pd


def checker(
    choicesets_list: list, df_choicesets: pd.DataFrame, raw_df: pd.DataFrame
) -> list:
    # Create an empty dict for later:
    sucsess = {}
    # Iterate over list of choice-sets:
    for choice in choicesets_list:
        # filter choicesets df for desired choicesets:
        filt_choicesets = df_choicesets[df_choicesets["choice_set"] == choice]

        # Extract desired variables:
        min_time = filt_choicesets["end_time"].min()
        chosen_time = filt_choicesets["chosen_time"].iloc[0]
        id_rep = filt_choicesets["id_rep"].iloc[0]
        chosen_session_choicesets = filt_choicesets[filt_choicesets["chosen"] == 1][
            "id_session"
        ]

        # Filter rae df by min and max time and id_rep:
        filt_raw_df = raw_df[
            (raw_df["id_rep"] == id_rep)
            & (raw_df["end_time"] >= min_time)
            & (raw_df["end_time"] <= chosen_time)
        ]

        # 1) The id_session of the event in the choice_set that has chosen = 1 should be the same id_session that has an event_type_desc of rep_line (event_type = 2)
        chosen_session_rawdata = filt_raw_df[filt_raw_df["end_time"] == chosen_time][
            "id_session"
        ]

        if chosen_session_choicesets.iloc[0] == chosen_session_rawdata.iloc[0]:
            key = str(choice)
            if key not in sucsess:
                sucsess[key] = []

            sucsess[key].append(True)
        else:
            if key not in sucsess:
                sucsess[key] = []

            sucsess[key].append(False)

        # 3)  All events in choice_set have end_time lower than the choosing event (rep_line) in raw data
        filt_raw_df_time = filt_raw_df[filt_raw_df["end_time"] > chosen_time]
        if filt_raw_df_time.empty:
            sucsess[key].append(True)
        else:
            sucsess[key].append(False)

        # 5) count of unique id_sessions is same between reg and raw data.
        unique_sessions_raw = filt_raw_df["id_session"].unique().tolist()
        unique_sessions_choiceses = filt_choicesets["id_session"].unique().tolist()
        if set(unique_sessions_raw) == set(unique_sessions_choiceses):
            sucsess[key].append(True)
        else:
            sucsess[key].append(False)

    return sucsess


def main():

    # Enter you local path here
    df_choicesets = pd.read_csv(r"ENTER PATH HERE")
    # Create choice-time var:
    df_choicesets["chosen_time"] = (
        df_choicesets["end_time"] + df_choicesets["waiting_time"]
    )
    # ENTER PATH HERE - I use usecols because this df is huge and it takes time to read it, thus I only load columns I work with:
    raw_df = pd.read_csv(
        r"ENTER PATH HERE",
        usecols=[
            " id_session",
            " event_type",
            " end_time",
            " event_type_desc",
            " end_date",
            " id_rep",
        ],
    )
    # Update col names without space:
    raw_df.columns = raw_df.columns.str.replace(" ", "")
    # Filter for desired events:
    raw_df = raw_df[raw_df["event_type"].isin([1, 2])]

    # Call the checker func:
    result_dict = checker([271, 291], df_choicesets, raw_df)

    for choice_set, results in result_dict.items():
        performance_lst = []
        if all(results):
            print(f"{choice_set}: SUCCESS")
        else:
            print(f"{choice_set}: FAILURE")


main()
