import asyncio
import csv

import pytest

from crt.control.policy import ControlSettings, GradePolicy
from crt.devices.base import HeartRateSample, TrainerSample
from crt.devices.mock import MockTrainer, constant_power, power_from_csv
from crt.physics.model import RiderParams
from crt.session.log import RideLog, read_log
from crt.session.runner import RideRunner, replay
from crt.session.session import RideSession, RideState


def session_for(course, **kwargs) -> RideSession:
    rider = RiderParams()
    return RideSession(course, rider, GradePolicy(course, ControlSettings(), rider), **kwargs)


def feed(session, power, start, end, rate=1.0, tick=0.05):
    """Samples at ``rate`` Hz and ticks every ``tick`` s, in simulated time."""
    commands = []
    t, next_sample = start, start
    while t <= end and not session.finished:
        if t >= next_sample:
            session.add_sample(TrainerSample(t, power))
            next_sample += 1 / rate
        if (c := session.tick(t)) is not None:
            commands.append(c)
        t += tick
    return commands


def test_ride_moves_and_sends_commands(make_course):
    session = session_for(make_course(grade=0.03))
    session.set_trainer_connected(True, 0.0)
    session.start(0.0)
    commands = feed(session, 250.0, 0.0, 60.0)
    snap = session.snapshot()
    assert snap.state is RideState.RIDING
    assert 300 < snap.s < 600
    assert snap.elapsed == pytest.approx(60.0, abs=0.06)
    assert snap.ascent == pytest.approx(snap.s * 0.03, rel=0.01)
    assert commands and commands[0].params.grade_percent == pytest.approx(3.0)


def test_stale_power_counts_as_zero(make_course):
    session = session_for(make_course())
    session.set_trainer_connected(True, 0.0)
    session.start(0.0)
    session.add_sample(TrainerSample(0.0, 300.0))
    session.tick(2.0)
    assert session.snapshot().power == 300.0
    session.tick(4.0)  # the sample is 4 s old
    assert session.snapshot().power == 0.0


def test_disconnect_pauses_and_reconnect_resumes(make_course):
    session = session_for(make_course())
    session.set_trainer_connected(True, 0.0)
    session.start(0.0)
    feed(session, 250.0, 0.0, 30.0)
    session.set_trainer_connected(False, 30.0)
    assert session.state is RideState.PAUSED
    s_before = session.snapshot().s
    assert session.tick(50.0) is None
    assert session.snapshot().s == s_before
    session.set_trainer_connected(True, 60.0)
    assert session.state is RideState.RIDING
    assert session.snapshot().v == 0.0  # restarts from a standstill
    feed(session, 250.0, 60.0, 70.0)
    assert session.snapshot().elapsed == pytest.approx(40.0, abs=0.1)  # pause excluded


def test_manual_pause_is_not_resumed_by_reconnect(make_course):
    session = session_for(make_course())
    session.set_trainer_connected(True, 0.0)
    session.start(0.0)
    session.pause(1.0)
    session.set_trainer_connected(True, 2.0)
    assert session.state is RideState.PAUSED


def test_finishes_at_course_end(make_course):
    session = session_for(make_course(length=300.0), start_s=100.0)
    session.set_trainer_connected(True, 0.0)
    session.start(0.0)
    session.add_heart_rate(HeartRateSample(0.0, 120))
    feed(session, 300.0, 0.0, 120.0)
    snap = session.snapshot()
    assert snap.state is RideState.FINISHED
    assert snap.s == 300.0 and snap.remaining == 0.0
    assert snap.heart_rate == 120
    assert snap.elapsed < 60.0


def test_log_round_trip_and_replay(make_course, tmp_path):
    course = make_course(grade=0.02)
    session = session_for(course)
    session.set_trainer_connected(True, 0.0)
    session.start(0.0)
    log = RideLog(tmp_path / "ride.csv", {"course": "x", "start_s": 0.0})
    t = 0.0
    while t <= 40.0:
        sample = TrainerSample(round(t, 3), 200.0 + 50.0 * (t > 20), 90.0, 30.0)
        session.add_sample(sample)
        log.sample(sample)
        for k in range(20):
            session.tick(t + k * 0.05)
        t += 1.0
    log.state(t, session.snapshot())
    log.close()

    header, samples, states = read_log(tmp_path / "ride.csv")
    assert header == {"course": "x", "start_s": 0.0}
    assert len(samples) == 41 and samples[25].power == 250.0 and samples[0].cadence == 90.0
    replayed = replay(session_for(course), samples, until=float(states[-1]["t"]))
    assert replayed.s == pytest.approx(float(states[-1]["s_m"]), abs=1.0)


