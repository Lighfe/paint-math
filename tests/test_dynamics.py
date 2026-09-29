import ast
import math
import sys
from pathlib import Path

import pytest

from paint_math import dynamics
from paint_math.dynamics import list_systems, simulate

MODULE_PATH = Path(dynamics.__file__)
AREA = (0.0, 0.0, 100.0, 100.0)
CENTRE = (50.0, 50.0)


def reference_rk4_step(f, x, y, h):
    """One classical RK4 step, written out by hand, for comparison."""
    k1x, k1y = f(x, y)
    k2x, k2y = f(x + h / 2 * k1x, y + h / 2 * k1y)
    k3x, k3y = f(x + h / 2 * k2x, y + h / 2 * k2y)
    k4x, k4y = f(x + h * k3x, y + h * k3y)
    return (
        x + h / 6 * (k1x + 2 * k2x + 2 * k3x + k4x),
        y + h / 6 * (k1y + 2 * k2y + 2 * k3y + k4y),
    )


def one_step(system, rel_start, params=None, h=0.1):
    """Run simulate for one step from a point relative to the area centre.

    Returns the second point relative to the centre.
    """
    start = (CENTRE[0] + rel_start[0], CENTRE[1] + rel_start[1])
    points = simulate(system, start, AREA, h, 1, params)
    assert len(points) == 2
    return (points[1][0] - CENTRE[0], points[1][1] - CENTRE[1])


# Module and systems


def test_module_imports_only_standard_library():
    tree = ast.parse(MODULE_PATH.read_text())
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative imports are not standard library"
            names.append(node.module)
    for name in names:
        top = name.split(".")[0]
        assert top in sys.stdlib_module_names or top == "__future__", name


def test_list_systems_sorted():
    assert list_systems() == ["linear_spiral", "lotka_volterra", "van_der_pol"]


def test_linear_spiral_equations():
    def f(x, y):
        return (-0.1 * x - y, x - 0.1 * y)

    expected = reference_rk4_step(f, 3.0, -2.0, 0.1)
    got = one_step("linear_spiral", (3.0, -2.0))
    assert got == pytest.approx(expected, abs=1e-12)


def test_van_der_pol_default_mu():
    def f(x, y, mu=1.0):
        return (y, mu * (1 - x * x) * y - x)

    expected = reference_rk4_step(f, 2.0, 1.0, 0.1)
    got = one_step("van_der_pol", (2.0, 1.0))
    assert got == pytest.approx(expected, abs=1e-12)


def test_van_der_pol_custom_mu():
    def f(x, y):
        return (y, 3.0 * (1 - x * x) * y - x)

    expected = reference_rk4_step(f, 2.0, 1.0, 0.1)
    got = one_step("van_der_pol", (2.0, 1.0), {"mu": 3.0})
    assert got == pytest.approx(expected, abs=1e-12)


def lv(alpha=1.0, beta=1.0, gamma=1.0, delta=1.0):
    def f(x, y):
        return (alpha * x - beta * x * y, delta * x * y - gamma * y)

    return f


def test_lotka_volterra_defaults():
    expected = reference_rk4_step(lv(), 2.0, 0.5, 0.1)
    got = one_step("lotka_volterra", (2.0, 0.5))
    assert got == pytest.approx(expected, abs=1e-12)


def test_lotka_volterra_all_params():
    params = {"alpha": 1.5, "beta": 0.7, "gamma": 2.0, "delta": 0.3}
    expected = reference_rk4_step(lv(**params), 2.0, 0.5, 0.1)
    got = one_step("lotka_volterra", (2.0, 0.5), params)
    assert got == pytest.approx(expected, abs=1e-12)


def test_partial_params_keep_other_defaults():
    params = {"beta": 2.0}
    expected = reference_rk4_step(lv(beta=2.0), 2.0, 0.5, 0.1)
    got = one_step("lotka_volterra", (2.0, 0.5), params)
    assert got == pytest.approx(expected, abs=1e-12)


