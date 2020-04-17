
# import packages
import os
import json
import pandas as pd
import numpy as np
import ruamel.yaml

pd.set_option('display.max_rows', 500)
pd.set_option('display.max_columns', 500)
pd.set_option('display.width', 1000)

PROJECT_PATH = "C://Clemens//Learning//dash//soccer_dashboard"
os.chdir(PROJECT_PATH)

PATH = "data//raw_data//wyscout_data"
PATH_CLEAN = "data"

country = "Germany"
fname_events = f"events/events_{country}.json"
fname_tags = "tags.csv"
fname_players = "players.parquet"
fname_matches = f"matches_{country.lower()}.parquet"
fname_out = f"events_{country.lower()}.parquet"


def compute_possession(row):
    """
    Helper function to compute the team that is currently in position
    """
    if row["eventName"] in ["Pass", "Free Kick", "Others on the ball", "Shot", "Save attempt", "Goalkeeper leaving line"]:
        return row["teamId"]
    elif row["eventName"] == "Duel" and row["Accurate"] == 1:
        return row["teamId"]
    elif row["eventName"] == "Duel" and row["Accurate"] == 0:
        if row["teamId"] == row["homeTeamId"]:
            return row["awayTeamId"]
        else:
            return row["homeTeamId"]
    elif row["eventName"] in ["Foul", "Interruption", "Offside"]:
        return 0
    else:
        return np.nan


def add_position_in_meters(df, cols_length, cols_width, field_length, field_width):
    """
    Function computes the position in meters instead of only a 0-100 scale.

    :param df: (pd.DataFrame) Data frame containing x and/or y coordinates
    :param cols_length: (list) Columns that contain values in x-direction
    :param cols_width: (list) Columns that contain values in y-direction
    :param field_length: (int) Length of the field in meters
    :param field_width: (int) Width of the field in meters
    :return: pd.DataFrame with additional columns ending in "Meters" that contain the coordinates in meters.
    """
    for col in cols_length:
        df[col + "Meters"] = np.where(df[col].notnull(), df[col] * field_length / 100, np.nan)
        df[col + "Meters"] = np.round(df[col + "Meters"], 2)
    for col in cols_width:
        df[col + "Meters"] = np.where(df[col].notnull(), df[col] * field_width / 100, np.nan)
        df[col + "Meters"] = np.round(df[col + "Meters"], 2)

    return df


with open("config.yml", "r", encoding="utf-8") as f:
    config = ruamel.yaml.YAML().load(f)

field_length = config["general"]["field_length"]
field_width = config["general"]["field_width"]

# read the different tags
tags = pd.read_csv(os.path.join(PATH, fname_tags), sep=";")
dict_tags = {row["Tag"]: row["Description"] for _, row in tags.iterrows()}

# read the JSON file
with open(os.path.join(PATH, fname_events)) as json_file:
    events = json.load(json_file)

# normalize to get a pandas data frame
df_events = pd.json_normalize(events)

# save positions in different columns
df_events["posBeforeX"] = df_events["positions"].map(lambda x: x[0]["x"])
df_events["posBeforeY"] = df_events["positions"].map(lambda x: x[0]["y"])
df_events["posAfterX"] = df_events["positions"].map(lambda x: x[1]["x"] if len(x) > 1 else np.nan)
df_events["posAfterY"] = df_events["positions"].map(lambda x: x[1]["y"] if len(x) > 1 else np.nan)

# save tags in different columns
df_events["tags"] = df_events["tags"].map(lambda x: [tag["id"] for tag in x])
for key in dict_tags:
    df_events[dict_tags[key]] = 1*df_events["tags"].map(lambda x: key in x)

# drop columns that are not needed
df_events.drop(["positions", "tags"], axis=1, inplace=True)

num_cols = ["subEventId"]
for col in num_cols:
    df_events[col] = pd.to_numeric(df_events[col], errors="coerce")

