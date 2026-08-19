# Surf / Marine / Weather API Research

**Project:** Smart Surf School Management System
**Stack:** Python 3.11 · Django 5 · DRF · HTMX · Alpine.js · Tailwind · SQLite (dev) / PostgreSQL (prod) · Windows 11 native (no Docker)
**Date of research:** 2026-08-15
**Reference spot:** Alaçatı / Çeşme, Turkey — `lat 38.28, lon 26.37`

All endpoints in this document were **called live** during research on 2026-08-15 against the Alaçatı coordinates. Every parameter name listed under Open-Meteo was confirmed to return a non-error response with that exact spelling. Anything not verified is explicitly marked `UNVERIFIED`.

---

## 0. TL;DR — The Decision

| Concern | Decision |
|---|---|
| **Default provider (no key, works out of the box)** | **Open-Meteo** — Forecast API + Marine API + Air Quality API |
| **Tide source (default)** | **Open-Meteo Marine `sea_level_height_msl`** (tide-inclusive sea level, no key) |
| **License-clean commercial land-weather fallback** | **MET Norway (Yr) Locationforecast 2.0** — free, no key, CC BY 4.0, commercial use permitted |
| **Paid upgrade path (waves, commercial SaaS)** | Open-Meteo API Standard plan, **or** self-hosted Open-Meteo (AGPLv3) |
| **Premium tide, if ever needed** | WorldTides v3 (cheap, pay-per-credit) |
| **Do not build on** | Surfline (no public API, ToS risk), Windy (non-commercial trial only), OpenWeatherMap (no marine data at all) |

**The single most important caveat, stated up front:** Open-Meteo's *free* API tier is **non-commercial only**. The *data* is CC BY 4.0 (commercial use fine), but the *free hosted service* is not. See §2.7 — this shapes the whole architecture and is why the provider layer must be pluggable from day one.

---

## 1. Required Field Coverage Matrix

Our 15 required fields against each candidate. `Y` = native field, `~` = derivable/partial, `N` = absent.

| Field | Open-Meteo | Stormglass | WorldTides | NOAA CO-OPS | NDBC | Surfline | Windy | OpenWeatherMap | Visual Crossing | met.no |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| Wave height | **Y** | Y | N | N | Y | Y | Y | N | ~ (Corporate tier) | ~ (Nordic only) |
| Swell height | **Y** | Y | N | N | ~ | Y | Y | N | ~ (Corporate tier) | N |
| Swell period | **Y** | Y | N | N | Y | Y | Y | N | ~ (Corporate tier) | N |
| Swell direction | **Y** | Y | N | N | Y | Y | Y | N | ~ | N |
| Wind speed | **Y** | Y | N | Y | Y | Y | Y | Y | Y | Y |
| Wind direction | **Y** | Y | N | Y | Y | Y | Y | Y | Y | Y |
| Tide | **~ (model)** | Y | **Y** | Y (US) | N | Y | N | N | N | ~ (Norway only) |
| Air temperature | **Y** | Y | N | Y | Y | Y | Y | Y | Y | Y |
| Water temperature | **Y** | Y | N | Y (US) | Y | Y | N | N | N | N |
| Weather condition | **Y** (WMO code) | ~ | N | N | N | Y | ~ | Y | Y | Y (symbol_code) |
| UV index | **Y** | Y | N | N | N | N | N | Y (One Call) | Y | Y |
| Precipitation | **Y** | Y | N | N | N | N | Y | Y | Y | Y |
| Visibility | **Y** | Y | N | Y (US) | Y | N | N | Y | Y | N |
| Sunrise | **Y** | ~ (astronomy ep) | N | N | N | Y | N | Y | Y | ~ (Sunrise 3.0 ep) |
| Sunset | **Y** | ~ (astronomy ep) | N | N | N | Y | N | Y | Y | ~ (Sunrise 3.0 ep) |
| **Coverage score** | **15/15** | 14/15 | 1/15 | 6/15 | 7/15 | 12/15 | 8/15 | 9/15 | 12/15 | 8/15 |

**Only Open-Meteo covers all 15 fields with no API key.** Stormglass matches it on coverage but caps the free tier at 10 requests/day.

### RECOMMENDATION
Build the field contract (a `SurfConditions` dataclass with exactly these 15 fields, all `Optional`) around Open-Meteo's coverage, because it is the only provider that can populate every one of them. Every other provider becomes a partial implementation that returns `None` for fields it lacks — the merge layer (§9.4) fills the gaps.

---

## 2. Open-Meteo — THE DEFAULT PROVIDER

### 2.1 Endpoints (all verified live)

| API | Base URL | Key? |
|---|---|:--:|
| Forecast | `https://api.open-meteo.com/v1/forecast` | No |
| Marine | `https://marine-api.open-meteo.com/v1/marine` | No |
| Air Quality | `https://air-quality-api.open-meteo.com/v1/air-quality` | No |
| Geocoding | `https://geocoding-api.open-meteo.com/v1/search` | No |
| Elevation | `https://api.open-meteo.com/v1/elevation` | No |
| Historical (ERA5) | `https://archive-api.open-meteo.com/v1/archive` | No |

Commercial (paid) customers use `https://customer-api.open-meteo.com/v1/...` and `https://customer-marine-api.open-meteo.com/v1/marine` with `&apikey=...`. **This matters for our design:** the base URL changes when a key is present, so the provider must compute its base URL from config, not hardcode it.

### 2.2 Marine API — EXACT parameter names (verified)

Confirmed by live call. These are the precise spellings:

**Hourly / Current variables:**
```
wave_height                     wave_direction                  wave_period
wave_peak_period
wind_wave_height                wind_wave_direction             wind_wave_period
wind_wave_peak_period
swell_wave_height               swell_wave_direction            swell_wave_period
swell_wave_peak_period
secondary_swell_wave_height     secondary_swell_wave_direction  secondary_swell_wave_period
tertiary_swell_wave_height      tertiary_swell_wave_direction   tertiary_swell_wave_period
sea_surface_temperature         sea_level_height_msl
ocean_current_velocity          ocean_current_direction
invert_barometer_height
```

**Daily variables:**
```
wave_height_max                 wave_direction_dominant         wave_period_max
wind_wave_height_max            wind_wave_direction_dominant    wind_wave_period_max
wind_wave_peak_period_max
swell_wave_height_max           swell_wave_direction_dominant   swell_wave_period_max
swell_wave_peak_period_max
```

Note the naming rule: the swell fields are prefixed `swell_wave_`, **not** `swell_`. Writing `swell_height` will 400. The user's guess of `swell_wave_height` / `swell_wave_period` / `swell_wave_direction` was **correct**.

**Units returned:** `wave_height` m · `swell_wave_period` s · `swell_wave_direction` ° · `sea_surface_temperature` °C · `sea_level_height_msl` m · `ocean_current_velocity` km/h.

**Other useful marine params:** `forecast_days` (0–8, default 5), `past_days`, `timezone=auto`, `length_unit=metric|imperial`, `cell_selection=sea|land|nearest`, `models=` (see §2.6).

`cell_selection=sea` is verified working and is **important for us** — Alaçatı is a coastal point and without it the grid cell picker can land on a land cell and return nulls.

### 2.3 Forecast API — EXACT parameter names (verified)