def test_params_dict_not_modified():
    params = {"beta": 2.0}
    one_step("lotka_volterra", (2.0, 0.5), params)
    assert params == {"beta": 2.0}
    params2 = {"mu": 0.5}
    one_step("van_der_pol", (2.0, 1.0), params2)
    assert params2 == {"mu": 0.5}


# simulate


def test_returns_n_steps_plus_one_float_tuples_starting_at_start():
    points = simulate("linear_spiral", (60, 50), AREA, 0.01, 50)
    assert isinstance(points, list)
    assert len(points) == 51
    assert points[0] == (60.0, 50.0)
    for p in points:
        assert isinstance(p, tuple)
        assert len(p) == 2
        assert all(type(c) is float for c in p)


def test_uses_rk4_over_many_steps():
    def f(x, y):
        return (-0.1 * x - y, x - 0.1 * y)

    x, y = 5.0, -3.0
    expected = [(x, y)]
    for _ in range(20):
        x, y = reference_rk4_step(f, x, y, 0.05)
        expected.append((x, y))
    points = simulate("linear_spiral", (55.0, 47.0), AREA, 0.05, 20)
    rel = [(px - 50.0, py - 50.0) for px, py in points]
    for got, exp in zip(rel, expected):
        assert got == pytest.approx(exp, abs=1e-9)


def test_coordinates_relative_to_area_centre_without_scaling():
    # Same relative start in two differently placed and sized areas gives the
    # same relative trajectory: no scaling to the area size.
    a1 = (0.0, 0.0, 100.0, 100.0)
    a2 = (200.0, -40.0, 600.0, 160.0)  # centre (400, 60)
    p1 = simulate("van_der_pol", (52.0, 51.0), a1, 0.1, 5)
    p2 = simulate("van_der_pol", (402.0, 61.0), a2, 0.1, 5)
    r1 = [(x - 50.0, y - 50.0) for x, y in p1]
    r2 = [(x - 400.0, y - 60.0) for x, y in p2]
    for a, b in zip(r1, r2):
        assert a == pytest.approx(b, abs=1e-9)


def test_centre_is_fixed_point_of_linear_spiral():
    points = simulate("linear_spiral", (30.0, 70.0), (10, 50, 50, 90), 0.1, 10)
    assert points[-1] == pytest.approx((30.0, 70.0))


def test_trajectory_ends_when_leaving_area():
    # Lotka-Volterra with alpha large: x grows fast, leaves a small area.
    area = (0.0, 0.0, 10.0, 10.0)  # centre (5, 5)
    points = simulate(
        "lotka_volterra", (6.0, 5.0), area, 0.1, 1000, {"alpha": 5.0, "beta": 0.0}
    )
    assert 1 < len(points) < 1001
    for x, y in points:
        assert 0.0 <= x <= 10.0 and 0.0 <= y <= 10.0
    # The next step would leave the area.
    rel = (points[-1][0] - 5.0, points[-1][1] - 5.0)
    nxt = reference_rk4_step(lv(alpha=5.0, beta=0.0), rel[0], rel[1], 0.1)
    assert not (0.0 <= nxt[0] + 5.0 <= 10.0 and 0.0 <= nxt[1] + 5.0 <= 10.0)


def test_point_on_edge_is_inside():
    # Start on the edge of the area is allowed.
    points = simulate("linear_spiral", (100.0, 50.0), AREA, 0.01, 0)
    assert points == [(100.0, 50.0)]
    points = simulate("linear_spiral", (0.0, 0.0), AREA, 0.01, 0)
    assert points == [(0.0, 0.0)]


