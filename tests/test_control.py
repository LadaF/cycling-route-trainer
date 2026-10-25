import numpy as np
import pytest

from crt.control.policy import ControlSettings, GradeCommand, GradePolicy, LevelCommand, LevelPolicy
from crt.physics.model import RiderParams
from crt.route.course import Course


def stepped_course() -> Course:
    """Flat for 500 m, then 8 % up to 1000 m, then 6 % down to 1500 m."""
    s = np.arange(0.0, 1500.1, 2.0)
    grade = np.where(s < 500, 0.0, np.where(s < 1000, 0.08, -0.06))
    z = 200 + np.concatenate([[0.0], np.cumsum(grade[:-1] * 2.0)])
    n = len(s)
    return Course(
        "steps", s, np.full(n, 50.0), np.full(n, 14.0), z, grade, np.zeros(n), z,
        np.full(n, np.nan), {},
    )  # fmt: skip


def policy(**settings) -> GradePolicy:
    return GradePolicy(stepped_course(), ControlSettings(**settings), RiderParams())


def test_follows_terrain_with_minimum_on_descent():
    p = policy(min_grade=1.5)
    assert p.target_grade(100.0, 0.0) == 1.5  # flat → minimum
    assert p.target_grade(700.0, 0.0) == pytest.approx(8.0)
    assert p.target_grade(1200.0, 0.0) == 1.5  # descent → minimum


def test_negative_minimum_lets_descents_through():
    p = policy(min_grade=-2.0)
    assert p.target_grade(1200.0, 0.0) == -2.0
    assert policy(min_grade=-50.0).target_grade(1200.0, 0.0) == pytest.approx(-6.0)


def test_difficulty_and_limits():
    assert policy(difficulty=0.5).target_grade(700.0, 0.0) == pytest.approx(4.0)
    assert policy(max_grade=5.0).target_grade(700.0, 0.0) == 5.0


def test_lookahead_averages_the_grade_ahead():
    p = policy(lookahead_s=2.0)
    # at 480 m and 10 m/s: 20 m ahead, the first 20 m of the climb start at 500 m
    assert p.target_grade(490.0, 10.0) == pytest.approx(4.0, abs=0.2)


def test_rate_limit_threshold_and_keepalive():
    p = policy(max_rate_hz=2.0, min_change=0.1, keepalive_s=5.0)
    first = p.decide(0.0, 700.0, 0.0)
    assert isinstance(first, GradeCommand) and first.params.grade_percent == pytest.approx(8.0)
    assert first.params.crr == 0.004
    assert first.params.cw_kg_per_m == pytest.approx(0.19, abs=0.01)  # ½ ρ CdA
    assert p.decide(0.3, 1200.0, 0.0) is None  # too soon after the last command
    assert p.decide(0.6, 1200.0, 0.0) is not None  # changed and allowed
    assert p.decide(2.0, 1200.0, 0.0) is None  # no change
    assert p.decide(6.0, 1200.0, 0.0) is not None  # keep-alive
    p.reset()
    assert p.decide(6.1, 1200.0, 0.0) is not None  # after a reconnect


def test_level_policy_maps_and_clamps():
    p = LevelPolicy(
        stepped_course(),
        ControlSettings(level_at_flat=2.0, level_per_percent=0.5, min_level=1.0, max_level=4.0),
        RiderParams(),
    )
    assert p.decide(0.0, 100.0, 0.0) == LevelCommand(2.0)
    assert p.decide(1.0, 700.0, 0.0) == LevelCommand(4.0)  # 2 + 0.5·8 = 6 → max 4