Relevant to our 15 fields:

| Our field | Open-Meteo parameter | Block | Unit |
|---|---|---|---|
| Air temperature | `temperature_2m` | current/hourly | °C |
| Feels-like | `apparent_temperature` | current/hourly | °C |
| Weather condition | `weather_code` | current/hourly/daily | WMO code (int) |
| Wind speed | `wind_speed_10m` | current/hourly | configurable |
| Wind direction | `wind_direction_10m` | current/hourly | ° |
| Wind gusts | `wind_gusts_10m` | current/hourly | configurable |
| Precipitation | `precipitation` | current/hourly | mm |
| Precip. probability | `precipitation_probability` | hourly | % |
| Visibility | `visibility` | current/hourly | **m** |
| UV index | `uv_index` | current/hourly | unitless |
| Humidity | `relative_humidity_2m` | current/hourly | % |
| Pressure | `pressure_msl` | current/hourly | hPa |
| Day/night | `is_day` | current/hourly | 1/0 |
| Cloud | `cloud_cover` | current/hourly | % |
| **Sunrise** | `sunrise` | **daily only** | ISO8601 |
| **Sunset** | `sunset` | **daily only** | ISO8601 |
| Daylight length | `daylight_duration` | daily | **seconds** |
| UV max | `uv_index_max` | daily | unitless |

**How sunrise/sunset come back — verified:**
- They exist **only** in the `daily` block. There is no `current.sunrise`.
- Format is a **naive local-time ISO8601 string with no offset and no seconds**: `"2026-08-15T06:29"`, `"2026-08-15T20:08"`.
- They are local to the timezone resolved by `timezone=auto` (here `Europe/Istanbul`, `utc_offset_seconds: 10800`).
- **Parsing trap:** `datetime.fromisoformat("2026-08-15T06:29")` yields a *naive* datetime. Django with `USE_TZ=True` will warn/misbehave. You must localize using the `timezone` field from the response, or add `utc_offset_seconds`. Do **not** assume UTC.
- Alternative: pass `timeformat=unixtime` and every timestamp (including sunrise/sunset) becomes a Unix epoch int in UTC. **This is cleaner for our storage layer** — see the recommendation below.

`visibility` is in **metres** (Alaçatı returned `42940.0`). Divide by 1000 for km in the UI.

**Unit override params:** `temperature_unit=celsius|fahrenheit`, `wind_speed_unit=kmh|ms|mph|kn`, `precipitation_unit=mm|inch`, `timeformat=iso8601|unixtime`.

For a surf/windsurf school, **`wind_speed_unit=kn` (knots)** is the correct default — it is the lingua franca of windsurfing and kitesurfing in Alaçatı. Verified: returns `"wind_speed_10m": "kn"` in `current_units`.

### 2.4 Concrete verified request URLs for Alaçatı

**MARINE** — returned HTTP 200 with all keys present:

```
https://marine-api.open-meteo.com/v1/marine
  ?latitude=38.28
  &longitude=26.37
  &current=wave_height,wave_direction,wave_period,swell_wave_height,swell_wave_direction,swell_wave_period,wind_wave_height,sea_surface_temperature,sea_level_height_msl
  &hourly=wave_height,wave_direction,wave_period,wave_peak_period,wind_wave_height,wind_wave_direction,wind_wave_period,swell_wave_height,swell_wave_direction,swell_wave_period,swell_wave_peak_period,sea_surface_temperature,sea_level_height_msl,ocean_current_velocity,ocean_current_direction
  &daily=wave_height_max,wave_direction_dominant,wave_period_max,swell_wave_height_max,swell_wave_direction_dominant,swell_wave_period_max
  &timezone=auto
  &forecast_days=7
  &length_unit=metric
  &cell_selection=sea
```

Single line, copy-pasteable:

```
https://marine-api.open-meteo.com/v1/marine?latitude=38.28&longitude=26.37&current=wave_height,wave_direction,wave_period,swell_wave_height,swell_wave_direction,swell_wave_period,wind_wave_height,sea_surface_temperature,sea_level_height_msl&hourly=wave_height,wave_direction,wave_period,wave_peak_period,wind_wave_height,wind_wave_direction,wind_wave_period,swell_wave_height,swell_wave_direction,swell_wave_period,swell_wave_peak_period,sea_surface_temperature,sea_level_height_msl,ocean_current_velocity,ocean_current_direction&daily=wave_height_max,wave_direction_dominant,wave_period_max,swell_wave_height_max,swell_wave_direction_dominant,swell_wave_period_max&timezone=auto&forecast_days=7&length_unit=metric&cell_selection=sea
```

Actual live response sample (2026-08-15T15:15, Europe/Istanbul):

```json
{
  "latitude": 38.208336, "longitude": 26.375015,
  "timezone": "Europe/Istanbul", "utc_offset_seconds": 10800,
  "current_units": { "wave_height": "m", "swell_wave_period": "s",
                     "swell_wave_direction": "°", "sea_surface_temperature": "°C",
                     "sea_level_height_msl": "m" },
  "current": {
    "time": "2026-08-15T15:15", "interval": 900,
    "wave_height": 0.98, "wave_direction": 350, "wave_period": 4.10,
    "swell_wave_height": 0.16, "swell_wave_direction": 300, "swell_wave_period": 5.40,
    "wind_wave_height": 0.94,
    "sea_surface_temperature": 22.5,
    "sea_level_height_msl": -0.39
  }
}
```

Note `latitude` snaps to `38.208336` — the model grid cell centre, ~8 km from the requested point. Expected and acceptable for a 0.08° marine grid, but worth surfacing in an admin debug view so staff understand why the number is "the bay", not "the exact beach".

**FORECAST** — returned HTTP 200 with all keys present:

```
https://api.open-meteo.com/v1/forecast
  ?latitude=38.28
  &longitude=26.37
  &current=temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,weather_code,cloud_cover,pressure_msl,visibility,wind_speed_10m,wind_direction_10m,wind_gusts_10m,uv_index
  &hourly=temperature_2m,precipitation_probability,precipitation,weather_code,visibility,wind_speed_10m,wind_direction_10m,wind_gusts_10m,uv_index,is_day
  &daily=weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,daylight_duration,uv_index_max,precipitation_sum,precipitation_probability_max,wind_speed_10m_max,wind_gusts_10m_max,wind_direction_10m_dominant
  &timezone=auto
  &forecast_days=7
  &wind_speed_unit=kn
  &temperature_unit=celsius
  &precipitation_unit=mm
```

Single line:

```
https://api.open-meteo.com/v1/forecast?latitude=38.28&longitude=26.37&current=temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,weather_code,cloud_cover,pressure_msl,visibility,wind_speed_10m,wind_direction_10m,wind_gusts_10m,uv_index&hourly=temperature_2m,precipitation_probability,precipitation,weather_code,visibility,wind_speed_10m,wind_direction_10m,wind_gusts_10m,uv_index,is_day&daily=weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,daylight_duration,uv_index_max,precipitation_sum,precipitation_probability_max,wind_speed_10m_max,wind_gusts_10m_max,wind_direction_10m_dominant&timezone=auto&forecast_days=7&wind_speed_unit=kn&temperature_unit=celsius&precipitation_unit=mm
```

Live response sample:

