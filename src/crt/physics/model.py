"""Rider physics along the course coordinate *s*.

The kinetic energy is integrated rather than the speed, which avoids the P/v
singularity when starting from a standstill:

    E = ½ m_eff v²
    dE/dt = η P − v [m g sin θ + Crr m g cos θ + ½ ρ CdA (v − w)|v − w|]
    ds/dt = v

θ is the true terrain angle (never the clamped grade sent to the trainer), and w the
wind component along the direction of travel. The rider cannot roll backwards: E is
kept ≥ 0. A speed cap acts as virtual braking.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np

from crt.route.course import Course

G = 9.80665
R_DRY_AIR = 287.05  # J/(kg K)


@dataclass(frozen=True)
class RiderParams:
    rider_mass: float = 75.0  # kg
    bike_mass: float = 9.0  # kg
    cda: float = 0.32  # m², hoods ≈ 0.32, drops ≈ 0.28
    crr: float = 0.004  # good road tyres on asphalt
    drivetrain_efficiency: float = 1.0  # 1.0: the trainer measures at the hub/flywheel
    wheel_inertia: float = 0.14  # kg m², both wheels
    wheel_radius: float = 0.335  # m
    temperature_c: float = 20.0
    sea_level_pressure: float = 101_325.0  # Pa
    wind_speed: float = 0.0  # m/s
    wind_from_deg: float = 0.0  # direction the wind blows from, degrees from north
    max_speed: float = 60 / 3.6  # m/s, virtual braking above this

    @property
    def mass(self) -> float:
        return self.rider_mass + self.bike_mass

    @property
    def effective_mass(self) -> float:
        """Mass including the rotational inertia of the wheels."""
        return self.mass + self.wheel_inertia / self.wheel_radius**2


def air_density(elevation: float, params: RiderParams) -> float:
    """Density from the standard-atmosphere pressure at the elevation and the actual
    air temperature."""
    pressure = params.sea_level_pressure * (1 - 2.25577e-5 * elevation) ** 5.25588
    return pressure / (R_DRY_AIR * (params.temperature_c + 273.15))


def headwind(bearing_deg: float, params: RiderParams) -> float:
    """Wind component against the direction of travel [m/s]; negative is tailwind."""
    if params.wind_speed == 0.0:
        return 0.0
    return params.wind_speed * math.cos(math.radians(params.wind_from_deg - bearing_deg))


def resistance_force(v: float, grade: float, rho: float, wind: float, p: RiderParams) -> float:
    """Total force opposing motion at speed ``v`` [N]. ``wind`` is the headwind."""
    theta = math.atan(grade)
    air = v + wind  # airspeed
    return (
        p.mass * G * math.sin(theta)
        + p.crr * p.mass * G * math.cos(theta)
        + 0.5 * rho * p.cda * air * abs(air)
    )


@dataclass(frozen=True)
class MotionState:
    s: float = 0.0  # distance along the course [m]
    v: float = 0.0  # speed [m/s]
    work: float = 0.0  # energy delivered by the rider [J]
    braking: float = 0.0  # energy removed by the speed cap [J]


def step(
    state: MotionState, power: float, dt: float, course: Course, p: RiderParams
) -> MotionState:
    """Advance by ``dt`` seconds at constant rider power (classic RK4 on s and E)."""
    m = p.effective_mass
    pw = p.drivetrain_efficiency * max(power, 0.0)

    if state.v < 1e-6:
        # At a standstill the energy equation has no gravity term (v·F = 0), so on a
        # descent nothing would ever start the motion. Take one explicit step in
        # velocity form when gravity overcomes rolling resistance.
        here = course.at(state.s)
        f0 = resistance_force(0.0, here.grade, air_density(here.z, p), headwind(here.bearing, p), p)
        if f0 < 0:
            v1 = math.sqrt((-f0 / m * dt) ** 2 + 2 * pw * dt / m)
            return replace(
                state, s=state.s + v1 * dt / 2, v=v1, work=state.work + max(power, 0.0) * dt
            )

    def derivatives(s: float, energy: float) -> tuple[float, float]:
        v = math.sqrt(2 * max(energy, 0.0) / m)
        point = course.at(s)
        rho = air_density(point.z, p)
        force = resistance_force(v, point.grade, rho, headwind(point.bearing, p), p)
        return v, pw - v * force

    e0 = 0.5 * m * state.v**2
    k1 = derivatives(state.s, e0)
    k2 = derivatives(state.s + dt / 2 * k1[0], e0 + dt / 2 * k1[1])
    k3 = derivatives(state.s + dt / 2 * k2[0], e0 + dt / 2 * k2[1])
    k4 = derivatives(state.s + dt * k3[0], e0 + dt * k3[1])
    s = state.s + dt / 6 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
    energy = max(e0 + dt / 6 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1]), 0.0)

    braking = state.braking
    e_cap = 0.5 * m * p.max_speed**2
    if energy > e_cap:
        braking += energy - e_cap
        energy = e_cap
    return replace(
        state,
        s=max(s, state.s),  # no rolling backwards
        v=math.sqrt(2 * energy / m),
        work=state.work + max(power, 0.0) * dt,
        braking=braking,
    )


def steady_speed(power: float, grade: float, rho: float, p: RiderParams) -> float:
    """Speed at which the power balances the resistance (no wind), for checks and tests."""
    theta = math.atan(grade)
    a = 0.5 * rho * p.cda
    b = p.mass * G * (math.sin(theta) + p.crr * math.cos(theta))
    roots = np.roots([a, 0.0, b, -p.drivetrain_efficiency * power])
    real = [r.real for r in roots if abs(r.imag) < 1e-9 and r.real > 0]
    return min(real) if real else 0.0