def test_step_onto_edge_is_kept():
    # Lotka-Volterra with beta = delta = gamma = 0, alpha = 1: dy/dt = 0 and
    # x grows. Choose the area so that the first RK4 step lands exactly on
    # the right edge.
    f = lv(alpha=1.0, beta=0.0, gamma=0.0, delta=0.0)
    x1, _ = reference_rk4_step(f, 1.0, 0.0, 0.5)
    half = x1  # area centre at 0 -> right edge at x1
    area = (-half, -half, half, half)
    params = {"alpha": 1.0, "beta": 0.0, "gamma": 0.0, "delta": 0.0}
    points = simulate("lotka_volterra", (1.0, 0.0), area, 0.5, 3, params)
    assert len(points) == 2
    assert points[1][0] == x1


def test_docstring_mentions_exit_behaviour():
    doc = simulate.__doc__ or ""
    low = doc.lower()
    assert "edge" in low and "outside" in low


def test_n_steps_zero_returns_start():
    points = simulate("van_der_pol", (51, 52), AREA, 0.1, 0)
    assert points == [(51.0, 52.0)]
    assert all(type(c) is float for c in points[0])


def test_known_solution_linear_spiral():
    points = simulate("linear_spiral", (60, 50), AREA, 0.01, 1000)
    assert len(points) == 1001
    ex = 50 + 10 * math.exp(-1) * math.cos(10)
    ey = 50 + 10 * math.exp(-1) * math.sin(10)
    last = points[-1]
    assert abs(last[0] - ex) <= 1e-6
    assert abs(last[1] - ey) <= 1e-6
    assert abs(last[0] - 46.913228) < 1e-5
    assert abs(last[1] - 47.998658) < 1e-5
    d_last = math.hypot(last[0] - 50, last[1] - 50)
    d_start = math.hypot(60 - 50, 50 - 50)
    assert d_last < d_start


# Input checks


def test_unknown_system():
    with pytest.raises(ValueError) as exc:
        simulate("lorenz", (50, 50), AREA, 0.1, 10)
    msg = str(exc.value)
    assert "lorenz" in msg
    for name in list_systems():
        assert name in msg


@pytest.mark.parametrize("step", [0, -0.1, math.nan, math.inf, -math.inf])
def test_bad_step(step):
    with pytest.raises(ValueError) as exc:
        simulate("linear_spiral", (50, 50), AREA, step, 10)
    msg = str(exc.value)
    assert "step" in msg
    assert repr(step) in msg or str(step) in msg


@pytest.mark.parametrize(
    "area",
    [(0, 0, 0, 100), (0, 0, 100, 0), (10, 0, 5, 100), (0, 10, 100, 5)],
)
def test_empty_area(area):
    with pytest.raises(ValueError) as exc:
        simulate("linear_spiral", (0, 0), area, 0.1, 10)
    msg = str(exc.value)
    assert "area" in msg
    assert str(area) in msg


@pytest.mark.parametrize("n_steps", [-1, 2.5, "3", None, True])
def test_bad_n_steps(n_steps):
    with pytest.raises(ValueError) as exc:
        simulate("linear_spiral", (50, 50), AREA, 0.1, n_steps)
    msg = str(exc.value)
    assert "n_steps" in msg
    assert repr(n_steps) in msg


@pytest.mark.parametrize(
    "start",
    [(-1, 50), (50, 101), (100.0001, 50), (math.nan, 50), (50, math.inf)],
)
def test_bad_start(start):
    with pytest.raises(ValueError) as exc:
        simulate("linear_spiral", start, AREA, 0.1, 10)
    msg = str(exc.value)
    assert "start" in msg
    assert str(start) in msg


def test_unknown_param_for_linear_spiral():
    with pytest.raises(ValueError) as exc:
        simulate("linear_spiral", (50, 50), AREA, 0.1, 10, {"mu": 2})
    msg = str(exc.value)
    assert "'mu'" in msg
    assert "allowed" in msg.lower()


def test_unknown_param_lists_allowed_keys():
    with pytest.raises(ValueError) as exc:
        simulate("lotka_volterra", (51, 51), AREA, 0.1, 10, {"mu": 2})
    msg = str(exc.value)
    assert "'mu'" in msg
    for key in ("alpha", "beta", "gamma", "delta"):
        assert key in msg