```json
{
  "timezone": "Europe/Istanbul", "utc_offset_seconds": 10800, "elevation": 17.0,
  "current_units": { "temperature_2m": "°C", "wind_speed_10m": "kn",
                     "visibility": "m", "uv_index": "", "weather_code": "wmo code" },
  "current": {
    "time": "2026-08-15T15:00",
    "temperature_2m": 29.8, "apparent_temperature": 28.0,
    "relative_humidity_2m": 39, "is_day": 1,
    "weather_code": 2, "cloud_cover": 25,
    "precipitation": 0.0, "visibility": 42940.0, "uv_index": 6.35,
    "wind_speed_10m": 14.3, "wind_direction_10m": 354, "wind_gusts_10m": 25.1
  },
  "daily_units": { "sunrise": "iso8601", "sunset": "iso8601",
                   "daylight_duration": "s", "uv_index_max": "" },
  "daily": {
    "time": ["2026-08-15", "..."],
    "sunrise": ["2026-08-15T06:29", "..."],
    "sunset":  ["2026-08-15T20:08", "..."],
    "uv_index_max": [7.35, "..."]
  }
}
```

**AIR QUALITY** (optional third call — only if you want pollen/AQI; `uv_index` is already in the Forecast API so this call is normally *not needed*):

```
https://air-quality-api.open-meteo.com/v1/air-quality?latitude=38.28&longitude=26.37&current=uv_index,uv_index_clear_sky,pm10,pm2_5,european_aqi&timezone=auto
```

Verified live: `uv_index: 5.90`, `european_aqi: 41`, `pm2_5: 6.4 μg/m³`.

Note the UV values **differ** between the Forecast API (`6.35`) and Air Quality API (`5.90`) for the same instant — they come from different models. Pick one source and stay consistent; do not average them.

### 2.5 Tides in Open-Meteo — what you actually get

Open-Meteo has **no dedicated tide API** and **no high/low tide extremes endpoint**. What it has is the marine variable **`sea_level_height_msl`** — sea surface height relative to mean sea level, in metres, hourly.

Verified live for Alaçatı (2026-08-15, first 6 hours): `[-0.50, -0.48, -0.45, -0.42, -0.40, -0.39]` — **real numbers, not nulls**.

Open-Meteo's own documentation caveat, quoted:

> "Tides and ocean currents are computed at 0.08° resolution using numerical models. Accuracy at coastal areas is limited. This is not suitable for coastal navigation."

Assessment for our use case:
- **Good enough.** Alaçatı is Aegean/Mediterranean, where the tidal range is roughly 20–40 cm. Tide is *not* a material factor in scheduling a windsurf or SUP lesson there — wind is. The tide display is informational.
- **Not good enough** if the product is ever sold to a school on the Atlantic coast (Portugal, France, Morocco, UK) where a 3–4 m range genuinely governs which sandbank is surfable and when. There, a 0.08° model without harmonic station data will be visibly wrong on timing.
- High/low **extremes** must be derived by us: find local minima/maxima of the hourly `sea_level_height_msl` series. Hourly resolution gives ±30 min accuracy on turn times, which is acceptable for a schedule board but not for navigation.

### 2.6 Models, reliability, resolution

30+ models integrated. Marine models available: MeteoFrance Wave, MeteoFrance Ocean Currents, DWD EWAM, DWD GWAM, ECMWF WAM, ECMWF WAM 0.25, GFS Wave 0.25°, GFS Wave 0.16°, ERA5-Ocean. Forecast models include ECMWF, DWD ICON (down to ~1 km regionally), GFS, MeteoFrance AROME.

- Typical single-location response: **under 10 ms** generation time (our live calls reported `generationtime_ms` ~0.36–2 ms).
- Model refresh: most every 6 h; regional models every 1–3 h.
- Forecast horizon: Forecast API up to 16 days, Marine API 0–8 days (default 5).
- **No SLA on the free tier.** A status page exists but no uptime guarantee is offered.

Practical reliability read: Open-Meteo is a small, well-run operation with an excellent track record, but it is not an enterprise vendor. Treat free-tier availability as best-effort. This is another argument for aggressive caching (§10) — a cached forecast means an Open-Meteo outage degrades the dashboard to "data is 3 hours old" rather than "blank page".

### 2.7 Licensing — the critical distinction

There are **two separate licences** and conflating them is the most common mistake:

**(a) The data — CC BY 4.0.** Commercial use is *permitted*. Required attribution, exact HTML from their licence page:

```html
<a href="https://open-meteo.com/">Weather data by Open-Meteo.com</a>
```

Underlying sources and their licences: DWD `CC-BY`, ECMWF `CC-BY`, MeteoSwiss `CC-BY`, NOAA NCEP standard NOAA licence, Météo-France custom, CMC custom. **None is non-commercial-only.**

**(b) The free hosted API service — NON-COMMERCIAL ONLY.** Terms state plainly: *"You may only use the free API services for non-commercial purposes."*

Free tier limits (verified from terms page): **< 10,000 calls/day, 5,000/hour, 600/minute**, ~300,000/month.

Qualifying non-commercial examples they give: private/non-profit sites without subscriptions or ads, personal home automation, public research, educational content.

**A surf school management system sold or subscribed to is commercial.** Three legitimate routes:

1. **Open-Meteo API Standard** — 1M calls/month, unlimited daily/minutely, commercial use permitted, reserved servers, fixed monthly price with no per-call overage. (Exact EUR price not published on the pricing page; requires going through subscribe/contact. Tiers above: Professional 5M, Enterprise 50M+.)
2. **Self-host Open-Meteo** — the server is open source under **AGPLv3**, on GitHub, deployable via Docker or prebuilt Ubuntu packages, giving unlimited calls. Free. *But:* no Docker on our Windows 11 target, so this means a Linux VM/VPS, plus you download and store multi-GB model grids. Realistic for a production VPS, not for the dev laptop. AGPL obligations only bite if you modify the server and expose it — running it unmodified as a private backend is fine.
3. **Stay free during dev and single-school internal use**, and gate the commercial switch behind config. This is the pragmatic path.

At our expected volume the point is nearly moot: with the caching in §10, one surf school with 5 spots polling every 30 minutes is **~480 calls/day** — 5% of the free daily cap. The licence, not the rate limit, is the binding constraint.

### RECOMMENDATION
**Adopt Open-Meteo as the default provider, registered under the slug `open-meteo`, requiring zero configuration.** Ship the three verified URLs above as the built-in query templates. Specifically:

1. Use **two calls** (Forecast + Marine), not three — `uv_index` is already in the Forecast API, so skip Air Quality unless pollen is wanted later.
2. Always send **`cell_selection=sea`** on marine calls and **`timezone=auto`** on both.
3. Set **`wind_speed_unit=kn`** as the project default (windsurf/kite convention in Alaçatı), and store canonical SI internally, converting at the presentation layer.
4. Use **`timeformat=unixtime`** in the client, not the default ISO strings. It eliminates the naive-datetime trap on `sunrise`/`sunset` entirely and stores cleanly in PostgreSQL as timezone-aware UTC. Convert to spot-local time only in templates.
5. Derive tide high/low extremes ourselves from the `sea_level_height_msl` hourly series; do not expect an extremes endpoint.
6. **Put the attribution link in the base template footer now, not later.** It is a licence condition, it costs one line, and retrofitting it after launch is how projects end up out of compliance.
7. Add a `settings.SURF_WEATHER_COMMERCIAL_MODE` flag. When true, the Open-Meteo provider must refuse to use the free host and require `OPEN_METEO_API_KEY` (switching base URL to `customer-api.open-meteo.com`). **Encode the licence boundary in code so it cannot be crossed by accident when the product starts charging.**

