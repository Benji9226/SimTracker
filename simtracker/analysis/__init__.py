"""Lap analysis. Everything here works on one tidy table (a pandas DataFrame, one row per sample):

    t         seconds since session start
    lap       lap number
    dist      distance in metres (cumulative)
    speed     km/h
    throttle  0..1
    brake     0..1
    steer     steering angle
    in_pits   1 while the car is in the pit lane (those laps are never "clean")

simtracker.telemetry.loader builds that table from the game's files.
"""