def test_power_from_csv(tmp_path):
    path = tmp_path / "p.csv"
    path.write_text("time_s,power_w\n0,100\n10,250\n# comment\n20,0\n")
    power = power_from_csv(path)
    assert [power(t) for t in (-1, 0, 9.9, 10, 25)] == [100, 100, 100, 250, 0]


class FastClock:
    """Simulated time that advances only when sleeping: deterministic and instant."""

    def __init__(self) -> None:
        self.t = 0.0
        self.speedup = 1.0

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += seconds
        await asyncio.sleep(0)


async def test_runner_rides_with_mock_trainer(make_course):
    course = make_course(length=400.0, grade=0.01)
    clock = FastClock()
    session = session_for(course)
    runner = RideRunner(session, clock)
    trainer = MockTrainer(
        constant_power(250.0), clock.now, clock.sleep, runner.trainer_sample,
        runner.trainer_connection,
    )  # fmt: skip
    snap = await asyncio.wait_for(runner.run(trainer, tick_hz=20.0), timeout=30)
    assert snap.state is RideState.FINISHED
    assert snap.s == 400.0
    assert trainer.commands and trainer.last_grade == pytest.approx(1.0)


async def test_runner_pause_resume_and_stop_are_logged(make_course, tmp_path):
    course = make_course(length=5000.0)
    clock = FastClock()
    session = session_for(course)
    log = RideLog(tmp_path / "ride.csv", {"course": "x", "start_s": 0.0})
    runner = RideRunner(session, clock, log)
    trainer = MockTrainer(
        constant_power(250.0), clock.now, clock.sleep, runner.trainer_sample,
        runner.trainer_connection,
    )  # fmt: skip

    def rider(snap) -> None:
        if snap.elapsed >= 30 and not runner_paused:
            runner_paused.append(clock.now())
            runner.pause()
        elif runner_paused and len(runner_paused) == 1 and clock.now() >= runner_paused[0] + 10:
            runner_paused.append(clock.now())
            runner.resume()
        elif len(runner_paused) == 2 and snap.elapsed >= 60:
            runner.stop()

    runner_paused: list[float] = []
    snap = await asyncio.wait_for(runner.run(trainer, on_update=rider), timeout=30)
    log.close()
    assert snap.state is RideState.RIDING and 0 < snap.s < 5000
    assert snap.elapsed == pytest.approx(60, abs=2)  # the 10 s pause does not count
    events = [r["command"] for r in csv.DictReader(_skip_header(tmp_path / "ride.csv"))
              if r["kind"] == "event"]  # fmt: skip
    assert events == ["trainer connected", "start", "paused", "resumed", "stop"]


async def test_runner_changes_difficulty_during_ride(make_course, tmp_path):
    course = make_course(length=5000.0, grade=0.08)
    clock = FastClock()
    session = session_for(course)
    log = RideLog(tmp_path / "ride.csv", {"course": "x", "start_s": 0.0})
    runner = RideRunner(session, clock, log)
    trainer = MockTrainer(
        constant_power(250.0), clock.now, clock.sleep, runner.trainer_sample,
        runner.trainer_connection,
    )  # fmt: skip
    changed_at: list[float] = []

    def rider(snap) -> None:
        if snap.elapsed >= 20 and not changed_at:
            changed_at.append(clock.now())
            runner.set_difficulty(0.5)
        elif changed_at and clock.now() >= changed_at[0] + 2:
            runner.stop()

    await asyncio.wait_for(runner.run(trainer, on_update=rider), timeout=30)
    log.close()
    assert trainer.last_grade == pytest.approx(4.0)
    grades = [c.params.grade_percent for _t, c in trainer.commands]
    switch = grades.index(4.0)
    assert set(grades[:switch]) == {8.0} and set(grades[switch:]) == {4.0}
    events = [r["command"] for r in csv.DictReader(_skip_header(tmp_path / "ride.csv"))
              if r["kind"] == "event"]  # fmt: skip
    assert "difficulty 50 %" in events


def _skip_header(path):
    f = path.open(encoding="utf-8")
    f.readline()
    return f