---

## 3. Stormglass.io

- **Base URL:** `https://api.stormglass.io/v2`
- **Endpoints:** `/weather/point`, `/tide/sea-level/point`, `/tide/extremes/point`, `/tide/stations`, `/astronomy/point`, `/bio/point`, `/elevation/point`
- **Auth:** required — `Authorization: <api-key>` header (not a Bearer token)
- **Parameters (camelCase, unlike Open-Meteo):** `waveHeight`, `waveDirection`, `wavePeriod`, `swellHeight`, `swellDirection`, `swellPeriod`, `secondarySwellHeight`, `windSpeed`, `windDirection`, `gust`, `airTemperature`, `waterTemperature`, `cloudCover`, `precipitation`, `visibility`, `humidity`, `pressure`, `seaLevel`, `currentSpeed`, `currentDirection`
- **Sources:** `sg` (Stormglass blended), `noaa`, `dwd`, `meteo`, `icon`, `fcoo`, `fmi`, `meto`. Returns per-source values in one response — genuinely useful for consensus/uncertainty display.
- **Tide support:** **Best in class of everything evaluated.** Real station-based tide with a proper high/low `extremes` endpoint, not just a model grid.

**Pricing (verified from their pricing page):**

| Plan | Price | Requests/day | Commercial use |
|---|---|---|:--:|
| Free | €0 | **10** | **No** |
| Small | €19/mo | 500 | No |
| Medium | €49/mo | 5,000 | **Yes** |
| Large | €129/mo | 25,000 | **Yes** |
| Enterprise | contact | custom | Yes |

10% discount on annual billing.

**Reliability:** commercial vendor, marine-specialist, good uptime reputation. No published SLA on lower tiers.

**Assessment:** 10 requests/day makes the free tier unusable as anything but a smoke test — with 5 spots that is 2 refreshes per spot per day. Commercial use starts at €49/mo. For a single surf school that is a real cost against a feature (tide) that barely matters in the Aegean.

### RECOMMENDATION
**Implement as an optional provider `stormglass`, but do not use it by default and do not put it on the critical path.** Its unique value is the `tide/extremes` endpoint, so wire it in as a *tide-only* provider first (`supports = {TIDE}`) — that way a school on an Atlantic coast can pay €49/mo and get real tide extremes without changing anything else. Keep it behind an unset `STORMGLASS_API_KEY`; absent key means the provider silently deregisters.

---

## 4. WorldTides

- **Base URL:** `https://www.worldtides.info/api/v3`
- **Auth:** API key required on every request (`&key=...`)
- **Data:** tide `heights`, `extremes` (high/low), `datums`, `stations`, PNG `plot`. **Tide only** — no wave, wind, or weather.
- **Credit model:** 1 credit ≈ 7 days of heights at 30-min intervals; extremes 1 credit per 7 days; datums 1 credit; stations 1–3 credits by search radius.
- **Free allocation:** **100 free credits on signup** (one-off, not recurring).
- **Paid:** ~$10 for 10,000 credits (≈5,000 predictions), cheaper in bulk. Roughly **$0.001/credit**.
- **Licensing / attribution:** required copyright notice — *"Tidal data retrieved from www.worldtides.info. Copyright © 2014-2026 Brainware LLC."* Terms state each API request is licensed for a **single user** unless the licence says otherwise; the `stations` response may be shared with multiple users. **This single-user clause is a real constraint for a multi-tenant SaaS** — clarify with them before shipping tide data to many end users from cached responses.
- **Reliability:** long-running (since 2014), narrow scope, stable.

**Assessment:** cheapest real tide data by a wide margin. With 30-min caching and 5 spots, annual cost is a few dollars. The single-user licence clause is the open question, not the price.

### RECOMMENDATION
**Implement as optional tide-only provider `worldtides`.** It is the best value premium tide upgrade and the natural choice over Stormglass when *only* tide is missing. **Before enabling it in any paid multi-tenant deployment, email info@worldtides.info and get the multi-user/caching position in writing** — our caching layer deliberately serves one upstream response to many users, which is exactly what the single-user clause addresses. Record their answer in this file.

---

## 5. NOAA CO-OPS (Tides & Currents) and NDBC

### 5.1 NOAA CO-OPS Data API
- **Base URL:** `https://api.tidesandcurrents.noaa.gov/api/prod/datagetter`
- **Auth:** **none.** No API key. They request an `application=YourAppName` param for troubleshooting.
- **Products:** `predictions` (tide), `water_level`, `water_temperature`, `air_temperature`, `wind`, `air_pressure`, `visibility`, `humidity`, `salinity`, `currents`, `currents_predictions`
- **Example (verified pattern):**
  ```
  https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?begin_date=20260815&end_date=20260822&station=8557863&product=predictions&datum=MLLW&time_zone=lst_ldt&interval=hilo&units=metric&application=SurfSchool&format=json
  ```
  `interval=hilo` gives exactly the high/low extremes we want.
- **Rate limits:** no published number; they throttle heavy users and ask you to space out calls.
- **Licence:** US Government work, public domain. Free for commercial use. No attribution required (courtesy credit good practice).
- **Fatal limitation:** **US stations only.** There is no station anywhere near Turkey. Coverage is US coasts, Great Lakes, and US territories.

### 5.2 NDBC (National Data Buoy Center)
- **Base URL:** `https://www.ndbc.noaa.gov/data/realtime2/{STATION_ID}.txt` (standard meteorological) and `.spec` (spectral wave summary)
- **Auth:** none. Plain fixed-width **text files**, not a JSON API — you parse whitespace-delimited columns.
- **Fields:** `WDIR`, `WSPD`, `GST`, `WVHT` (significant wave height), `DPD` (dominant period), `APD`, `MWD` (mean wave direction), `PRES`, `ATMP`, `WTMP`, `VIS`. The `.spec` file adds `SwH`, `SwP`, `SwD`, `WWH`, `WWP`, `WWD`, `STEEPNESS`.
- **Nature of the data:** **observations, not forecasts.** This is real measured buoy data — the ground truth wave data everything else is validated against.
- **Licence:** public domain, commercial use fine.
- **Fatal limitation:** buoy network is overwhelmingly US. No usable buoy for the Turkish Aegean.

### RECOMMENDATION
**Do not implement either for launch.** Both are excellent, free, keyless and commercially unencumbered, but neither has coverage at our reference spot, which makes them dead weight in v1. **Revisit only if the product expands to US customers** — at that point NOAA CO-OPS becomes the single best tide provider in existence for US spots (free, no key, station-grade, `interval=hilo`) and should be implemented immediately as `noaa-coops`. Note this explicitly in the provider registry docstring so the option is not forgotten. NDBC's value is different — it is an *observation* source for a future "forecast vs. actual" accuracy feature, not a forecast provider; it does not fit the `SurfConditions` forecast interface and should not be forced into it.

