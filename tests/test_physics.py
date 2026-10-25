import math

import numpy as np
import pytest
from scipy.integrate import solve_ivp

from crt.physics.model import (
    MotionState,
    RiderParams,
    air_density,
    headwind,
    resistance_force,
    steady_speed,
    step,
)


def ride(course, power, seconds, params=None, state=None, dt=0.05):
    params, state = params or RiderParams(), state or MotionState()
    for _ in range(round(seconds / dt)):
        state = step(state, power, dt, course, params)
    return state


def test_air_density():
    p = RiderParams()
    assert air_density(0.0, p) == pytest.approx(1.204, abs=0.002)
    assert air_density(1000.0, p) == pytest.approx(1.204 * 0.887, rel=0.005)


def test_headwind_direction():
    p = RiderParams(wind_speed=5.0, wind_from_deg=0.0)  # wind from the north
    assert headwind(0.0, p) == pytest.approx(5.0)  # riding north: in the face
    assert headwind(180.0, p) == pytest.approx(-5.0)
    assert headwind(90.0, p) == pytest.approx(0.0, abs=1e-12)


def test_steady_speed_flat_known_value():
    # 200 W, 84 kg, CdA 0.32, Crr 0.004: about 34.4 km/h on the flat
    v = steady_speed(200.0, 0.0, 1.204, RiderParams())
    assert v * 3.6 == pytest.approx(34.4, abs=0.2)


@pytest.mark.parametrize(("power", "grade"), [(200.0, 0.0), (300.0, 0.05), (150.0, 0.08)])
def test_reaches_steady_speed(make_course, power, grade):
    course = make_course(length=20_000.0, grade=grade)
    p = RiderParams()
    state = ride(course, power, 240.0, p)
    rho = air_density(course.at(state.s).z, p)
    assert state.v == pytest.approx(steady_speed(power, grade, rho, p), rel=0.003)


def test_starts_from_standstill(make_course):
    state = ride(make_course(), 250.0, 1.0)
    assert 0.0 < state.v < 5.0
    assert state.s > 0.0


def test_no_power_on_flat_slows_down_and_never_reverses(make_course):
    course = make_course(grade=0.03)
    state = ride(course, 0.0, 120.0, state=MotionState(v=8.0))
    assert state.v == pytest.approx(0.0, abs=1e-6)
    assert state.s > 0.0


def test_coasting_downhill_reaches_terminal_speed(make_course):
    course = make_course(length=20_000.0, grade=-0.06)
    p = RiderParams(max_speed=100.0)
    state = ride(course, 0.0, 300.0, p)
    # terminal speed: gravity balances rolling and air resistance
    rho = air_density(course.at(state.s).z, p)
    speeds = np.linspace(5, 30, 25001)
    target = next(v for v in speeds if resistance_force(v, -0.06, rho, 0.0, p) >= 0)
    assert state.v == pytest.approx(target, rel=0.002)
    assert state.braking == 0.0


def test_speed_cap_brakes(make_course):
    course = make_course(length=20_000.0, grade=-0.10)
    p = RiderParams(max_speed=50 / 3.6)
    state = ride(course, 0.0, 200.0, p)
    assert state.v == pytest.approx(50 / 3.6)
    assert state.braking > 0.0


def test_energy_balance_on_climb(make_course):
    """Work = kinetic + potential energy + losses; check against an independent
    solve_ivp integration of the same equations."""
    course = make_course(length=5000.0, grade=0.04)
    p = RiderParams()
    m, me = p.mass, p.effective_mass
    rho = air_density(course.at(0).z, p)  # the grade is constant, rho varies slightly
    theta = math.atan(0.04)

    def rhs(_t, y):
        s, energy = y
        v = math.sqrt(2 * max(energy, 0.0) / me)
        rho_s = air_density(course.at(s).z, p)
        force = (
            m * 9.80665 * (math.sin(theta) + p.crr * math.cos(theta)) + 0.5 * rho_s * p.cda * v * v
        )
        return [v, 280.0 - v * force]

    ref = solve_ivp(rhs, (0, 60), [0.0, 0.0], rtol=1e-9, atol=1e-9)
    state = ride(course, 280.0, 60.0, p)
    assert state.s == pytest.approx(ref.y[0, -1], rel=1e-4)
    assert 0.5 * me * state.v**2 == pytest.approx(ref.y[1, -1], rel=1e-3)
    assert state.work == pytest.approx(280.0 * 60.0)
    assert rho > 1.0
