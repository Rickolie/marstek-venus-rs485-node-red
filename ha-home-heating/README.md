# Home Heating

Home Assistant integration that controls a heat pump, an electric boiler with optional doorstroomboiler (instant water heater) and ventilation, with watchdogs that report problems over MQTT. All settings are on a **Home Heating** page in the sidebar.

It replaces a set of Node-RED flows (heat pump history, feedforward and boiler) with the same algorithms, now as plain Python with tests.

## What it does

**Heat pump feedforward**: the water temperature from the outside temperature.
- Outside temperature: the measured temperature, or the weather forecast (lead time, average window, weight, optional bias correction). Falls back to the measurement when the forecast is older than a set age.
- Heating curve: your own lookup table (linear between the points), or the straight line calculated from the history.
- Simple day schedule for mild weather: when the average outside temperature is above a set value, heat at a fixed water temperature from a start time until the inside temperature is reached, then off for the rest of the day.
- Cooling: when the outside temperature *will be* above a set value (forecast), cool along a cooling curve (default 19 °C water at 22 °C outside to 22 °C at 40 °C), never below the minimum cooling water temperature.
- Outputs (all optional): the water temperature (number, input_number, climate or water_heater), an on/off switch and the heat / cool / off mode (climate hvac mode or select option).

**Boiler**
- Element: the weekly legionella run (also without sun), solar (expected solar power next hour above a threshold, not on holiday), or the cheapest prices of the day when not enough solar is expected. Heats up to the target, restarts only below the target minus a hysteresis.
- Doorstroomboiler: optional. On below a set boiler temperature, unless the element heats. Never both on; switching off always happens one second before switching on.
- Heat exchanger valve: open when the CV water is warmer than the boiler, closed otherwise.

**Ventilation boost**: maximum ventilation when it is warmer than the set value outside (default 10 °C), but cooler outside than inside, and warm inside (default 22 °C). Back to normal afterwards (for a fan: the speed it had before).

**History**: daily averages of outside temperature, water target temperature, power, energy and COP (COP 0 excluded), only for days on which the heat pump was used. Uses the exact state history plus the long-term statistics, so all history is used. Gives the History heating curve, the expected power and COP per outside temperature, and a table per °C.

**Redundant inputs**: every input is a list of entities. The first is the master; when it is unavailable (or stale) the next one takes over. The watchdog warns when a backup is in use or deviates from the master.

**Watchdogs**: sensors unavailable or deviating, devices unavailable, a device that does not follow a command within a set time, the boiler element on without the boiler getting warmer, no recent forecast, history or price problems, and errors in the calculation itself. Each problem is sent once when it starts, again every set number of hours while it lasts, and once when it is solved.

## Installation

1. HACS → three dots → **Custom repositories** → add `https://github.com/Rickolie/ha-home-heating` as type *Integration*.
2. Install **Home Heating** and restart Home Assistant.
3. Settings → Devices & services → **Add integration** → *Home Heating*.
4. Open **Home Heating** in the sidebar and check the **Inputs** tab: the defaults are the entities of the original Node-RED setup.

Without HACS: copy `custom_components/home_heating` to `/config/custom_components/` and restart.

## Safe switch-over from Node-RED

Nothing is switched or written until **Write to devices** is on (General tab, or `switch.home_heating_write_to_devices`). Until then the integration only calculates, and the Status tab shows what it *would* do ("dry run" in Recent actions).

1. Install, set the inputs, leave **Write to devices** off.
2. Turn on the parts you want to move (Heat pump control, Boiler control, …) and compare the Status tab with what Node-RED does.
3. Disable the matching Node-RED flow, then turn on **Write to devices**.

The parts are independent, so you can move the heat pump first and the boiler later.

## MQTT topics

Published through the MQTT integration of Home Assistant (base topic configurable, default `home_heating`):

| Topic | Retained | Content |
|---|---|---|
| `home_heating/alert` | no | `{check, state, message, since, repeat, title}`, on every change of a check and repeated while it fails |
| `home_heating/watchdog` | yes | overall state and all checks |
| `home_heating/status` | yes | main decisions (heat pump, ventilation, boiler) |
| `home_heating/heartbeat` | no | every run, to detect that Home Assistant or the integration stopped |

The same alerts are fired as the Home Assistant event `home_heating_alert`.

`examples/node-red-alerts.json` is a Node-RED flow that forwards the alerts to Telegram, and warns when the heartbeat stops for 5 minutes. Set your chat id in its *Settings* node; for Signal, replace the Telegram sender by your Signal node.

## Entities

- Sensors: feedforward water temperature, outside temperature used, heat pump mode, boiler status, boiler price plan, history (days used, fit, curve), history water slope / intercept, watchdog, and the value used for every input (with its source: master or backup).
- Binary sensors: heat pump heating / cooling, ventilation boost, boiler element / doorstroomboiler should heat, legionella run, problem.
- Switches: write to devices, heat pump control, cooling, ventilation boost, boiler control, use doorstroomboiler, legionella, boiler on cheapest prices.
- Services: `home_heating.recalculate_history`, `refresh_forecast`, `start_legionella`, `plan_prices`.

## Changing the algorithm

The algorithms are in `custom_components/home_heating/logic/`, without Home Assistant imports:

| File | What |
|---|---|
| `feedforward.py` | outside temperature, heating curve, simple day schedule, cooling |
| `boiler.py` | element, doorstroomboiler, valve, legionella |
| `ventilation.py` | ventilation boost |
| `prices.py` | price list and cheapest hours |
| `history.py` | daily averages and fit lines |
| `inputs.py` | master / backup selection, 24 h average |
| `watchdog.py` | when to send an alert |

All settings, their defaults and the sidebar page are defined in `settings.py`: a new setting only needs to be added there.

Run the tests:

```bash
pip install -r requirements_test.txt
pytest
```

`tests/logic/` tests the algorithms (including a comparison with the original Node-RED history flow), `tests/integration/` runs the integration in a real Home Assistant core.

Release a new version: raise `version` in `manifest.json` and `VERSION` in `__init__.py`, push, and create a GitHub release. HACS then offers the update.
