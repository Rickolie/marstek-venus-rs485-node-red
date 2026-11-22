"""Redundant inputs: a list of entities per input, the first one is the master.

The master is used while it is valid. When it is not, the first valid backup takes over.
Problems (unavailable, stale, deviating from the master) are reported for the watchdog.
"""

from __future__ import annotations

from dataclasses import dataclass, field

INVALID_STATES = {"unknown", "unavailable", "none", ""}


@dataclass
class Source:
    """One entity of an input, as read from Home Assistant."""

    entity_id: str
    state: str | None  # None: entity does not exist
    age_s: float = 0.0  # seconds since the entity last reported


@dataclass
class Reading:
    """The selected value of an input."""

    value: float | str | None
    source: str | None  # entity id that delivered the value
    status: str  # "master", "backup", "unavailable" or "not configured"
    problems: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.value is not None


def _number(state: str | None) -> float | None:
    if state is None or state.strip().lower() in INVALID_STATES:
        return None
    try:
        value = float(state)
    except ValueError:
        return None
    return value if value == value and abs(value) != float("inf") else None  # no NaN / inf


def _text(state: str | None) -> str | None:
    if state is None or state.strip().lower() in INVALID_STATES:
        return None
    return state


def select(
    sources: list[Source],
    numeric: bool = True,
    max_age_s: float = 0,
    max_deviation: float = 0,
) -> Reading:
    """Select the value of an input from its master and backups.

    max_age_s: a source that did not report for longer is invalid (0: no age check).
    max_deviation: report a valid backup that differs more than this from a valid master (0: no check).
    """
    if not sources:
        return Reading(None, None, "not configured")

    parse = _number if numeric else _text
    problems: list[str] = []
    values: list[tuple[Source, float | str]] = []
    for source in sources:
        value = parse(source.state)
        if source.state is None:
            problems.append(f"{source.entity_id} does not exist")
        elif value is None:
            problems.append(f"{source.entity_id} is {source.state}")
        elif max_age_s and source.age_s > max_age_s:
            problems.append(f"{source.entity_id} did not report for {source.age_s / 60:.0f} min")
        else:
            values.append((source, value))

    if not values:
        return Reading(None, None, "unavailable", problems)

    chosen, value = values[0]
    status = "master" if chosen is sources[0] else "backup"

    if numeric and max_deviation and status == "master":
        for source, other in values[1:]:
            if abs(other - value) > max_deviation:
                problems.append(
                    f"{source.entity_id} differs {abs(other - value):.1f} from master {chosen.entity_id}"
                )

    return Reading(value, chosen.entity_id, status, problems)


def rolling_average(samples: list[tuple[float, float]], now: float, hours: float) -> float | None:
    """Time-weighted average over the last `hours`. Each sample is valid until the next one.

    Returns None when the samples cover less than half of the period.
    """
    start = now - hours * 3600
    points = sorted(samples)
    total = covered = 0.0
    for i, (t, value) in enumerate(points):
        end = points[i + 1][0] if i + 1 < len(points) else now
        a, b = max(t, start), min(end, now)
        if b > a:
            total += value * (b - a)
            covered += b - a
    if covered < hours * 3600 / 2:
        return None
    return total / covered