---

## 6. Surfline (unofficial)

- **Official position:** Surfline has **no public API.** Their support article confirms it and points commercial enquiries to a business contact.
- **Unofficial endpoint:** `https://services.surfline.com/kbyg/spots/forecasts/...` (the older `api.surfline.com` is deprecated). It is the internal endpoint the website consumes, discoverable in page source. Community projects and Apify scrapers exist against it.
- **Data quality:** the best surf-specific forecast available — spot-level, human-calibrated, with surf height ranges rather than raw model output.
- **Terms of Use:** their ToS governs surfline.com and subdomains including forecasts and data insights. Reverse-engineering an internal endpoint to power a commercial competing product is squarely against the spirit and almost certainly the letter of it.
- **Stability:** zero contract. Internal endpoints change without notice; auth/tokens get added; IP blocking is a live risk.

### RECOMMENDATION
**Do not implement. Not in v1, not behind a flag, not "just for dev".** Two independent disqualifiers: (1) legal — this is a commercial product and using a competitor's undocumented internal API is an unacceptable business risk for a feature we can get legitimately elsewhere; (2) engineering — an uncontracted endpoint that can break or start returning 403s any morning is the worst possible dependency for the screen the whole school looks at before lessons. If Surfline-grade data is genuinely needed later, **contact Surfline for a commercial licence** — that is the only correct path. Leave `surfline` out of the registry entirely rather than shipping a tempting stub.

---

## 7. Windy Point Forecast API

- **Base URL:** `POST https://api.windy.com/api/point-forecast/v2` (JSON body, not query params)
- **Auth:** API key required, and it is **service-specific** — a Map Forecast or Webcams key will not work here.
- **Models:** weather `arome`, `icon`, `gfs`, `nam*`, `hrrr*`, `canHrdps`; sea `gfsWave`, `iconWave`, `iconEuWave`, `canRdwpsWave`, `cmems`
- **Relevant params:** `waves` (height/period/direction), `wavesPower`, `windWaves`, `swell1`, `swell2`
- **Free tier:** trial limited to **500 sessions/day**; exceeding it lets them cut service for the remainder of the day.
- **Terms:** the Map & Point Forecast terms grant a **strictly personal, non-commercial licence**; all commercial use requires the Professional plan.
- **No tide, no UV, no sunrise/sunset.**

### RECOMMENDATION
**Do not implement.** It offers nothing Open-Meteo does not already provide keylessly (both ultimately serve GFS/ICON wave models), while adding an API key, a POST-body protocol that differs from every other provider, and an explicitly non-commercial trial licence. Strictly worse than the default on every axis that matters to us. Skip.

---

## 8. OpenWeatherMap, Visual Crossing, met.no

### 8.1 OpenWeatherMap
- **Base URLs:** `https://api.openweathermap.org/data/2.5/weather`, `.../data/2.5/forecast`, One Call `https://api.openweathermap.org/data/3.0/onecall`
- **Auth:** API key required (`&appid=`)
- **Free tier:** 60 calls/min, 1,000,000 calls/month on Current Weather + 5-day/3-hour Forecast + Air Pollution + Geocoding + Weather Maps. One Call 3.0/4.0 gives **first 1,000 calls/day free** but requires a card on file and bills per call beyond it — a genuine bill-shock risk if a caching bug loops.
- **UV index and sunrise/sunset** are available (One Call / current).
- **Disqualifier: no marine, wave, swell, or tide data at all.** Verified against their pricing/product pages — no maritime product is offered.

### 8.2 Visual Crossing
- **Base URL:** `https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline` (multi-location: `.../timelinemulti`)
- **Auth:** API key required (`&key=`)
- **Free tier: 1,000 records/day — and notably, free for *commercial* use as well as non-commercial.** This is the standout term: it is the only mainstream provider whose free tier is commercially clean without self-hosting.
- **Paid:** $0.0001/record pay-as-you-go; plans from ~$35/mo.
- **Covers:** temperature, wind, precipitation, visibility, UV index, sunrise/sunset, conditions — strong land-weather coverage in a single tidy call.
- **Marine:** "Maritime Elements" (wave height, wave direction, swell height, swell period) exist but are **gated to the Corporate plan**, not the free or entry tiers.
- **Tide:** none.
- **Billing unit trap:** a "record" is roughly one day-or-hour of data per location, not one HTTP request. A 7-day hourly forecast is ~168 records, so 1,000/day is only ~6 such calls/day. Much tighter than it first appears.

### 8.3 MET Norway / Yr
- **Base URL:** `https://api.met.no/weatherapi/locationforecast/2.0/compact` (also `/complete`, `/classic`)
- **Auth:** **no API key.** But a **valid identifying `User-Agent` is mandatory** — requests with missing or banned UAs (`okhttp`, `Dalvik`, `fhttp`, `Java`) get **403 Forbidden**. Format: `AppName/version contact-email-or-url`, e.g. `SurfSchool/1.0 ops@your-school.example` -- met.no requires a real, monitored contact address of **your** deployment, not one copied from here.
- **Rate limit:** anything over **20 requests/second per application** (total, not per client) needs a special agreement.
- **Caching is a term of service, not a suggestion:** you must cache locally and use `If-Modified-Since` with the exact prior `Last-Modified` value. Ignoring this risks being blocked without warning.
- **Licence: CC BY 4.0. Commercial use is permitted.** Attribution: give appropriate credit, link the licence, indicate changes.
- **Covers:** temperature, wind speed/direction, precipitation, humidity, pressure, cloud, UV radiation (`ultraviolet_index_clear_sky`), symbol codes for conditions. Sunrise/sunset via the separate **Sunrise 3.0** endpoint. No visibility.
- **Marine:** `oceanforecast/2.0` exists but covers **Nordic waters only** — useless at Alaçatı. `tidalwater/1.1` is **Norway only**.

**Why met.no matters despite the gaps:** it is the *only* provider evaluated that is simultaneously **free, keyless, and explicitly commercial-use-permitted**. That combination is unique and makes it the correct answer to "what do we use for land weather once this product starts charging money and we haven't bought an Open-Meteo plan yet?"

### RECOMMENDATION
- **OpenWeatherMap: do not implement.** Zero marine data makes it structurally unfit for a surf product; it would be a second land-weather provider we do not need.
- **Visual Crossing: implement as optional provider `visualcrossing`, priority behind Open-Meteo.** Its commercially-clean free tier is genuinely valuable insurance, but the record-based billing and Corporate-gated marine data stop it being the default. Good land-weather fallback.
- **met.no: implement as provider `metno`, and make it the designated commercial-mode land-weather fallback.** Free, keyless, commercial-OK is a combination worth the integration effort. **Hard requirements when implementing:** set the `User-Agent` from Django settings (never ship a default/blank one — it is an instant 403), and honour `If-Modified-Since`/`Last-Modified`, which our cache layer must therefore store as ETag-like metadata (§10). Pair `metno` (land) + Open-Meteo Marine (waves) as the licence-safest free stack, accepting that waves still need the Open-Meteo licence resolved.

---

## 9. Free Tide Without a Key — Options and the Fallback Plan

Restating the question: is there a free, no-key, **global** tide source?

