import pandas as pd
from sqlalchemy import *
from create_ref_mariadb import mariadb_connection
from create_ref_mongodb import mongodb_connection
from datetime import datetime, timedelta
import pytz


def last_forecast_weather_to_df(client):
    # Get the last update
    last_forcast_update = client.forecast.find({}, {"data" : 1}).sort("extract_date", -1).limit(1)
    last_forcast_update = list(last_forcast_update)[0]["data"]

    # Select required data from the document
    list_data = []
    for windfarm in last_forcast_update:
        for forecast in windfarm["list"]:
            dict_forecast = {}
            for key, value in forecast.items():
                dict_forecast[key] = forecast[key]
            dict_forecast["windfarm_id"] = windfarm["windfarm_id"]
            list_data.append(dict_forecast)

    # Transform list in to DataFrame
    dict_rename = {"main.temp": "temp", "main.feels_like": "feels_like", "main.temp_min": "temp_min", "main.temp_max": "temp_max",
                    "main.pressure": "pressure", "main.humidity": "humidity", "wind.speed": "wind_speed", "wind.deg": "wind_deg", 
                    "wind.gust": "wind_gust", "dt_txt": "forecast_date", "main.temp_kf": "temp_kf", "clouds.all": "clouds"}
    df = pd.json_normalize(list_data)\
    .drop(["weather", "pop", "dt", "visibility", "sys.pod", "main.sea_level", "main.grnd_level"], axis = 1)\
    .rename(dict_rename, axis = "columns")
    return df

def current_weather_to_df(client) -> pd.DataFrame:
    end = datetime.now()
    start = end - timedelta(days=7)
    current_weather = list(client.current.find({"extract_date": {'$lt': end, '$gte': start}}, {"data" : 1}).sort("extract_date", -1))
    liste = list()
    for record in current_weather:
        record = record["data"]
        for windfarm in record:
            keys_list = ["base", "cod", "coord", "name", "sys", "timezone", "visibility", "id", "weather", "clouds"]
            for key in keys_list:
                del windfarm[key]
            liste.append(windfarm)
    df_current = pd.json_normalize(liste)
    dict_rename = {"main.temp": "temp", "main.feels_like": "feels_like", "main.temp_min": "temp_min", "main.temp_max": "temp_max", "main.pressure": "pressure",
                    "main.humidity": "humidity", "main.sea_level": "sea_level", "main.grnd_level": "grnd_level", "wind.speed": "speed", "wind.deg": "deg", "wind.gust": "gust", "dt": "date"}
    df_current = df_current.rename(dict_rename, axis = "columns")
    return df_current

def power_curve(eng) -> pd.DataFrame:
    df = pd.read_sql(
        """SELECT p.windturbine_id, windspeed, power, windfarm_id,latitude,longitude
            FROM mariadb_itw.windturbines as wt inner join mariadb_itw.powercurves as p
            ON p.windturbine_id = wt.windturbine_id ;""",
        con=eng
        )
    return df

def forecast_power_by_turbine(df_forecast_weather: pd.DataFrame, df_power_curve: pd.DataFrame) -> pd.DataFrame:

    # Get estimated power for the 5-next days based on forecast wind and powercurve
    df_final = pd.DataFrame()
    for index, row in df_forecast_weather.iterrows():
        df = df_power_curve[(df_power_curve["windfarm_id"] == row["windfarm_id"]) & (df_power_curve["windspeed"] == round(row["wind_speed"], 1))]
        df_copy = df.copy()
        df_copy.loc[:,"forecast_date"] = row["forecast_date"]
        df_final = pd.concat([df_final, df_copy])
    df_final.rename(columns = {"power": "power_kw"}, inplace = True)
    df_final.drop(columns = ["latitude", "longitude"], inplace=True)
    df_final.sort_values(by=["forecast_date", "windfarm_id", "windturbine_id"], inplace=True)
    df_final.reset_index(inplace=True, drop=True)
    return df_final

def max_power_by_turbine(eng) -> pd.DataFrame:
    # function to have max power_curve from mariadb
    df_max_power = pd.read_sql(
        """SELECT windturbine_id, max(power)
            FROM mariadb_itw.powercurves
            GROUP BY windturbine_id;""",
        con=eng
        )
    return df_max_power

def date_lastupdate_forecast(client) -> datetime:
    """Return the date extraction of the last forecast weather update."""
    date = client.forecast.find({}, {"extract_date" : 1}).sort("extract_date", -1).limit(1)
    date = list(date)[0]["extract_date"]
    return date

# Fetch datas from MariaDB referentials
def fetch_windturbines_data(engine) -> pd.DataFrame :
    """
    GET all windturbines in Dataframe
    """
    df = pd.read_sql(
    """
    SELECT wt.windturbine_id AS wtId, 
        wt.code AS wtCode,
        wf.windfarm_id AS wfId, 
        wf.code AS wfCode, 
        wf.latitude wfLat,
        wf.longitude wfLon
    FROM mariadb_itw.windturbines AS wt
    INNER JOIN mariadb_itw.windfarms AS wf
        ON wf.windfarm_id = wt.windfarm_id
    WHERE wt.last_scada_update IS NOT NULL
    ORDER BY wf.code ASC, wt.code ASC
    ;
    """,
        con=engine
    )
    

    return df

def fetch_windfarms_data(engine) -> pd.DataFrame :
    """
    GET all windturbines in Dataframe
    """
    df = pd.read_sql(
    """
    SELECT wf.windfarm_id AS wfId, 
        wf.code AS wfCode, 
        wf.latitude wfLat,
        wf.longitude wfLon
    FROM mariadb_itw.windfarms AS wf
    ORDER BY wf.code ASC;
    """,
        con=engine
    )
    

    return df

# Fetch SCADA datas from MongoDB
def fetch_scada_data(client) -> pd.DataFrame:
    tz = pytz.timezone('Europe/Paris')
    current_datetime = datetime.now(tz)
    lastday_datetime = current_datetime - timedelta(hours=24)

    scada_col = client.scada
    scada_data = scada_col.find({
        "log_date": {'$gte': lastday_datetime}
    }).sort("log_date", 1)

    dfScada = pd.DataFrame(list(scada_data))
    return dfScada

# Instanciation of df
client = mongodb_connection()
eng = mariadb_connection()

df_forecast_weather = last_forecast_weather_to_df(client)
df_power_curve = power_curve(eng)
df_power_forecast = forecast_power_by_turbine(df_forecast_weather, df_power_curve)
df_wf_turbine = pd.read_sql(
        """SELECT *
            FROM mariadb_itw.windturbines;""",
        con=eng
        )
date_forecast = date_lastupdate_forecast(client) + timedelta(hours = 2)

df_windturbines = fetch_windturbines_data(eng)
df_windfarms = fetch_windfarms_data(eng)
df_scada = fetch_scada_data(client)
df_scada_final = df_scada.merge(df_windturbines, left_on="windturbine_id", right_on="wtId")