# make sure that the event "Offside" also leads to a subevent "Offside"
df_events["subEventName"] = np.where(df_events["eventName"] == "Offside", "Offside", df_events["subEventName"])

# make sure the goal kick is always taken at the own goal
df_events["posBeforeX"] = np.where(df_events["subEventName"] == "Goal kick", 5, df_events["posBeforeX"])
df_events["posBeforeY"] = np.where(df_events["subEventName"] == "Goal kick", 50, df_events["posBeforeY"])

# make sure the save attempt always happens at the own goal (currently at (0,0) or (100,100))
df_events["posBeforeX"] = np.where(df_events["subEventName"].isin(["Save attempt", "Reflexes"]), 0,
                                   df_events["posBeforeX"])
df_events["posBeforeY"] = np.where(df_events["subEventName"].isin(["Save attempt", "Reflexes"]), 50,
                                   df_events["posBeforeY"])

# get the position of the event in meters
df_events = add_position_in_meters(df_events, cols_length=["posBeforeX", "posAfterX"],
                                   cols_width=["posBeforeY", "posAfterY"], field_length=field_length,
                                   field_width=field_width)

# Prepare the output table
##########################

# drop columns that are not needed any more
pos_cols = [col for col in df_events.columns if col.startswith("Position:")]
cols_drop = ["eventId", "subEventId", "posBeforeX", "posAfterX", "posBeforeY", "posAfterY", "Free space right",
             "Free space left", "Missed ball", "Take on left", "Take on right", "Sliding tackle", "Through", "Fairplay",
             "Lost", "Neutral", "Won", "Red card", "Yellow card", "Second yellow card", "Anticipated", "Anticipation",
             "High", "Low", "Interception", "Clearance", "Opportunity", "Feint", "Blocked"] + pos_cols
cols_drop = [col for col in cols_drop if col in df_events.columns]
df_events.drop(cols_drop, axis=1, inplace=True)

# add some player information
df_players = pd.read_parquet(os.path.join(PATH_CLEAN, fname_players))
df_players = df_players[["playerId", "shortName", "foot", "role.code2"]].copy()
df_players.rename(columns={"role.code2": "playerPosition", "foot": "playerStrongFoot", "shortName":"playerName"},
                  inplace=True)
df_events = pd.merge(df_events, df_players, on="playerId", how="left")

# add home and away team
df_matches = pd.read_parquet(os.path.join(PATH_CLEAN, fname_matches))

for side in ["home", "away"]:
    df_side = df_matches[df_matches["side"] == side][["matchId", "teamId"]]
    df_side.rename(columns={"teamId": f"{side}TeamId"}, inplace=True)
    df_events = pd.merge(df_events, df_side, on="matchId", how="left")

# compute the team that is currently in possession of the ball
df_events["teamPossession"] = df_events.apply(lambda row: compute_possession(row), axis=1)

# change column names to camelCase
lowercase_cols = [col[0].lower() + col[1:] for col in df_events.columns]
df_events.columns = lowercase_cols

col_changes = {"own goal": "ownGoal",
               "key pass": "keyPass",
               "counter attack": "counterAttack",
               "left foot": "leftFoot",
               "right foot": "rightFoot",
               "dangerous ball lost": "dangerousBallLost",
               "not accurate": "notAccurate"}

df_events.rename(columns=col_changes, inplace=True)

# bring columns into correct order
col_order = ["id", "matchId", "matchPeriod", "eventSec", "eventName", "subEventName", "teamId", "posBeforeXMeters",
             "posBeforeYMeters", "posAfterXMeters", "posAfterYMeters", "playerId", "playerName", "playerPosition",
             "playerStrongFoot", "teamPossession", "homeTeamId", "awayTeamId", "accurate", "notAccurate"]

other_cols = [col for col in df_events.columns if not col in col_order]
col_order = col_order + other_cols
df_events = df_events[col_order].copy()

df_events.to_parquet(os.path.join(PATH_CLEAN, fname_out))