| Option | Key? | Global? | Extremes? | Commercial? | Verdict |
|---|:--:|:--:|:--:|:--:|---|
| **Open-Meteo `sea_level_height_msl`** | **No** | **Yes** | Derive yourself | Data CC BY; service non-comm | **Best default** |
| NOAA CO-OPS | No | **US only** | Yes (`interval=hilo`) | Yes (public domain) | Best *if* US |
| ODB Open Tide API (NTU Taiwan) | No | Yes (TPXO10) | No | Unclear + TPXO terms | See below |
| Local harmonic computation (pyTMD / pytides) | No | Yes | Yes | **Model-licence dependent** | Best offline fallback |
| WorldTides | Yes | Yes | Yes | Paid, single-user clause | Best paid |
| Stormglass | Yes | Yes | Yes | €49/mo+ | Best paid, station-grade |
| met.no tidalwater | No | **Norway only** | Yes | CC BY | Irrelevant to us |

**9.1 The ODB Open Tide API** — `https://eco.odb.ntu.edu.tw/api/tide`, run by Ocean Data Bank, National Taiwan University. No API key. Uses **TPXO10-atlas-v2** via pyTMD. Params `lon0`, `lat0`, `start`, `end` (≤30 days hourly); returns `z` tide height in **cm**, `u`/`v` current components in cm/s.

`UNVERIFIED / PARTIALLY FAILED`: three live attempts at the Alaçatı coordinates returned **HTTP 422** or an empty body across parameter-format variations. The documented parameter names are right but the exact date-format contract was not established in this pass. **Do not design around it until a successful call is reproduced.** Two further concerns even if it works: it is a single academic server with no SLA or support commitment, and **TPXO carries its own licensing terms — TPXO atlas products are free for academic use, with commercial use requiring a licence from Oregon State University.** Consuming it via a third-party API does not launder that. Same caveat applies to FES2014 (AVISO licence, free for research, commercial by agreement).

**9.2 Local harmonic computation** — `pyTMD`, `pytide` (CNES), or `pytides` compute tide from harmonic constituents offline: no key, no network, no rate limit, exact high/low extremes at any resolution. **The blocker is not the code, it is the constituent data** — the accurate global atlases (TPXO, FES) carry the non-commercial-by-default terms above. NOAA publishes station constituents for US stations freely and public-domain, which is a genuinely clean path *for US spots only*.

For Alaçatı specifically, the honest engineering answer: with a ~20–40 cm Mediterranean range, a locally-computed harmonic tide and the Open-Meteo model will agree within the noise, and **neither will change a single scheduling decision.** Effort spent on tide precision here is effort not spent on wind, which is the variable that actually determines whether a lesson runs.

**9.3 Deriving high/low extremes from Open-Meteo (the actual v1 plan)**

Take the hourly `sea_level_height_msl` array and find sign changes in the first difference:

```python
def find_tide_extremes(times, heights):
    """Local minima/maxima of an hourly sea-level series -> tide extremes."""
    out = []
    for i in range(1, len(heights) - 1):
        prev, cur, nxt = heights[i - 1], heights[i], heights[i + 1]
        if None in (prev, cur, nxt):
            continue
        if cur > prev and cur >= nxt:
            out.append({"time": times[i], "type": "high", "height_m": cur})
        elif cur < prev and cur <= nxt:
            out.append({"time": times[i], "type": "low", "height_m": cur})
    return out
```

Accuracy on turn *time* is ±30 min from hourly sampling. Refine with quadratic interpolation through the three points if a nicer number is wanted; do not pretend it is station-grade either way.

**9.4 Field-level merging across providers**

Because tide comes from a different provider than waves in some configurations, the aggregator must merge **per field**, not per provider — take wave fields from the marine provider, tide fields from the tide provider, land fields from the weather provider, each with its own `source` label so the UI can attribute correctly and the admin can debug which provider produced a suspicious number.

### RECOMMENDATION
**Ship Open-Meteo `sea_level_height_msl` + our own extremes derivation as the v1 tide, and label it honestly in the UI as "modelled tide" with a tooltip, not as station data.** Do not implement ODB (unverified and licence-murky) and do not implement local harmonic computation for v1 (the constituent atlases are not commercially clean and the Aegean range does not justify it). Define the `TideProvider` sub-interface now so that WorldTides or Stormglass can be dropped in as a paid upgrade the day a school on a real tidal coast signs up — that is the scenario that makes this matter, and it is a config change, not a rewrite.

---

## 10. Proposed Design: Pluggable Provider Layer

### 10.1 Package layout

```
surf_school/
  weather/
    __init__.py
    models.py              # Spot, ForecastSnapshot, ProviderHealth
    dto.py                 # frozen dataclasses: SurfConditions, TidePoint, Field enum
    base.py                # BaseWeatherProvider ABC
    registry.py            # ProviderRegistry, @register decorator
    aggregator.py          # field-level merge + fallback chain
    cache.py               # cache keys, TTL policy, conditional-request metadata
    exceptions.py          # ProviderError hierarchy
    providers/
      __init__.py          # autodiscovery import
      open_meteo.py        # DEFAULT — no key
      metno.py             # commercial-clean land fallback
      visualcrossing.py    # optional, key
      stormglass.py        # optional, key, tide-capable
      worldtides.py        # optional, key, tide-only
    services.py            # public API used by views/DRF
    tasks.py               # Celery tasks (optional)
    apps.py                # ready() -> registry.autodiscover()
```

### 10.2 The data contract

```python
# weather/dto.py
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

class Field(str, Enum):
    WAVE_HEIGHT = "wave_height"
    SWELL_HEIGHT = "swell_height"
    SWELL_PERIOD = "swell_period"
    SWELL_DIRECTION = "swell_direction"
    WIND_SPEED = "wind_speed"
    WIND_DIRECTION = "wind_direction"
    TIDE = "tide"
    AIR_TEMPERATURE = "air_temperature"
    WATER_TEMPERATURE = "water_temperature"
    WEATHER_CONDITION = "weather_condition"
    UV_INDEX = "uv_index"
    PRECIPITATION = "precipitation"
    VISIBILITY = "visibility"
    SUNRISE = "sunrise"
    SUNSET = "sunset"

@dataclass(frozen=True, slots=True)
class SurfConditions:
    """Canonical SI units. Every field Optional — no provider fills them all."""
    timestamp: datetime                      # tz-aware UTC, always
    wave_height_m: float | None = None
    swell_height_m: float | None = None
    swell_period_s: float | None = None
    swell_direction_deg: float | None = None
    wind_speed_ms: float | None = None       # store m/s, present as knots
    wind_direction_deg: float | None = None
    tide_height_m: float | None = None
    air_temperature_c: float | None = None
    water_temperature_c: float | None = None
    weather_code: int | None = None          # normalised to WMO
    uv_index: float | None = None
    precipitation_mm: float | None = None
    visibility_m: float | None = None
    sunrise: datetime | None = None
    sunset: datetime | None = None
    sources: dict[str, str] | None = None    # {"wave_height": "open-meteo", ...}
```

**Canonical-units rule:** every provider converts into SI at its own boundary. m/s, metres, °C, degrees, mm, WMO codes, tz-aware UTC. Presentation converts to knots/km. Without this rule the merge layer silently mixes knots and km/h and nobody notices until a lesson is cancelled for the wrong reason.

