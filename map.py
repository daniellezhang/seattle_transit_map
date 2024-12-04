import geopy.distance
import numpy as np
from sklearn.neighbors import BallTree
import pandas as pd
from datetime import datetime
from datetime import timedelta
import folium
import webbrowser

# waking to stop speed: mi/h
WALKING_TO_STOP_SPEED = 3
# max walking to stop time: 15 minutes
MAX_WALKING_TO_STOP_TIME = 15 * 60

def custom_distance(a, b):
    manhattan_distance = (geopy.distance.distance((a[0], a[1]), (a[0], b[1])) + geopy.distance.distance((a[0], a[1]), (b[0], a[1]))).miles
    return manhattan_distance /WALKING_TO_STOP_SPEED * 3600

def custom_route_id_with_direction(row):
    return row['route_id'] + "-" + str(row['direction_id'])

f = open("./input.txt", 'r')
inputs = f.read().split(',')
f.close()
destination_lat = float(inputs[0])
destination_lon = float(inputs[1])
max_commute_time = float(inputs[2]) * 60

stops = pd.read_csv("./gtfs_puget_sound_consolidated/stops.txt")

# Using BallTree to get a list of transit stops that are close to the destination
stops_tree = BallTree(stops[["stop_lat", "stop_lon"]].values, metric = lambda a,b: custom_distance(a,b))
closest_stops_indices, distances = stops_tree.query_radius([[destination_lat, destination_lon]], min(MAX_WALKING_TO_STOP_TIME,max_commute_time), True)
stops = stops[["stop_lat", "stop_lon", "stop_id"]]

stop_set = set()
# set to track the stops traversed
for i in range(len(closest_stops_indices[0])):
    index = closest_stops_indices[0][i]
    lat = stops.loc[index]["stop_lat"]
    lon = stops.loc[index]["stop_lon"]
    stop_id = stops.loc[index]["stop_id"]
    stop_set.add(stop_id)

# new column in stops df to track the list of routes that go through the stops
sets = [set() for i in stops.index]
stops.insert(3, "routes", sets, allow_duplicates=False)

# process stop_times and trip data
stop_times = pd.read_csv("./gtfs_puget_sound_consolidated/stop_times.txt", low_memory=False)
stop_times["arrival_time"]= pd.to_timedelta(stop_times["arrival_time"])
stop_times = stop_times[["trip_id", "stop_id", "arrival_time"]]
trips = pd.read_csv("./gtfs_puget_sound_consolidated/trips.txt", low_memory=False)
trips = trips[["route_id", "trip_id", "direction_id"]]

#building graph with routes that go through the starting stops
unique_trip_id_set = set(stop_times.query('stop_id in @stop_set')["trip_id"])
relevant_trips = trips.query('trip_id in @unique_trip_id_set')

#filter out the data in stop_times based on trip_id
stop_times = stop_times.query('trip_id in @unique_trip_id_set')

# create new route_id that includes direction
relevant_trips['route_id_with_direction'] = relevant_trips.apply(custom_route_id_with_direction, axis=1)

# build a new df for route information and max route traveling stop-to-stop time
travel_graph= pd.DataFrame(columns = ["route_id_with_direction", "stop_list", "arrival_time_list", "max_total_travel_time"])

for index, row in relevant_trips.iterrows():
    route_id_with_direction = row["route_id_with_direction"]
    trip_id = row["trip_id"]
    current_trip = stop_times.loc[stop_times["trip_id"] == trip_id].sort_values(by=["arrival_time"])
    current_stops = current_trip["stop_id"].tolist()
    if not travel_graph["route_id_with_direction"].str.contains(route_id_with_direction).any():
        travel_time = np.max(current_trip["arrival_time"]) - np.min(current_trip["arrival_time"])
        new_row = {"route_id_with_direction": route_id_with_direction, "stop_list": current_stops, "arrival_time_list": current_trip["arrival_time"].tolist(), "max_total_travel_time": travel_time}
        travel_graph = travel_graph._append(new_row, ignore_index=True)
        for stop_id in current_stops:
            stops.loc[stops["stop_id"] == stop_id]["routes"].values[0].add(route_id_with_direction)
    else:
        max_total_travel_time = travel_graph.loc[travel_graph["route_id_with_direction"] == route_id_with_direction]["max_total_travel_time"].values[0]
        trip_stop_times = stop_times.loc[(stop_times["trip_id"] == trip_id)]
        total_travel_time = np.max(trip_stop_times["arrival_time"]) - np.min(trip_stop_times["arrival_time"])
        # update the travel time if this trip's total travel time is longer
        if total_travel_time > max_total_travel_time:
            travel_graph.loc[travel_graph["route_id_with_direction"] == route_id_with_direction]["max_total_travel_time"].values[0] = total_travel_time
            travel_graph.loc[travel_graph["route_id_with_direction"] == route_id_with_direction]["arrival_time_list"].values[0] = trip_stop_times
            travel_graph.loc[travel_graph["route_id_with_direction"] == route_id_with_direction]["stop_list"].values[0] = current_stops

# new df to track all the stops that satisfy the max commute time radius
map = stops.iloc[closest_stops_indices[0]]
map = map.rename(columns={'stop_lat': 'lat','stop_lon':'lon'})[['lat','lon']]
map = map.assign(time = distances[0])

max_commute_time_timedelta = timedelta(seconds=max_commute_time)

for i in closest_stops_indices[0]:
    stop_id = str(stops.loc[i]["stop_id"])
    routes = stops.loc[i]["routes"]
    max_travel_time_for_current_destination = max_commute_time - map.loc[i]["time"]
    # traverse on the routes that go through the given stop_id
    for route in routes:
        route_info = travel_graph.loc[travel_graph["route_id_with_direction"] == route]
        stop_list = route_info["stop_list"].values[0]
        arrival_time_list = route_info["arrival_time_list"].values[0]
        current_stop = stop_list[0]
        travel_start_time = arrival_time_list[0]
        destination_stop_index = stop_list.index(stop_id)
        for x in range(0, destination_stop_index):
            travel_time = (arrival_time_list[destination_stop_index] - arrival_time_list[x]).total_seconds()
            if travel_time > 0 and travel_time < max_travel_time_for_current_destination:
                lat = stops.loc[stops["stop_id"]==stop_list[x]]["stop_lat"].values[0]
                lon = stops.loc[stops["stop_id"]==stop_list[x]]["stop_lon"].values[0]
                time = travel_time
                map.loc[-1] = [lat, lon,time]

# creating final map using folium
final_map = folium.Map(location=(destination_lat, destination_lon), zoom_start=10)#location - the center of the map, zoom_start - the resolution

fg = folium.FeatureGroup(name="Stops")
for index, row in map.iterrows():
    fg.add_child(
        folium.CircleMarker(
            (row['lat'], row['lon']),
            radius = 7,
            color="cornflowerblue",
            stroke=False,
            fill=True,
            fill_opacity=0.6,
            opacity=1,
            popup=(folium.Popup("Transit stops")),
        )
    )

final_map.add_child(fg)

fg = folium.FeatureGroup(name="Commute Destination")
fg.add_child(
    folium.CircleMarker(
        (destination_lat, destination_lon),
        radius = 12,
        color="#FF7043",
        stroke=False,
        fill=True,
        fill_opacity=1,
        opacity=1,
        popup=(folium.Popup("Commute Destination")),
    )
)
final_map.add_child(fg)

final_map.save("map.html")
webbrowser.open("map.html")
# Add layer control and show map
folium.LayerControl(collapsed=False).add_to(final_map)
final_map
