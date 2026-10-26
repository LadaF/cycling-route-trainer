"""FIT activity file, written with fit-tool.

The ride is a virtual ride (``sub_sport = virtual_activity``), so Garmin Connect and
Strava do not count it as an outdoor ride. Pauses are timer stop/start events.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from statistics import mean

from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.profile_type import (
    Activity,
    Event,
    EventType,
    FileType,
    LapTrigger,
    Manufacturer,
    SessionTrigger,
    Sport,
    SubSport,
)

from crt.record.track import Track

FIT_EPOCH = 631065600  # 1989-12-31T00:00:00Z as a Unix time


def _ms(t: datetime.datetime) -> int:
    """fit-tool timestamps: milliseconds since the Unix epoch."""
    return round(t.timestamp() * 1000)


def _timer(t: datetime.datetime, event_type: EventType) -> EventMessage:
    message = EventMessage()
    message.event = Event.TIMER
    message.event_type = event_type
    message.timestamp = _ms(t)
    return message


def _record(p) -> RecordMessage:
    message = RecordMessage()
    message.timestamp = _ms(p.time)
    message.position_lat = p.lat
    message.position_long = p.lon
    message.altitude = p.elevation
    message.distance = p.distance
    message.speed = p.speed
    message.power = round(p.power)
    if p.cadence is not None:
        message.cadence = round(p.cadence)
    if p.heart_rate is not None:
        message.heart_rate = p.heart_rate
    return message


def _totals(message: LapMessage | SessionMessage, track: Track) -> None:
    """Fields shared by the lap and the session (they cover the same ride)."""
    first, last = track.points[0], track.points[-1]
    message.timestamp = _ms(track.end)
    message.start_time = _ms(track.start)
    message.start_position_lat = first.lat
    message.start_position_long = first.lon
    message.end_position_lat = last.lat
    message.end_position_long = last.lon
    message.sport = Sport.CYCLING
    message.sub_sport = SubSport.VIRTUAL_ACTIVITY
    message.event_type = EventType.STOP
    message.total_elapsed_time = track.elapsed
    message.total_timer_time = track.timer_time
    message.total_distance = track.distance
    message.total_ascent = round(track.ascent)
    message.total_descent = round(track.descent)
    if track.timer_time > 0:
        message.avg_speed = track.distance / track.timer_time
    message.max_speed = max(p.speed for p in track.points)
    power = [p.power for p in track.points]
    message.avg_power = round(mean(power))
    message.max_power = round(max(power))
    message.total_work = round(mean(power) * track.timer_time)
    if hr := [p.heart_rate for p in track.points if p.heart_rate is not None]:
        message.avg_heart_rate = round(mean(hr))
        message.max_heart_rate = max(hr)
    # Like Garmin devices: the average cadence leaves out coasting.
    if cadence := [p.cadence for p in track.points if p.cadence]:
        message.avg_cadence = round(mean(cadence))
        message.max_cadence = round(max(cadence))


def fit_bytes(track: Track) -> bytes:
    builder = FitFileBuilder(auto_define=True)

    file_id = FileIdMessage()
    file_id.type = FileType.ACTIVITY
    file_id.manufacturer = Manufacturer.DEVELOPMENT.value
    file_id.product = 0
    file_id.time_created = _ms(track.start)
    builder.add(file_id)

    # Timer events and records in time order; at equal times a start comes before a
    # record and a stop after it.
    timeline = [(_ms(track.start), 0, _timer(track.start, EventType.START))]
    for a, b in track.pauses:
        timeline.append((_ms(a), 2, _timer(a, EventType.STOP_ALL)))
        timeline.append((_ms(b), 0, _timer(b, EventType.START)))
    timeline += [(_ms(p.time), 1, _record(p)) for p in track.points]
    timeline.append((_ms(track.end), 2, _timer(track.end, EventType.STOP_ALL)))
    timeline.sort(key=lambda item: item[:2])
    builder.add_all([message for _t, _rank, message in timeline])

    lap = LapMessage()
    _totals(lap, track)
    lap.message_index = 0
    lap.event = Event.LAP
    lap.lap_trigger = LapTrigger.SESSION_END
    builder.add(lap)

    session = SessionMessage()
    _totals(session, track)
    session.message_index = 0
    session.event = Event.SESSION
    session.first_lap_index = 0
    session.num_laps = 1
    session.trigger = SessionTrigger.ACTIVITY_END
    builder.add(session)

    activity = ActivityMessage()
    activity.timestamp = _ms(track.end)
    offset = track.end.astimezone().utcoffset() or datetime.timedelta()
    # Not converted by fit-tool: seconds since the FIT epoch, in local time.
    activity.local_timestamp = round(track.end.timestamp() + offset.total_seconds()) - FIT_EPOCH
    activity.total_timer_time = track.timer_time
    activity.num_sessions = 1
    activity.type = Activity.MANUAL
    activity.event = Event.ACTIVITY
    activity.event_type = EventType.STOP
    builder.add(activity)

    return builder.build().to_bytes()


def write_fit(track: Track, path: Path) -> None:
    path.write_bytes(fit_bytes(track))