**WMO normalisation:** Open-Meteo uses WMO codes natively, so make WMO the internal standard and give every other provider a `_to_wmo()` mapping (met.no `symbol_code` strings, Visual Crossing `icon`/`conditions`, Stormglass cloud cover + precip).

### 10.3 Base class

```python
# weather/base.py
from abc import ABC, abstractmethod
from datetime import date

class BaseWeatherProvider(ABC):
    slug: str
    display_name: str
    supports: frozenset[Field]
    requires_api_key: bool = False
    attribution_html: str = ""
    priority: int = 100                 # lower = preferred
    max_forecast_days: int = 7

    def __init__(self, api_key: str | None = None, timeout: float = 10.0):
        self.api_key, self.timeout = api_key, timeout

    @classmethod
    def is_available(cls, settings_obj) -> bool:
        """Deregister cleanly when the key is absent."""
        if not cls.requires_api_key:
            return True
        return bool(getattr(settings_obj, f"{cls.slug.upper().replace('-', '_')}_API_KEY", None))

    @abstractmethod
    def fetch_current(self, lat: float, lon: float) -> SurfConditions: ...

    @abstractmethod
    def fetch_forecast(self, lat: float, lon: float, days: int) -> list[SurfConditions]: ...

    def fetch_tide_extremes(self, lat: float, lon: float, day: date) -> list[TidePoint]:
        raise NotImplementedError

    def health_check(self) -> bool: ...
```

Design points worth defending:

- **`supports: frozenset[Field]`** is what makes field-level merging possible. The aggregator asks "who can give me `TIDE`?" rather than "who is the current provider?". This is the single decision that stops the layer becoming an if/else chain.
- **`is_available()` as a classmethod** means a provider with no configured key never enters the registry at all, instead of failing at request time. Missing config becomes a startup-time absence, not a 500.
- **`attribution_html` on the class** means the footer renders attributions for whichever providers actually contributed. Licence compliance follows the data automatically instead of depending on someone remembering.
- **`priority`** gives a deterministic fallback order without hardcoding names in the aggregator.

### 10.4 Registry

```python
# weather/registry.py
class ProviderRegistry:
    def __init__(self):
        self._classes: dict[str, type[BaseWeatherProvider]] = {}

    def register(self, cls):
        if cls.slug in self._classes:
            raise ImproperlyConfigured(f"Duplicate provider slug: {cls.slug}")
        self._classes[cls.slug] = cls
        return cls

    def get(self, slug) -> BaseWeatherProvider: ...
    def available(self) -> list[BaseWeatherProvider]:
        """Instantiated, key-satisfied, sorted by priority."""
    def providers_for(self, field: Field) -> list[BaseWeatherProvider]:
        return [p for p in self.available() if field in p.supports]

registry = ProviderRegistry()
register = registry.register
```

Autodiscovery in `apps.py::ready()` imports `weather.providers.*`, so adding a provider is one new file plus a `@register` decorator — no central list to edit and forget.

Settings surface:

```python
SURF_WEATHER = {
    "DEFAULT_PROVIDER": "open-meteo",
    "FALLBACK_CHAIN": ["open-meteo", "metno", "visualcrossing"],
    "TIDE_PROVIDER": "open-meteo",       # -> "worldtides" / "stormglass" when paid
    "COMMERCIAL_MODE": False,            # True forces licensed providers only
    "USER_AGENT": "SurfSchool/1.0 (contact@example.com)",  # met.no requires this
    "TIMEOUT": 10.0,
    "CACHE_TTL": {"current": 900, "hourly": 1800, "daily": 10800, "astro": 86400},
}
```

### 10.5 Aggregator behaviour

`get_conditions(spot)` walks `providers_for(field)` in priority order, takes the first non-`None` value per field, records `sources[field] = provider.slug`, and on `ProviderError` marks that provider unhealthy and continues down the chain. Result: one provider failing degrades individual *fields*, never the whole dashboard.

Wrap failures in a **circuit breaker** — after N consecutive failures, skip the provider for M minutes (persisted in `ProviderHealth`). Without it, a provider that is timing out at 10s each adds 10s to every page load while it is down, which is worse than not having it.

### RECOMMENDATION
**Build exactly this structure before writing the first provider.** The temptation is to call Open-Meteo directly from a view "for now" and abstract later; resist it, because the licence situation in §2.7 guarantees a provider swap is coming, and retrofitting an abstraction after views, templates and tasks all depend on Open-Meteo's response shape is far more expensive than the two hours the ABC costs today. Concretely: implement `BaseWeatherProvider`, `ProviderRegistry`, `SurfConditions`, and `OpenMeteoProvider` in the first pass; add `MetNoProvider` second **purely to prove the abstraction holds** against a provider with a totally different response shape, auth model and caching contract. If the interface survives met.no, it will survive the rest.

---

## 11. Caching Strategy

### 11.1 Why caching is not optional here

Three independent reasons: (1) Open-Meteo's free tier has hard rate limits and a non-commercial licence — fewer calls is both cheaper and lower-risk; (2) **met.no makes local caching an explicit term of service** and blocks violators; (3) the data itself only changes every 1–6 hours, so anything more frequent is pure waste.

### 11.2 Cache tiers

| Tier | Backend | TTL | Purpose |
|---|---|---|---|
| L1 request-local | `dict` on request | request | Dedupe repeated calls within one render |
| L2 shared | Redis (prod) / LocMem (dev) | 15 min – 3 h | The real cache |
| L3 durable | PostgreSQL `ForecastSnapshot` | 7–30 days | Survives cache flush; enables history/accuracy |

L3 is what makes an Open-Meteo outage a non-event: serve the last snapshot with a visible "as of HH:MM" stamp rather than an error.

### 11.3 TTLs matched to model refresh

| Data | TTL | Rationale |
|---|---|---|
| Current conditions | **15 min** | Open-Meteo `current` has `interval: 900` (15 min) — matching it exactly means never fetching a value that cannot have changed |
| Hourly forecast | **30 min** | Models refresh every 1–6 h; 30 min is comfortably fresh |
| Daily forecast | **3 h** | Aligns with the 6-hourly model cycle |
| Sunrise / sunset | **24 h** | Astronomical — deterministic, changes ~1 min/day |
| Tide series | **6 h** | Harmonic/model driven, effectively static intraday |
| Geocoding / elevation | **30 days** | Static per spot |

Sunrise/sunset at 24 h is the cheap win: it is ~1/3 of the payload and it is *astronomy*, so re-fetching it every 15 minutes is indefensible.

### 11.4 Key design

```
surfwx:v1:{provider}:{kind}:{lat:.3f}:{lon:.3f}:{units_hash}
```

- **`v1` prefix** lets you invalidate everything by bumping it when the DTO changes — essential when pickled/JSON-serialised dataclasses change shape between deploys.
- **Round lat/lon to 3 dp (~110 m)**. Open-Meteo snaps to grid cells anyway (our 38.28 became 38.208), so finer keys just fragment the cache for identical upstream responses. Spots 100 m apart legitimately share a cache entry.
- **`units_hash`** prevents serving a metric-cached response to an imperial request.

### 11.5 Stale-while-revalidate

