import numpy as np
import pytest

from crt.route.course import Course


def straight_course(length: float = 2000.0, grade: float = 0.0, spacing: float = 2.0) -> Course:
    """A straight course heading north with a constant grade (a fraction)."""
    s = np.arange(0.0, length + spacing / 2, spacing)
    z = 200.0 + grade * s
    n = len(s)
    return Course(
        "straight", s, 50.0 + s / 111_250.0, np.full(n, 14.0), z, np.full(n, grade),
        np.zeros(n), z.copy(), np.full(n, np.nan), {},
    )  # fmt: skip


@pytest.fixture
def make_course():
    return straight_course
