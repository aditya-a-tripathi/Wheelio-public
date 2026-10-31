"""Effort model: how hard a stretch of path is to cover, per mobility profile.

Effort is in "flat-meters": 1 unit = pushing 1 m on flat concrete. Coefficients are our own
estimates from the SCIRE review of wheelchair propulsion studies (see the README); the UI and
pitch must say so.
"""
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Profile:
    max_grade_pct: float  # default limit on the steepest stretch, either direction
    up: float  # effort per % of uphill grade (1 + up * g)
    down: float  # effort per % of downhill grade (braking, control)
    speed_mps: float  # flat-ground travel speed
    slow_up: float  # time multiplier per % uphill (1 + slow_up * g)


PROFILES = {
    # Push force ~3x at 12.5% (Gagnon 2014): 1 + 0.16 * 12.5 = 3.0. 8.33% = ADA ramp maximum.
    "manual_chair": Profile(8.33, up=0.16, down=0.05, speed_mps=1.0, slow_up=0.08),
    # Motor does the work: hills cost little effort, steeper limit.
    "power_chair": Profile(12.0, up=0.04, down=0.02, speed_mps=1.5, slow_up=0.02),
    # Walkers and crutches: tiring on hills, harder to control going down.
    "walker": Profile(8.33, up=0.12, down=0.08, speed_mps=0.8, slow_up=0.06),
}

SURFACE = {"Concrete": 1.0, "Pavement": 1.0, "Crosswalk": 1.0, "Brick": 1.2, "Gravel": 1.5}
LABEL = {"No bueno": 1.5}  # Dartmouth label of unknown meaning; penalize, never block
# Where the path came from. OSM sidewalks fill gaps in Dartmouth's layer, but nobody has
# checked their curb ramps, so prefer Dartmouth's mapped paths when the choice is close.
SOURCE = {"osm": 1.15}
CROSSWALK_PENALTY = 15.0  # flat-meters: curb ramp uncertainty at each crossing


def subprofile(elev: list[list[float]], a: float, b: float) -> tuple[np.ndarray, np.ndarray]:
    """Elevation samples between distances a < b along the edge (u -> v), interpolated at ends."""
    d = np.array([p[0] for p in elev])
    z = np.array([p[1] for p in elev])
    inner = (d > a) & (d < b)
    ds = np.concatenate([[a], d[inner], [b]])
    zs = np.interp(ds, d, z)
    return ds, zs


def piece_grades(ds: np.ndarray, zs: np.ndarray, forward: bool) -> tuple[np.ndarray, np.ndarray]:
    """(piece lengths, signed grade % in the direction of travel)."""
    dl = np.diff(ds)
    dz = np.diff(zs) if forward else -np.diff(zs)
    g = np.divide(100.0 * dz, dl, out=np.zeros_like(dl), where=dl > 0)
    return dl, g


def cost_and_time(edge: dict, a: float, b: float, forward: bool, p: Profile) -> tuple[float, float]:
    """Effort (flat-meters) and time (s) to travel edge distance [a, b] in a direction."""
    if b - a <= 0:
        return 0.0, 0.0
    if edge["grade_source"] == "label":
        # Lidar unusable here: assume half the stretch climbs at the label grade, half descends.
        g = edge["max_abs_grade_pct"]
        dl = np.array([(b - a) / 2, (b - a) / 2])
        grades = np.array([g, -g])
    else:
        ds, zs = subprofile(edge["elev"], a, b)
        dl, grades = piece_grades(ds, zs, forward)
    up = np.clip(grades, 0, None)
    down = np.clip(-grades, 0, None)
    factor = (1 + p.up * up + p.down * down) * SURFACE.get(edge["material"], 1.0)
    factor *= LABEL.get(edge["label"], 1.0)
    factor *= SOURCE.get(edge.get("source"), 1.0)
    effort = float((dl * factor).sum())
    if edge["material"] == "Crosswalk":
        effort += CROSSWALK_PENALTY * (b - a) / edge["length_m"]
    time_s = float((dl * (1 + p.slow_up * up)).sum() / p.speed_mps)
    return effort, time_s