Store `(payload, fetched_at, hard_expiry)` with the cache TTL set to the *hard* expiry (say 4× the soft TTL). On read: if `now < soft_expiry` serve fresh; if between soft and hard, **serve stale immediately and refresh in the background**; only past hard expiry does the user wait. Users never block on a network call, and the upstream never sees a thundering herd.

### 11.6 Conditional requests (required for met.no)

The cache entry must carry `Last-Modified` / `ETag` alongside the payload so refreshes send `If-Modified-Since`. A `304 Not Modified` costs almost nothing and resets the TTL. **This is a met.no ToS obligation**, so the cache layer must support it from the start rather than having it bolted on — it changes the stored value shape, which is exactly the sort of thing that is painful to retrofit.

### 11.7 Refresh strategy on Windows 11

Windows-native, no Docker, so **Celery is awkward** (Celery dropped robust Windows worker support; `--pool=solo` works but is fragile and single-threaded).

- **Dev / single school (recommended v1):** lazy stale-while-revalidate refresh triggered by requests, with the background refresh on a `ThreadPoolExecutor`. **No Celery, no Redis, no broker.** LocMem or database cache. Zero moving parts on Windows.
- **Production, if scheduled prefetch is wanted:** a Django management command `python manage.py refresh_forecasts` run by **Windows Task Scheduler** every 30 min. This is the pragmatic Windows-native answer and avoids the entire Celery-on-Windows problem class.
- **Only if genuinely needed later:** Celery + Redis on a Linux VPS, or Redis via WSL2.

Warm the cache for *active* spots only — spots with a lesson scheduled in the next 48 h. A school with 5 spots at 30-min prefetch is ~480 calls/day; prefetching every spot ever created scales badly for no benefit.

### 11.8 HTMX integration

HTMX makes the polling pattern natural and lets the cache do its job:

```html
<div hx-get="{% url 'weather:spot_conditions' spot.pk %}"
     hx-trigger="load, every 300s"
     hx-swap="innerHTML">
```

Poll the *view* every 5 min; the view serves from cache and only occasionally hits upstream. Client refresh rate and upstream call rate are fully decoupled — which is precisely why the polling frequency can be generous without touching the rate limit. Return a small HTML partial, not JSON, and let Alpine.js own only local interactivity (unit toggle, expanding the hourly strip).

### RECOMMENDATION
**Implement L2 + L3 with stale-while-revalidate and the TTL table above from day one; skip Celery and Redis entirely for v1.** Use Django's database cache backend in dev (works on Windows with no services) and Redis in prod only if a real need appears. Schedule prefetch with Windows Task Scheduler calling a management command — not Celery Beat. Store `Last-Modified`/`ETag` in every cache entry even before met.no is implemented, because that is the one part of this design that is expensive to add later. Target: **under 500 upstream calls/day for a 5-spot school**, i.e. ~5% of the Open-Meteo free daily allowance, leaving ample headroom.

---

## 12. Implementation Checklist

1. `weather` app skeleton per §10.1; `SurfConditions` DTO with all 15 fields, SI units, tz-aware UTC.
2. `BaseWeatherProvider` + `ProviderRegistry` + autodiscovery in `apps.ready()`.
3. `OpenMeteoProvider` — two calls (Forecast + Marine), `timeformat=unixtime`, `cell_selection=sea`, `timezone=auto`. Use the exact verified URLs in §2.4.
4. Tide extremes derivation from `sea_level_height_msl` (§9.3); UI label "modelled tide".
5. Cache layer: keys, TTL table, stale-while-revalidate, `Last-Modified`/`ETag` storage (§11).
6. **Attribution in the base template footer** — `<a href="https://open-meteo.com/">Weather data by Open-Meteo.com</a>` — driven by `attribution_html` of contributing providers.
7. `MetNoProvider` as the abstraction stress-test (different shape, mandatory User-Agent, conditional requests).
8. `ForecastSnapshot` model + `refresh_forecasts` management command + Windows Task Scheduler entry.
9. `SURF_WEATHER_COMMERCIAL_MODE` flag enforcing the licence boundary in code.
10. Optional/deferred: `worldtides`, `stormglass`, `visualcrossing`. Explicitly excluded: Surfline, Windy, OpenWeatherMap, NOAA/NDBC (revisit NOAA on US expansion).

---

## 13. Open Questions to Resolve Before Commercial Launch

1. **Open-Meteo API Standard exact price** — not published; requires contacting them or going through subscribe. Needed for the cost model.
2. **WorldTides single-user clause** — confirm in writing whether serving cached tide data to multiple end users is permitted (§4).
3. **ODB Open Tide API** — reproduce a successful call, or drop it permanently (§9.1).
4. **Commercial-mode decision** — buy an Open-Meteo plan vs. self-host on a Linux VPS. Depends on (1).

---

## Sources

- [Open-Meteo Marine Weather API docs](https://open-meteo.com/en/docs/marine-weather-api)
- [Open-Meteo Terms](https://open-meteo.com/en/terms) · [Pricing](https://open-meteo.com/en/pricing) · [Licence](https://open-meteo.com/en/licence) · [Features](https://open-meteo.com/en/features)
- [Open-Meteo server source (AGPLv3)](https://github.com/open-meteo/open-meteo)
- [Stormglass API docs](https://docs.stormglass.io/) · [Stormglass pricing](https://stormglass.io/pricing/) · [Stormglass Global Tide API](https://stormglass.io/global-tide-api/)
- [WorldTides API docs](https://www.worldtides.info/apidocs) · [WorldTides developer/pricing](https://www.worldtides.info/developer) · [WorldTides copyright](https://www.worldtides.info/copyright)
- [NOAA CO-OPS Data API](https://api.tidesandcurrents.noaa.gov/api/prod/)
- [NDBC realtime data](https://www.ndbc.noaa.gov/data/realtime2/)
- [Surfline: Does Surfline have a forecast API?](https://support.surfline.com/hc/en-us/articles/13883685219227-Does-Surfline-have-a-forecast-API) · [Surfline Terms of Use](https://www.surfline.com/terms-of-use)
- [Windy Point Forecast API docs](https://api.windy.com/point-forecast/docs) · [Windy API terms of use](https://account.windy.com/agreements/windy-api-map-and-point-forecast-terms-of-use) · [Windy Point Forecast pricing](https://api.windy.com/point-forecast/pricing)
- [OpenWeatherMap pricing](https://openweathermap.org/price)
- [Visual Crossing Timeline Weather API docs](https://www.visualcrossing.com/resources/documentation/weather-api/timeline-weather-api/) · [Visual Crossing free plan](https://www.visualcrossing.com/resources/documentation/visual-crossing-weather-free-plan-free-weather-data-for-analysts-and-api-developers/) · [Visual Crossing editions](https://www.visualcrossing.com/weather-data-editions/)
- [MET Norway Locationforecast 2.0 docs](https://api.met.no/weatherapi/locationforecast/2.0/documentation) · [MET Norway Terms of Service](https://api.met.no/doc/TermsOfService)
- [ODB Open Tide API (TPXO10)](https://github.com/cywhale/tide)
- [pyTMD/pytide (CNES)](https://github.com/CNES/pangeo-pytide) · [pytides](https://github.com/sam-cox/pytides) · [PyFES](https://cnes.github.io/aviso-fes/)
