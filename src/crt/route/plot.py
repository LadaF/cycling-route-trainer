"""Elevation and grade profile plot of a processed course (matplotlib)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from crt.route.course import Course


def plot_course(
    course: Course, extra: dict[str, np.ndarray] | None = None, path: Path | None = None
) -> None:
    """Show the profile in a window, or save it to ``path`` (format from its suffix).

    ``extra`` maps labels to additional elevation profiles on the course grid.
    """
    import matplotlib

    if path is not None:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    km = course.s / 1000
    fig, (ax_z, ax_g) = plt.subplots(
        2, 1, sharex=True, figsize=(12, 7), height_ratios=[2, 1], layout="constrained"
    )
    if not np.isnan(course.z_file).all():
        ax_z.plot(km, course.z_file, lw=0.8, color="0.55", label="route file")
    ax_z.plot(km, course.z_raw, lw=0.6, alpha=0.8, zorder=3, label="DEM, raw")
    for label, z in (extra or {}).items():
        ax_z.plot(km, z, lw=0.8, label=label)
    ax_z.plot(km, course.z, lw=1.6, color="black", label="final (smoothed)")
    for i, st in enumerate(course.meta.get("structures", [])):
        ax_z.axvspan(
            st["s0"] / 1000,
            st["s1"] / 1000,
            color="tab:red",
            alpha=0.15,
            label="bridge / tunnel" if i == 0 else None,
        )
    ax_z.set_ylabel("elevation [m]")
    ax_z.set_title(
        f"{course.name}: {course.length / 1000:.2f} km, ascent {course.total_ascent:.0f} m"
    )
    ax_z.legend(loc="best", fontsize="small")
    ax_z.grid(alpha=0.3)

    ax_g.plot(km, course.grade * 100, lw=0.8, color="black")
    ax_g.axhline(0, color="0.5", lw=0.6)
    ax_g.set_ylabel("grade [%]")
    ax_g.set_xlabel("distance [km]")
    ax_g.grid(alpha=0.3)

    if path is None:
        plt.show()
    else:
        fig.savefig(path, dpi=150)
        plt.close(fig)
