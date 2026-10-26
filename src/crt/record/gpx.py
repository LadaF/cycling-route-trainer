"""GPX track, the fallback export.

Heart rate and cadence go in Garmin's TrackPointExtension, power in a plain
``<power>`` element, which Strava reads. Each pause starts a new track segment.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import gpxpy.gpx

from crt.record.track import Track, TrackPoint

TPX = "http://www.garmin.com/xmlschemas/TrackPointExtension/v1"


def _point(p: TrackPoint) -> gpxpy.gpx.GPXTrackPoint:
    point = gpxpy.gpx.GPXTrackPoint(
        round(p.lat, 7), round(p.lon, 7), elevation=round(p.elevation, 1), time=p.time
    )
    power = ET.Element("power")
    power.text = str(round(p.power))
    point.extensions.append(power)
    tpx = ET.Element(f"{{{TPX}}}TrackPointExtension")
    if p.heart_rate is not None:
        ET.SubElement(tpx, f"{{{TPX}}}hr").text = str(p.heart_rate)
    if p.cadence is not None:
        ET.SubElement(tpx, f"{{{TPX}}}cad").text = str(round(p.cadence))
    if len(tpx):
        point.extensions.append(tpx)
    return point


def gpx_xml(track: Track) -> str:
    gpx = gpxpy.gpx.GPX()
    gpx.creator = "cycling-route-trainer"
    gpx.nsmap["gpxtpx"] = TPX
    trk = gpxpy.gpx.GPXTrack(name=track.name)
    gpx.tracks.append(trk)
    segment = gpxpy.gpx.GPXTrackSegment()
    trk.segments.append(segment)
    previous = None
    for p in track.points:
        if previous is not None and track.paused_between(previous, p):
            segment = gpxpy.gpx.GPXTrackSegment()
            trk.segments.append(segment)
        segment.points.append(_point(p))
        previous = p
    return gpx.to_xml()


def write_gpx(track: Track, path: Path) -> None:
    path.write_text(gpx_xml(track), encoding="utf-8")
