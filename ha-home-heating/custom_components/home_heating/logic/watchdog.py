"""Watchdog: keeps track of checks and decides when to send an alert.

Every check has a state: "ok", "warning" or "error", with a message. An alert is sent when the state
of a check changes, and again every `repeat_hours` while it stays warning or error (0: only on change).
"""

from __future__ import annotations

from dataclasses import dataclass, field

LEVELS = {"ok": 0, "warning": 1, "error": 2}


@dataclass
class Check:
    state: str = "ok"
    message: str = ""
    since: float = 0.0  # timestamp of the last state change
    notified: float | None = None  # timestamp of the last alert, None: no alert sent


@dataclass
class Alert:
    check: str
    state: str
    message: str
    since: float
    repeat: bool  # True: still failing, sent again


@dataclass
class Watchdog:
    checks: dict[str, Check] = field(default_factory=dict)

    def update(self, results: dict[str, tuple[str, str]], now: float, repeat_hours: float) -> list[Alert]:
        """Apply the results of one run: {check id: (state, message)}. Returns the alerts to send.

        Checks that are no longer reported (for example a disabled part) are removed, with a
        recovery alert when they were failing.
        """
        alerts = []
        for check_id, (state, message) in results.items():
            check = self.checks.get(check_id)
            if check is None:
                check = self.checks[check_id] = Check(state="ok", since=now)
            if state != check.state:
                check.state, check.message, check.since = state, message, now
                if state != "ok" or check.notified is not None:  # recovery only after a sent problem
                    alerts.append(Alert(check_id, state, message, now, False))
                    check.notified = now if state != "ok" else None
            else:
                check.message = message
                last = check.notified if check.notified is not None else check.since
                if state != "ok" and repeat_hours and now - last >= repeat_hours * 3600:
                    alerts.append(Alert(check_id, state, message, check.since, True))
                    check.notified = now
        for check_id in [c for c in self.checks if c not in results]:
            check = self.checks.pop(check_id)
            if check.state != "ok" and check.notified is not None:
                alerts.append(Alert(check_id, "ok", "check no longer active", now, False))
        return alerts

    @property
    def state(self) -> str:
        """Overall state: the worst check."""
        return max((c.state for c in self.checks.values()), key=LEVELS.get, default="ok")

    def as_dict(self) -> dict:
        return {
            check_id: {"state": c.state, "message": c.message, "since": c.since}
            for check_id, c in sorted(self.checks.items())
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> Watchdog:
        wd = cls()
        for check_id, c in (data or {}).items():
            wd.checks[check_id] = Check(c.get("state", "ok"), c.get("message", ""), c.get("since", 0.0), c.get("notified"))
        return wd

    def to_store(self) -> dict:
        return {
            check_id: {"state": c.state, "message": c.message, "since": c.since, "notified": c.notified}
            for check_id, c in self.checks.items()
        }
