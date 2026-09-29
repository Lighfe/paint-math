"""Simulate 2D dynamical systems inside a rectangular canvas area.

A system is ``dx/dt = f(x, y)``, ``dy/dt = g(x, y)``. It runs in coordinates
relative to the centre of the area (no scaling) and is integrated with
classical fourth-order Runge-Kutta (RK4) with a fixed step.
"""

import math
import numbers


def _linear_spiral(x, y, p):
    return (-0.1 * x - y, x - 0.1 * y)


def _lotka_volterra(x, y, p):
    return (
        p["alpha"] * x - p["beta"] * x * y,
        p["delta"] * x * y - p["gamma"] * y,
    )


def _van_der_pol(x, y, p):
    return (y, p["mu"] * (1 - x * x) * y - x)


# name -> (right-hand side, default parameters)
_SYSTEMS = {
    "linear_spiral": (_linear_spiral, {}),
    "lotka_volterra": (
        _lotka_volterra,
        {"alpha": 1.0, "beta": 1.0, "gamma": 1.0, "delta": 1.0},
    ),
    "van_der_pol": (_van_der_pol, {"mu": 1.0}),
}


def list_systems():
    """Return the names of the known systems, sorted by name."""
    return sorted(_SYSTEMS)


def _is_finite_number(value):
    return (
        isinstance(value, numbers.Real)
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _rk4_step(f, x, y, h, p):
    k1x, k1y = f(x, y, p)
    k2x, k2y = f(x + h / 2 * k1x, y + h / 2 * k1y, p)
    k3x, k3y = f(x + h / 2 * k2x, y + h / 2 * k2y, p)
    k4x, k4y = f(x + h * k3x, y + h * k3y, p)
    return (
        x + h / 6 * (k1x + 2 * k2x + 2 * k3x + k4x),
        y + h / 6 * (k1y + 2 * k2y + 2 * k3y + k4y),
    )


def simulate(system, start, area, step, n_steps, params=None):
    """Integrate a named system inside ``area`` and return its trajectory.

    Arguments:
        system: a name from ``list_systems()``.
        start: ``(x, y)`` start point in canvas coordinates, inside the area.
        area: ``(x_min, y_min, x_max, y_max)`` in canvas coordinates.
        step: fixed RK4 time step, finite and > 0.
        n_steps: number of steps, an integer >= 0.
        params: optional dict that overrides some of the system's parameters;
            the others keep their defaults. The dict is not modified.

    The system runs in coordinates relative to the centre of the area, without
    scaling. The returned points are ``(x, y)`` tuples of floats in canvas
    coordinates; the first point is ``start``.

    The area is closed: a point on its edge counts as inside. When a new point
    lies outside the area, the trajectory ends there: that point is not
    returned, the list is shorter than ``n_steps + 1``, and no error is
    raised. Otherwise the list has ``n_steps + 1`` points.

    Raises ValueError for an unknown system, a bad step, an empty area, a bad
    n_steps, a start outside the area or not finite, or an unknown parameter.
    """
    if system not in _SYSTEMS:
        raise ValueError(
            f"unknown system {system!r}; known systems: {', '.join(list_systems())}"
        )
    f, defaults = _SYSTEMS[system]

    if not _is_finite_number(step) or step <= 0:
        raise ValueError(f"step must be a finite number > 0, got step={step!r}")

    try:
        x_min, y_min, x_max, y_max = area
    except (TypeError, ValueError):
        raise ValueError(
            f"area must be (x_min, y_min, x_max, y_max), got area={area!r}"
        ) from None
    if not all(_is_finite_number(v) for v in area):
        raise ValueError(f"area must have finite numbers, got area={area!r}")
    if x_max <= x_min or y_max <= y_min:
        raise ValueError(
            f"area is empty (needs x_max > x_min and y_max > y_min), got area={area!r}"
        )

    if (
        not isinstance(n_steps, numbers.Integral)
        or isinstance(n_steps, bool)
        or n_steps < 0
    ):
        raise ValueError(
            f"n_steps must be an integer >= 0, got n_steps={n_steps!r}"
        )

    try:
        sx, sy = start
    except (TypeError, ValueError):
        raise ValueError(f"start must be (x, y), got start={start!r}") from None
    if not (_is_finite_number(sx) and _is_finite_number(sy)):
        raise ValueError(f"start must have finite coordinates, got start={start!r}")
    sx, sy = float(sx), float(sy)

    def inside(x, y):
        return x_min <= x <= x_max and y_min <= y <= y_max

    if not inside(sx, sy):
        raise ValueError(f"start is outside the area {area!r}, got start={start!r}")

    allowed = sorted(defaults)
    p = dict(defaults)
    for key, value in (params or {}).items():
        if key not in defaults:
            raise ValueError(
                f"unknown parameter {key!r} for system {system!r}; "
                f"allowed keys: {allowed if allowed else 'none'}"
            )
        p[key] = float(value)

    cx = (x_min + x_max) / 2
    cy = (y_min + y_max) / 2
    x, y = sx - cx, sy - cy
    points = [(sx, sy)]
    for _ in range(n_steps):
        x, y = _rk4_step(f, x, y, step, p)
        px, py = float(x + cx), float(y + cy)
        if not inside(px, py):
            break
        points.append((px, py))
    return points
