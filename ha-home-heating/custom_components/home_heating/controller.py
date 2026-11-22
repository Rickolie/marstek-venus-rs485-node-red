"""The controller: reads Home Assistant, runs the algorithms, writes to the devices.

Every `update_interval` seconds:
1. read all inputs (master + backups)
2. heat pump feedforward, ventilation boost and boiler decisions (logic/)
3. write to the devices, only what has to change, switches off first and on one second later
4. watchdog checks, alerts to MQTT and as a Home Assistant event

The weather forecast is fetched every 30 minutes, the history is recalculated daily.
State that must survive a restart (simple schedule stop, legionella, plans, watchdog) is stored.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import settings as settings_mod
from .const import (
    EVENT_ALERT,
    FORECAST_INTERVAL_MIN,
    HISTORY_INPUTS,
    INPUTS,
    SIGNAL_UPDATE,
    STORE_KEY,
    STORE_VERSION,
)
from .logic import boiler as boiler_logic
from .logic import feedforward as ff_logic
from .logic import history as history_logic
from .logic import prices as price_logic
from .logic import ventilation as vent_logic
from .logic.inputs import Reading, Source, rolling_average, select
from .logic.watchdog import Watchdog

_LOGGER = logging.getLogger(__name__)

ON, OFF = "on", "off"


@dataclass
class Action:
    """One service call to a device."""

    entity_id: str
    domain: str
    service: str
    data: dict
    description: str
    want: tuple[str, Any]  # ("state", "on") or ("value", 35.0): what the device must show afterwards
    first: bool = False  # switched off: before the others


def _domain(entity_id: str) -> str:
    return entity_id.split(".", 1)[0]


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso(ts: float | None) -> str | None:
    return None if ts is None else dt_util.as_local(datetime.fromtimestamp(ts, UTC)).isoformat()


class Controller:
    """Runs Home Heating for one config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.s = settings_mod.merged(entry.options)
        self._store: Store = Store(hass, STORE_VERSION, STORE_KEY)
        self._lock = asyncio.Lock()
        self._unsubs: list = []
        self._schedule_unsubs: list = []

        self.ff_state = ff_logic.FeedforwardState()
        self.boiler_state = boiler_logic.BoilerState()
        self.vent_active = False
        self.vent_previous: Any = None  # fan percentage before the boost
        self.price_plan: dict | None = None
        self.watchdog = Watchdog()
        self.history: dict | None = None
        self.history_error: str | None = None
        self.history_running = False
        self.forecast: list[tuple[float, float]] | None = None
        self.forecast_fetched: float | None = None
        self.forecast_error: str | None = None
        self.outside_samples: deque[tuple[float, float]] = deque()
        self.commands: dict[str, dict] = {}  # entity -> command that has to be followed
        self.command_failures: dict[str, str] = {}
        self.element_check: tuple[float, float] | None = None  # (since, boiler temp) while the element heats
        self.boiler_heating_problem: str | None = None
        self.actions_log: deque[dict] = deque(maxlen=30)
        self.dry_run_last: dict[str, str] = {}  # entity -> last logged dry-run action, logged once
        self.run_error: str | None = None
        self.data: dict = {}

    # ----------------------------------------------------------------------------- life cycle

    async def async_start(self) -> None:
        await self._async_restore()
        self._schedule()

    async def async_started(self) -> None:
        """Home Assistant is running: fill the 24 h average and get forecast and history."""
        await self._async_prefill_outside()
        await self.async_refresh_forecast()
        await self.async_tick()
        if self.history is None:
            self.hass.async_create_background_task(self.async_recalculate_history(), "home_heating history")

    async def async_stop(self) -> None:
        for unsub in self._unsubs + self._schedule_unsubs:
            unsub()
        self._unsubs.clear()
        self._schedule_unsubs.clear()
        await self._store.async_save(self._store_data())

    def _schedule(self) -> None:
        for unsub in self._schedule_unsubs:
            unsub()
        hour, minute = (int(p) for p in self.s["history_time"].split(":"))
        self._schedule_unsubs = [
            async_track_time_interval(self.hass, self._async_interval, timedelta(seconds=self.s["update_interval"])),
            async_track_time_interval(self.hass, self._async_forecast_interval, timedelta(minutes=FORECAST_INTERVAL_MIN)),
            async_track_time_change(self.hass, self._async_history_time, hour=hour, minute=minute, second=0),
        ]

    async def async_apply_settings(self) -> None:
        """Settings changed on the sidebar page or with a switch."""
        self.s = settings_mod.merged(self.entry.options)
        self._schedule()
        await self.async_tick()

    async def _async_interval(self, _now=None) -> None:
        await self.async_tick()

    async def _async_forecast_interval(self, _now=None) -> None:
        await self.async_refresh_forecast()

    async def _async_history_time(self, _now=None) -> None:
        await self.async_recalculate_history()

    # ----------------------------------------------------------------------------- store

    async def _async_restore(self) -> None:
        data = await self._store.async_load() or {}
        self.ff_state = ff_logic.FeedforwardState(**data.get("feedforward", {}))
        self.boiler_state = boiler_logic.BoilerState(**data.get("boiler", {}))
        self.vent_active = data.get("vent_active", False)
        self.vent_previous = data.get("vent_previous")
        self.price_plan = data.get("price_plan")
        self.watchdog = Watchdog.from_dict(data.get("watchdog"))
        self.history = data.get("history")
        forecast = data.get("forecast") or {}
        if forecast.get("points"):
            self.forecast = [tuple(p) for p in forecast["points"]]
            self.forecast_fetched = forecast.get("fetched")

    def _store_data(self) -> dict:
        history = {k: v for k, v in (self.history or {}).items() if k != "days"} if self.history else None
        return {
            "feedforward": asdict(self.ff_state),
            "boiler": asdict(self.boiler_state),
            "vent_active": self.vent_active,
            "vent_previous": self.vent_previous,
            "price_plan": self.price_plan,
            "watchdog": self.watchdog.to_store(),
            "history": history,
            "forecast": {"points": self.forecast, "fetched": self.forecast_fetched} if self.forecast else None,
        }

    # ----------------------------------------------------------------------------- reading

    def _read(self, key: str, now: float) -> Reading:
        numeric = INPUTS[key][2]
        sources = []
        for entity_id in self.s.get(key) or []:
            st = self.hass.states.get(entity_id)
            if st is None:
                sources.append(Source(entity_id, None))
                continue
            reported = getattr(st, "last_reported", None) or st.last_updated
            sources.append(Source(entity_id, st.state, now - reported.timestamp()))
        deviation = self.s["input_max_deviation"] if INPUTS[key][1] == "°C" else 0
        return select(sources, numeric, self.s["input_max_age_minutes"] * 60, deviation)

    async def _async_prefill_outside(self) -> None:
        """The 24 h average from the recorder, so it is right directly after a restart."""
        entity_ids = (self.s["in_outside_temp"] or [])[:1]
        if not entity_ids:
            return
        start = dt_util.utcnow() - timedelta(hours=25)
        try:
            states = await self._async_recorder(
                lambda: self._recorder_states(start, entity_ids).get(entity_ids[0], [])
            )
        except Exception as err:  # noqa: BLE001 - the average fills itself in 24 h
            _LOGGER.warning("Could not read the outside temperature history: %s", err)
            return
        for t, value in states:
            value = _float(value)
            if value is not None:
                self.outside_samples.append((t, value))

    # ----------------------------------------------------------------------------- recorder

    async def _async_recorder(self, job):
        if "recorder" not in self.hass.config.components:
            raise RuntimeError("the recorder is not running")
        from homeassistant.components.recorder import get_instance  # noqa: PLC0415

        return await get_instance(self.hass).async_add_executor_job(job)

    def _recorder_states(self, start: datetime, entity_ids: list[str]) -> dict[str, list[tuple[float, str]]]:
        """State changes since start: {entity: [(timestamp, state)]}. Runs in the recorder thread."""
        from homeassistant.components.recorder import history as rec_history  # noqa: PLC0415

        raw = rec_history.get_significant_states(
            self.hass, start, None, entity_ids, None,
            include_start_time_state=True, significant_changes_only=False,
            minimal_response=True, no_attributes=True,
        )
        result = {}
        for entity_id, rows in raw.items():
            points = []
            for row in rows:
                if isinstance(row, State):
                    points.append((row.last_changed.timestamp(), row.state))
                else:
                    changed = row.get("last_changed")
                    ts = dt_util.parse_datetime(changed).timestamp() if isinstance(changed, str) else float(changed)
                    points.append((ts, row.get("state")))
            result[entity_id] = points
        return result

    def _recorder_statistics(self, start: datetime, entity_ids: list[str]) -> dict:
        from homeassistant.components.recorder import statistics as rec_stats  # noqa: PLC0415

        rows = rec_stats.statistics_during_period(
            self.hass, start, None, set(entity_ids), "hour", None, {"mean", "min", "max"}
        )
        return {
            entity_id: [
                {"start": r["start"] if isinstance(r["start"], (int, float)) else r["start"].timestamp(),
                 "mean": r.get("mean"), "min": r.get("min"), "max": r.get("max")}
                for r in data
            ]
            for entity_id, data in rows.items()
        }

    async def async_recalculate_history(self) -> None:
        """Daily averages from all history (state history + long-term statistics)."""
        if self.history_running:
            return
        self.history_running = True
        try:
            keys = {key: (self.s[setting] or [None])[0] for key, setting in HISTORY_INPUTS.items()}
            entity_ids = [e for e in keys.values() if e]
            if not keys["outside"] or not keys["power"]:
                self.history_error = "outside temperature or heat pump power not configured"
                return
            start = datetime(2000, 1, 1, tzinfo=UTC)
            states, statistics = await self._async_recorder(
                lambda: (self._recorder_states(start, entity_ids), self._recorder_statistics(start, entity_ids))
            )
            history = {key: states.get(e, []) for key, e in keys.items() if e}
            stats = {key: statistics.get(e, []) for key, e in keys.items() if e}
            tz = dt_util.get_time_zone(self.hass.config.time_zone)
            result = await self.hass.async_add_executor_job(
                history_logic.calculate, history, stats, dt_util.utcnow().timestamp(), tz, self.s
            )
            if result is None:
                self.history_error = "no days found on which the heat pump has been used"
            else:
                self.history, self.history_error = result, None
        except RuntimeError as err:
            self.history_error = str(err)
        except Exception as err:  # noqa: BLE001 - reported by the watchdog
            _LOGGER.exception("History calculation failed")
            self.history_error = f"calculation failed: {err}"
        finally:
            self.history_running = False
        await self.async_tick()

    # ----------------------------------------------------------------------------- forecast

    async def async_refresh_forecast(self) -> None:
        entity_id = self.s["weather_entity"]
        if not entity_id:
            self.forecast_error = "no weather entity"
            return
        try:
            response = await self.hass.services.async_call(
                "weather", "get_forecasts", {"entity_id": entity_id, "type": "hourly"},
                blocking=True, return_response=True,
            )
            items = (response or {}).get(entity_id, {}).get("forecast") or []
            points = []
            for item in items:
                t = dt_util.parse_datetime(str(item.get("datetime")))
                temp = _float(item.get("temperature"))
                if t is not None and temp is not None:
                    points.append((t.timestamp(), temp))
            if not points:
                raise ValueError("empty forecast")
            self.forecast = sorted(points)
            self.forecast_fetched = dt_util.utcnow().timestamp()
            self.forecast_error = None
        except Exception as err:  # noqa: BLE001 - reported by the watchdog, current temperature is used
            self.forecast_error = str(err) or type(err).__name__
            _LOGGER.debug("Forecast not available: %s", err)

    # ----------------------------------------------------------------------------- run

    async def async_tick(self) -> None:
        if self._lock.locked():
            return
        async with self._lock:
            try:
                await self._async_run()
                self.run_error = None
            except Exception as err:  # noqa: BLE001 - keep running, reported by the watchdog
                _LOGGER.exception("Home Heating run failed")
                self.run_error = f"{type(err).__name__}: {err}"
                await self._async_watchdog({}, dt_util.utcnow().timestamp())
            async_dispatcher_send(self.hass, SIGNAL_UPDATE)
            self._store.async_delay_save(self._store_data, 30)

    async def _async_run(self) -> None:
        s = self.s
        now = dt_util.now()
        ts = now.timestamp()

        # 1. Inputs
        readings = {key: self._read(key, ts) for key in INPUTS}
        outside = readings["in_outside_temp"].value
        inside = readings["in_inside_temp"].value
        if outside is not None:
            self.outside_samples.append((ts, outside))
        while self.outside_samples and self.outside_samples[0][0] < ts - 25 * 3600:
            self.outside_samples.popleft()
        outside_avg = rolling_average(list(self.outside_samples), ts, 24)

        # 2a. Heat pump feedforward (also calculated when control is off, to compare)
        ff = ff_logic.calculate(
            ff_logic.FeedforwardInput(
                now=now, outside=outside, outside_avg_24h=outside_avg, inside=inside,
                forecast=self.forecast, forecast_fetched=self.forecast_fetched,
                history_fit=(self.history or {}).get("fit"),
            ),
            s, self.ff_state,
        )

        # 2b. Ventilation boost
        if s["vent_enabled"]:
            vent_active, vent_reason = vent_logic.boost(outside, inside, s, self.vent_active)
        else:
            vent_active, vent_reason = False, "disabled"
        vent_changed = vent_active != self.vent_active

        # 2c. Boiler
        holiday = readings["in_holiday"].value in ("on", "true", "home_away", "True")
        if s["price_enabled"]:
            self._plan_prices(now, readings)
        boiler_logic.legionella_due(now, s, self.boiler_state)
        element_state = self._state(s["element_switch"])
        boiler = boiler_logic.decide(
            boiler_logic.BoilerInput(
                temp=readings["in_boiler_temp"].value, cv_temp=readings["in_cv_temp"].value,
                solar_next_hour=readings["in_solar_next_hour"].value, holiday=holiday,
                element_on=None if element_state is None else element_state == ON,
                in_price_slot=s["price_enabled"] and price_logic.in_plan(self.price_plan, ts),
            ),
            s, self.boiler_state,
        )

        # 3. Outputs
        actions: list[Action] = []
        if s["hp_enabled"] and ff.available:
            actions += self._heat_pump_actions(ff)
        if s["vent_entity"] and vent_changed:
            actions += self._vent_actions(vent_active)  # also back to normal when the boost is disabled
        if s["boiler_enabled"] and boiler.available:
            actions += self._boiler_actions(boiler)
        await self._async_execute(actions, ts)
        self.vent_active = vent_active

        # 4. Watchdog
        self.data = {
            "updated": now.isoformat(),
            "outputs_enabled": s["outputs_enabled"],
            "inputs": {key: asdict(r) for key, r in readings.items()},
            "outside_avg_24h": None if outside_avg is None else round(outside_avg, 1),
            "feedforward": asdict(ff),
            "ventilation": {"active": vent_active, "reason": vent_reason},
            "boiler": {**asdict(boiler), "legionella": self.boiler_state.legionella, "holiday": holiday,
                       "price_plan": self._plan_view()},
            "history": {k: v for k, v in (self.history or {}).items() if k != "days"} or None,
            "history_error": self.history_error,
            "forecast": {"points": len(self.forecast or []), "fetched": _iso(self.forecast_fetched),
                         "error": self.forecast_error},
            "actions": list(self.actions_log),
        }
        await self._async_watchdog(readings, ts)
        await self._async_publish_status()

    # ----------------------------------------------------------------------------- price plan

    def _plan_prices(self, now: datetime, readings: dict[str, Reading], force: bool = False) -> None:
        s = self.s
        hour, minute = (int(p) for p in s["price_plan_time"].split(":"))
        today = now.date().isoformat()
        due = (now.hour, now.minute) >= (hour, minute)
        if not force and (not due or (self.price_plan or {}).get("date") == today):
            return
        items = []
        st = self.hass.states.get(s["price_entity"]) if s["price_entity"] else None
        for attribute in s["price_attribute"].split(","):
            value = st.attributes.get(attribute.strip()) if st else None
            if isinstance(value, list):
                items += value
        slots = price_logic.parse(items, s["price_time_key"], s["price_value_key"], s["price_factor"])
        remaining = readings["in_solar_remaining_today"].value
        self.price_plan = price_logic.plan_day(now, s, slots, remaining)
        self.price_plan["prices"] = len(slots)

    def _plan_view(self) -> dict | None:
        if not self.price_plan:
            return None
        return {
            "date": self.price_plan["date"],
            "reason": self.price_plan["reason"],
            "slots": [[_iso(a), _iso(b), round(p, 4)] for a, b, p in self.price_plan["slots"]],
        }

    # ----------------------------------------------------------------------------- actions

    def _state(self, entity_id: str) -> str | None:
        st = self.hass.states.get(entity_id) if entity_id else None
        return None if st is None else st.state

    def _switch(self, entity_id: str, on: bool, label: str) -> list[Action]:
        state = self._state(entity_id)
        if not entity_id or state is None or state in ("unavailable", "unknown"):
            return []
        want = ON if on else OFF
        if state == want:
            return []
        return [Action(entity_id, _domain(entity_id), f"turn_{want}", {}, f"{label} {want}", ("state", want), not on)]

    def _set_number(self, entity_id: str, value: float, label: str, min_change: float) -> list[Action]:
        st = self.hass.states.get(entity_id) if entity_id else None
        if st is None or st.state in ("unavailable", "unknown"):
            return []
        domain = _domain(entity_id)
        if domain in ("climate", "water_heater"):
            current = _float(st.attributes.get("temperature"))
            low, high = _float(st.attributes.get("min_temp")), _float(st.attributes.get("max_temp"))
            service, key = "set_temperature", "temperature"
        else:
            current = _float(st.state)
            low, high = _float(st.attributes.get("min")), _float(st.attributes.get("max"))
            service, key = "set_value", "value"
        if low is not None:
            value = max(value, low)
        if high is not None:
            value = min(value, high)
        if current is not None and abs(current - value) < min_change:
            return []
        return [Action(entity_id, domain, service, {key: value}, f"{label} {value:g}", ("value", value))]

    def _select(self, entity_id: str, option: str, label: str) -> list[Action]:
        st = self.hass.states.get(entity_id) if entity_id else None
        if st is None or st.state in ("unavailable", "unknown") or not option:
            return []
        if _domain(entity_id) == "climate":
            if st.state == option:
                return []
            return [Action(entity_id, "climate", "set_hvac_mode", {"hvac_mode": option}, f"{label} {option}", ("state", option))]
        if st.state == option:
            return []
        return [Action(entity_id, _domain(entity_id), "select_option", {"option": option}, f"{label} {option}", ("state", option))]

    def _heat_pump_actions(self, ff: ff_logic.FeedforwardResult) -> list[Action]:
        s = self.s
        actions = []
        running = ff.demand != "off"
        if s["out_heating_switch"]:
            actions += self._switch(s["out_heating_switch"], running, "Heat pump")
        if s["out_mode_entity"]:
            mode = {"heat": s["mode_heat"], "cool": s["mode_cool"], "off": s["mode_off"]}[ff.demand]
            actions += self._select(s["out_mode_entity"], mode, "Heat pump mode")
        if s["out_water_temp_entity"] and running and ff.water_temp is not None:
            actions += self._set_number(s["out_water_temp_entity"], ff.water_temp, "Water temperature", s["out_min_change"])
        return actions

    def _vent_actions(self, boost: bool) -> list[Action]:
        s = self.s
        entity_id = s["vent_entity"]
        st = self.hass.states.get(entity_id)
        if st is None:
            return []
        domain = _domain(entity_id)
        value = s["vent_boost_value"] if boost else s["vent_normal_value"]
        if domain == "fan":
            if boost:
                self.vent_previous = st.attributes.get("percentage")
            elif not value:
                value = self.vent_previous
            percentage = _float(value)
            if percentage is None:
                return []
            return [Action(entity_id, "fan", "set_percentage", {"percentage": int(percentage)},
                           f"Ventilation {int(percentage)} %", ("attr_percentage", int(percentage)))]
        if domain in ("select", "input_select"):
            return self._select(entity_id, str(value or ""), "Ventilation")
        return self._switch(entity_id, boost, "Ventilation")

    def _boiler_actions(self, b: boiler_logic.BoilerResult) -> list[Action]:
        s = self.s
        actions = self._switch(s["element_switch"], b.element, "Boiler element")
        actions += self._switch(s["instant_switch"], b.instant, "Doorstroomboiler")
        if s["valve_entity"] and b.valve_setpoint is not None:
            actions += self._set_number(s["valve_entity"], b.valve_setpoint, "Valve", 0.1)
        return actions

    async def _async_execute(self, actions: list[Action], ts: float) -> None:
        """Switch off first, the rest one second later. Only log when not writing to devices."""
        dry_run = not self.s["outputs_enabled"]
        if not dry_run:
            self.dry_run_last.clear()
        else:
            wanted = {a.entity_id for a in actions}
            for entity_id in [e for e in self.dry_run_last if e not in wanted]:
                del self.dry_run_last[entity_id]  # no longer needed: log again when it comes back
            actions = [a for a in actions if self.dry_run_last.get(a.entity_id) != a.description]
            self.dry_run_last.update({a.entity_id: a.description for a in actions})
        if not actions:
            return
        first = [a for a in actions if a.first]
        rest = [a for a in actions if not a.first]
        for group in (first, rest):
            if group is rest and first and rest and not dry_run:
                await self._pause()
            for a in group:
                self.actions_log.appendleft({"time": _iso(ts), "action": a.description, "dry_run": dry_run})
                if dry_run:
                    continue
                try:
                    await self.hass.services.async_call(
                        a.domain, a.service, {"entity_id": a.entity_id, **a.data}, blocking=True
                    )
                    self.command_failures.pop(a.entity_id, None)
                    previous = self.commands.get(a.entity_id)
                    since = previous["at"] if previous and previous["want"] == a.want else ts  # repeated: keep the first time
                    self.commands[a.entity_id] = {"want": a.want, "at": since, "description": a.description}
                except Exception as err:  # noqa: BLE001 - reported by the watchdog
                    _LOGGER.warning("%s failed: %s", a.description, err)
                    self.command_failures[a.entity_id] = f"{a.description} failed: {err}"

    async def _pause(self) -> None:
        """One second between switching off and switching on."""
        await asyncio.sleep(1)

    def _follows(self, entity_id: str, want: tuple[str, Any]) -> bool:
        st = self.hass.states.get(entity_id)
        if st is None:
            return False
        kind, value = want
        if kind == "state":
            return st.state == value
        if kind == "attr_percentage":
            return _float(st.attributes.get("percentage")) == value
        current = _float(st.attributes.get("temperature")) if _domain(entity_id) in ("climate", "water_heater") else _float(st.state)
        return current is not None and abs(current - value) < 0.15

    # ----------------------------------------------------------------------------- watchdog

    def _parts(self) -> set[str]:
        s = self.s
        parts = {"history"}
        if s["hp_enabled"]:
            parts.add("hp")
        if s["vent_enabled"]:
            parts.add("vent")
        if s["boiler_enabled"]:
            parts.add("boiler")
            if s["price_enabled"]:
                parts.add("price")
        return parts

    def _checks(self, readings: dict[str, Reading], ts: float) -> dict[str, tuple[str, str]]:
        s = self.s
        parts = self._parts()
        checks: dict[str, tuple[str, str]] = {}

        if self.run_error:
            checks["run"] = ("error", f"calculation failed: {self.run_error}")
            return checks
        checks["run"] = ("ok", "")

        # Inputs: unavailable, backup in use, stale, deviating
        for key, reading in readings.items():
            label, _unit, _numeric, needed_by = INPUTS[key]
            needed = parts.intersection(needed_by)
            if not needed:
                continue
            only_history = needed == {"history"}
            if reading.status == "not configured":
                if key not in ("in_holiday", "in_solar_remaining_today"):
                    checks[f"input_{key}"] = ("warning", f"{label}: not configured")
            elif reading.status == "unavailable":
                checks[f"input_{key}"] = ("warning" if only_history else "error",
                                          f"{label} unavailable: {'; '.join(reading.problems)}")
            elif reading.problems:
                used = f" (using backup {reading.source})" if reading.status == "backup" else ""
                checks[f"input_{key}"] = ("warning", f"{label}: {'; '.join(reading.problems)}{used}")
            else:
                checks[f"input_{key}"] = ("ok", "")
        if "hp" in parts and readings["in_inside_temp"].status == "not configured" and s["simple_enabled"]:
            checks["input_in_inside_temp"] = ("warning", "Inside temperature not configured: the simple day "
                                              "schedule cannot stop for the day")

        # Devices that should be controlled
        devices = []
        if "hp" in parts:
            devices += [s["out_water_temp_entity"], s["out_heating_switch"], s["out_mode_entity"]]
        if "vent" in parts:
            devices += [s["vent_entity"]]
        if "boiler" in parts:
            devices += [s["element_switch"], s["valve_entity"]] + ([s["instant_switch"]] if s["use_instant"] else [])
        for entity_id in filter(None, devices):
            st = self.hass.states.get(entity_id)
            if st is None:
                checks[f"device_{entity_id}"] = ("error", f"{entity_id} does not exist")
            elif st.state in ("unavailable", "unknown"):
                checks[f"device_{entity_id}"] = ("error", f"{entity_id} is {st.state}")
            elif _domain(entity_id) == "climate" and entity_id == s["valve_entity"] and st.state == "heat/cool":
                checks[f"device_{entity_id}"] = ("warning", f"{entity_id} is heat/cool: the valve thermostat lost its own control")
            else:
                checks[f"device_{entity_id}"] = ("ok", "")

        # Commands that were not followed
        verify = s["output_verify_minutes"] * 60
        for entity_id, command in list(self.commands.items()):
            if self._follows(entity_id, command["want"]):
                del self.commands[entity_id]
            elif ts - command["at"] > verify:
                checks[f"command_{entity_id}"] = ("error", f"{command['description']}: the device did not follow "
                                                  f"within {s['output_verify_minutes']} min")
        for entity_id, message in self.command_failures.items():
            checks[f"command_{entity_id}"] = ("error", message)
        for entity_id in self.commands:
            checks.setdefault(f"command_{entity_id}", ("ok", ""))

        # The element heats, but the boiler does not get warmer
        if "boiler" in parts and s["boiler_heating_check_minutes"]:
            temp = readings["in_boiler_temp"].value
            minutes = s["boiler_heating_check_minutes"]
            if self._state(s["element_switch"]) == ON and temp is not None:
                if self.element_check is None:
                    self.element_check = (ts, temp)
                elif ts - self.element_check[0] >= minutes * 60:
                    rise = temp - self.element_check[1]
                    self.boiler_heating_problem = (
                        f"Boiler element on for {minutes:g} min, temperature rose only {rise:.1f} °C"
                        if rise < s["boiler_min_rise"] else None
                    )
                    self.element_check = (ts, temp)  # next period
            else:
                self.element_check = self.boiler_heating_problem = None
            checks["boiler_heating"] = ("warning", self.boiler_heating_problem) if self.boiler_heating_problem else ("ok", "")

        # Forecast, history and prices
        if "hp" in parts and (s["outside_source"] == "Forecast" or s["cooling_enabled"]):
            if not ff_logic.forecast_recent(
                ff_logic.FeedforwardInput(now=dt_util.now(), outside=None, outside_avg_24h=None, inside=None,
                                          forecast=self.forecast, forecast_fetched=self.forecast_fetched),
                s["forecast_max_age_hours"],
            ):
                checks["forecast"] = ("warning", f"No recent weather forecast ({self.forecast_error or 'not fetched'}): "
                                      "using the current outside temperature")
            else:
                checks["forecast"] = ("ok", "")
        if self.history_error:
            level = "warning" if s["curve_source"] == "History" and "hp" in parts else "ok"
            checks["history"] = (level, f"History: {self.history_error}" if level != "ok" else "")
        else:
            checks["history"] = ("ok", "")
        if "price" in parts:
            plan = self.price_plan or {}
            if plan and not plan.get("prices"):
                checks["prices"] = ("warning", f"No prices found in {s['price_entity']}.{s['price_attribute']}")
            else:
                checks["prices"] = ("ok", "")
        return checks

    async def _async_watchdog(self, readings: dict[str, Reading], ts: float) -> None:
        checks = self._checks(readings, ts) if readings or self.run_error else {"run": ("ok", "")}
        alerts = self.watchdog.update(checks, ts, self.s["alert_repeat_hours"])
        self.data["watchdog"] = {"state": self.watchdog.state, "checks": self.watchdog.as_dict()}
        for alert in alerts:
            payload = {
                "check": alert.check,
                "state": alert.state,
                "message": alert.message,
                "since": _iso(alert.since),
                "repeat": alert.repeat,
                "title": f"Home Heating {alert.state}: {alert.message}" if alert.state != "ok"
                else f"Home Heating ok again: {alert.check}",
            }
            self.hass.bus.async_fire(EVENT_ALERT, payload)
            await self._async_mqtt("alert", payload, retain=False)
            if alert.state != "ok":
                _LOGGER.warning("%s", payload["title"])

    # ----------------------------------------------------------------------------- MQTT

    async def _async_mqtt(self, topic: str, payload: dict, retain: bool) -> None:
        if not self.s["mqtt_enabled"] or "mqtt" not in self.hass.config.components:
            return
        from homeassistant.components import mqtt  # noqa: PLC0415

        try:
            await mqtt.async_publish(
                self.hass, f"{self.s['mqtt_base_topic'].rstrip('/')}/{topic}",
                json.dumps(payload, default=str), 0, retain,
            )
        except Exception as err:  # noqa: BLE001 - MQTT down must not stop the control
            _LOGGER.debug("MQTT publish failed: %s", err)

    async def _async_publish_status(self) -> None:
        d = self.data
        ff, boiler = d["feedforward"], d["boiler"]
        await self._async_mqtt("status", {
            "updated": d["updated"],
            "outputs_enabled": d["outputs_enabled"],
            "watchdog": d["watchdog"]["state"],
            "heat_pump": {"mode": ff["mode"], "demand": ff["demand"], "water_temp": ff["water_temp"],
                          "outside_temp_used": ff["outside_temp_used"]},
            "ventilation_boost": d["ventilation"]["active"],
            "boiler": {"element": boiler["element"], "element_reason": boiler["element_reason"],
                       "instant": boiler["instant"], "valve_setpoint": boiler["valve_setpoint"],
                       "legionella": boiler["legionella"]},
        }, retain=True)
        await self._async_mqtt("watchdog", d["watchdog"], retain=True)
        await self._async_mqtt("heartbeat", {"time": d["updated"]}, retain=False)

    # ----------------------------------------------------------------------------- services

    async def async_start_legionella(self) -> None:
        self.boiler_state.legionella = True
        await self.async_tick()

    async def async_plan_prices(self) -> None:
        now = dt_util.now()
        self._plan_prices(now, {"in_solar_remaining_today": self._read("in_solar_remaining_today", now.timestamp())}, force=True)
        await self.async_tick()

    @callback
    def status(self) -> dict:
        return self.data
