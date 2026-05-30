import pandas as pd
import logging
import time

logging.basicConfig(level=logging.INFO)


choicesets = pd.read_csv(r"C:\Users\nadid\Downloads\df_choicesets_18_05_2026.csv")
logging.info(f"There are {choicesets.shape[0]} rows in choicests_df")

"""
This file recieves a choiceset data set (before unification of customer's meeseges and unifies them
"""


def main():
    """
    The actual job that returns the unified df (Please add more steps if needed)
    """
    start_time = time.time()
    logging.info(f"Starting unification code")

    # Setting up grouping variables:
    column_to_sum_by = "choice_set"
    columns_to_sum = ["sentiment", "duration", "number_words"]
    columns_to_keep_original_val = [
        col for col in choicesets.columns if col not in columns_to_sum
    ]
    group_id = (
        (choicesets[column_to_sum_by] != choicesets[column_to_sum_by].shift())
        | (choicesets["id_session"] != choicesets["id_session"].shift())
    ).cumsum()

    agg_dict = {}
    # sum columns
    for col in columns_to_sum:
        agg_dict[col] = "sum"

    # keep first value for all other columns
    for col in columns_to_keep_original_val:
        agg_dict[col] = "first"

    merged_df_gpt = (
        choicesets.groupby(group_id, sort=False)
        .agg(
            {
                **{col: "sum" for col in columns_to_sum},
                **{col: "first" for col in columns_to_keep_original_val},
            }
        )
        .assign(n_messages=choicesets.groupby(group_id).size().values)
        .reset_index(drop=True)
    )

    end_time = time.time()
    logging.info(f"Execution time: {end_time - start_time:.2f} seconds")

    logging.info(
        (
            f"Rows in unified df: {merged_df_gpt.shape[0]} |  Rows in un-unified df: {choicesets.shape[0]}"
        )
    )
    return merged_df_gpt


if __name__ == "__main__":
    main()
