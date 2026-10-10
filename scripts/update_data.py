#!/usr/bin/env python3
"""Refresh data/data.txt for the Crop Weather Tools dashboard.

Sources
  Kentucky observations: ACIS StnData (NOAA Regional Climate Centers)
  Arizona observations:  AZMet API (University of Arizona)
  16-day forecast and hourly forecast: Open-Meteo forecast API
  46-day outlook: ECMWF IFS extended-range ensemble (EC46) via the Open-Meteo seasonal API

Run:  python scripts/update_data.py  (writes data/data.txt; exits non-zero on failure
so the workflow never commits a broken file)
"""
import datetime as dt
import statistics as st
import sys
import time
from pathlib import Path

import requests

KY = {  # ACIS station id: display name
    "KBWG": "Bowling Green",
    "KLEX": "Lexington",
    "KPAH": "Paducah",
    "KSDF": "Louisville",
}
AZ = {  # AZMet id: (name, lat, lon)
    "az06": ("Maricopa", 33.069, -111.972),
    "az05": ("Coolidge", 32.981, -111.606),
    "az26": ("Buckeye", 33.379, -112.585),
    "az19": ("Paloma", 32.926, -112.896),
    "az04": ("Safford", 32.812, -109.681),
    "az02": ("Yuma Valley", 32.712, -114.705),
}
import os
REGION = os.environ.get("REGION", "all")  # ky, az or all
if REGION == "ky":
    AZ = {}
if REGION == "az":
    KY = {}
MEMBERS = 51          # all EC46 members available via Open-Meteo (control + 50)
OUTLOOK_DAYS = 45     # EC46 runs to day 46 from its own start date
FC_DAYS = 15
HOURS = 120
CLIM_YEARS = 30
OUT = Path(__file__).resolve().parents[1] / "data" / "data.txt"
S = requests.Session()


def get(url, **kw):
    for attempt in range(4):
        try:
            r = S.get(url, timeout=120, **kw)
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            if attempt == 3:
                raise
            print(f"retry {url}: {e}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))


def post(url, **kw):
    for attempt in range(4):
        try:
            r = S.post(url, timeout=180, **kw)
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            if attempt == 3:
                raise
            print(f"retry {url}: {e}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def I(v):
    return "" if v is None else str(int(round(v)))


def F(v, n=1):
    if v is None:
        return ""
    s = f"{v:.{n}f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def interp(vals):
    """Fill interior gaps linearly; leading/trailing gaps with nearest value."""
    idx = [i for i, v in enumerate(vals) if v is not None]
    if not idx:
        return vals
    out = list(vals)
    for i in range(len(out)):
        if out[i] is None:
            lo = max([j for j in idx if j < i], default=None)
            hi = min([j for j in idx if j > i], default=None)
            if lo is None:
                out[i] = vals[hi]
            elif hi is None:
                out[i] = vals[lo]
            else:
                out[i] = vals[lo] + (vals[hi] - vals[lo]) * (i - lo) / (hi - lo)
    return out


def daterange(a, b):
    d = a
    while d <= b:
        yield d
        d += dt.timedelta(days=1)


def acis(sid, sdate, edate):
    r = post("https://data.rcc-acis.org/StnData",
             json={"sid": sid, "sdate": str(sdate), "edate": str(edate),
                   "elems": "maxt,mint", "meta": "name,ll"}).json()
    lon, lat = r["meta"]["ll"]
    rows = {d[0]: (num(d[1]), num(d[2])) for d in r["data"]}
    return lat, lon, rows


def azmet(sid, start, end):
    rows = {}
    a = start
    while a <= end:
        days = min(366, (end - a).days + 2)
        d = get(f"https://api.azmet.arizona.edu/v1/observations/daily/{sid}/{a}T00:00/P{days}DT0H").json()["data"]
        for x in d:
            rows[x["datetime"]] = x
        a = a + dt.timedelta(days=days)
    return rows


def forecast(lat, lon, hourly=False):
    p = {"latitude": lat, "longitude": lon,
         "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,et0_fao_evapotranspiration,wind_speed_10m_max",
         "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch",
         "forecast_days": 16, "past_days": 5, "timezone": "auto"}
    if hourly:
        p.update({"hourly": "temperature_2m,precipitation_probability,wind_speed_10m,relative_humidity_2m",
                  "forecast_hours": HOURS})
    return get("https://api.open-meteo.com/v1/forecast", params=p).json()


def seasonal(lat, lon, variables):
    r = get("https://seasonal-api.open-meteo.com/v1/seasonal",
            params={"latitude": lat, "longitude": lon, "daily": ",".join(variables),
                    "temperature_unit": "fahrenheit", "forecast_days": OUTLOOK_DAYS + 5, "models": "ecmwf_ec46",
                    "timezone": "auto"}).json()
    d = r["daily"]
    out = {"time": d["time"]}
    for v in variables:
        keys = [v] + sorted(k for k in d if k.startswith(v + "_member"))
        out[v] = [d[k] for k in keys]
    return out


def main():
    today = dt.datetime.utcnow().date()
    yesterday = today - dt.timedelta(days=1)

    # ---------- find the last day every station has observations ----------
    ky_raw, az_raw = {}, {}
    sy = yesterday.year if (yesterday.month, yesterday.day) >= (9, 7) else yesterday.year - 1
    ky_start = dt.date(sy - 1, 9, 1)
    clim_first = dt.date(yesterday.year - CLIM_YEARS, 1, 1)
    for sid in KY:
        ky_raw[sid] = acis(sid, clim_first, yesterday)
    az_start = dt.date(yesterday.year, 1, 1)
    az_prev = dt.date(yesterday.year - 1, 1, 1)
    for sid in AZ:
        az_raw[sid] = azmet(sid, az_prev, yesterday)

    def last_valid_ky(rows):
        ds = [d for d, (x, n) in rows.items() if x is not None and n is not None]
        return dt.date.fromisoformat(max(ds))

    def last_valid_az(rows):
        ds = [d for d, x in rows.items() if num(x.get("heat_units_55F")) is not None]
        return dt.date.fromisoformat(max(ds))

    last_obs = min([last_valid_ky(r[2]) for r in ky_raw.values()] +
                   [last_valid_az(r) for r in az_raw.values()])
    if (yesterday - last_obs).days > 4:
        sys.exit(f"Observations are stale (last common day {last_obs}); not updating.")
    first_fc = last_obs + dt.timedelta(days=1)
    fc_dates = [str(first_fc + dt.timedelta(days=i)) for i in range(OUTLOOK_DAYS)]
    print("last_obs", last_obs, "season year", sy)

    lines = [f"META last_obs={last_obs} first_fc={first_fc} ky_start={ky_start} az_start={az_start} "
             f"outlook=ecmwf_ec46 generated={dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%MZ')}"]

    # ---------- Kentucky ----------
    for sid, name in KY.items():
        lat, lon, rows = ky_raw[sid]
        days = [str(d) for d in daterange(ky_start, last_obs)]
        tmax = interp([rows.get(d, (None, None))[0] for d in days])
        tmin = interp([rows.get(d, (None, None))[1] for d in days])

        f = forecast(lat, lon, hourly=True)
        fd = f["daily"]
        fmap = {t: i for i, t in enumerate(fd["time"])}
        fc = []
        for d in fc_dates[:FC_DAYS]:
            i = fmap.get(d)
            fc.append((fd["temperature_2m_max"][i], fd["temperature_2m_min"][i],
                       fd["precipitation_sum"][i], fd["wind_speed_10m_max"][i]) if i is not None else (None,) * 4)
        h = f["hourly"]

        s = seasonal(lat, lon, ["temperature_2m_max", "temperature_2m_min"])
        smap = {t: i for i, t in enumerate(s["time"])}
        mx, mn = s["temperature_2m_max"], s["temperature_2m_min"]
        members, med = [], []
        for m in range(min(MEMBERS, len(mx))):
            row = []
            for k, d in enumerate(fc_dates):
                i = smap.get(d)
                a = mx[m][i] if i is not None else None
                b = mn[m][i] if i is not None else None
                if a is None or b is None:  # before the outlook starts: use the deterministic forecast
                    a, b = fc[min(k, FC_DAYS - 1)][0], fc[min(k, FC_DAYS - 1)][1]
                row.append((a + b) / 2 if a is not None and b is not None else None)
            members.append(interp(row))
        for k, d in enumerate(fc_dates):
            i = smap.get(d)
            xs = [x[i] for x in mx if i is not None and x[i] is not None]
            ns = [x[i] for x in mn if i is not None and x[i] is not None]
            med.append((st.median(xs) if xs else fc[min(k, FC_DAYS - 1)][0],
                        st.median(ns) if ns else fc[min(k, FC_DAYS - 1)][1]))

        lines.append(f"#KY {sid} {name}")
        lines.append("OBS " + ";".join(f"{I(a)},{I(b)}" for a, b in zip(tmax, tmin)))
        lines.append("FC " + ";".join(f"{I(a)},{I(b)},{F(c, 2)},{I(w)}" for a, b, c, w in fc))
        lines.append(f"HR start={h['time'][0]} " + ";".join(
            f"{I(a)},{I(b)},{I(c)},{I(e)}" for a, b, c, e in zip(
                h["temperature_2m"], h["precipitation_probability"], h["wind_speed_10m"], h["relative_humidity_2m"])))
        for m, row in enumerate(members):
            lines.append(f"S{m} " + ",".join(I(v) for v in row))
        lines.append("SMED " + ";".join(f"{I(a)},{I(b)}" for a, b in med))

        # freeze climatology from the last CLIM_YEARS complete years
        for thr in (32, 28, 24):
            last, first = [], []
            for y in range(yesterday.year - CLIM_YEARS, yesterday.year):
                sp = [d for d, (x, n) in rows.items() if d.startswith(str(y)) and d[5:7] <= "06" and n is not None and n <= thr]
                fa = [d for d, (x, n) in rows.items() if d.startswith(str(y)) and d[5:7] >= "07" and n is not None and n <= thr]
                if sp:
                    last.append(dt.date.fromisoformat(max(sp)).timetuple().tm_yday)
                if fa:
                    first.append(dt.date.fromisoformat(min(fa)).timetuple().tm_yday)
            lines.append(f"FR{thr} " + ",".join(map(str, sorted(last))) + "|" + ",".join(map(str, sorted(first))))
        print(sid, "ok")

    # ---------- Arizona ----------
    for sid, (name, lat, lon) in AZ.items():
        rows = az_raw[sid]
        cur = [str(d) for d in daterange(az_start, last_obs)]
        prev = [str(d) for d in daterange(az_prev, dt.date(az_prev.year, 12, 31))]
        hu = interp([num(rows.get(d, {}).get("heat_units_55F")) for d in cur])
        eto = interp([num(rows.get(d, {}).get("eto_pen_mon_in")) for d in cur])
        hs = [num(rows.get(d, {}).get("heatstress_cotton_meanF")) for d in cur]
        hprev = interp([num(rows.get(d, {}).get("heat_units_55F")) for d in prev])

        f = forecast(lat, lon)
        fd = f["daily"]
        fmap = {t: i for i, t in enumerate(fd["time"])}
        fc = []
        for d in fc_dates[:FC_DAYS]:
            i = fmap.get(d)
            fc.append((fd["temperature_2m_max"][i], fd["temperature_2m_min"][i],
                       fd["et0_fao_evapotranspiration"][i]) if i is not None else (None,) * 3)
        s = seasonal(lat, lon, ["temperature_2m_max", "temperature_2m_min", "et0_fao_evapotranspiration"])
        smap = {t: i for i, t in enumerate(s["time"])}
        med = []
        for k, d in enumerate(fc_dates):
            i = smap.get(d)
            vals = []
            for v in ["temperature_2m_max", "temperature_2m_min", "et0_fao_evapotranspiration"]:
                xs = [x[i] for x in s[v] if i is not None and x[i] is not None]
                vals.append(st.median(xs) if xs else None)
            if vals[2] is not None:
                vals[2] /= 25.4  # seasonal ET0 is in mm
            if None in vals:
                fb = fc[min(k, FC_DAYS - 1)]
                vals = [vals[j] if vals[j] is not None else fb[j] for j in range(3)]
            med.append(vals)

        lines.append(f"#AZ {sid} {name}")
        lines.append("OCUR " + ";".join(f"{F(a)},{F(b, 2)},{F(c)}" for a, b, c in zip(hu, eto, hs)))
        lines.append("HPREV " + ",".join(F(v) for v in hprev))
        lines.append("FC " + ";".join(f"{I(a)},{I(b)},{F(c, 2)}" for a, b, c in fc))
        lines.append("SMED " + ";".join(f"{I(a)},{I(b)},{F(c, 2)}" for a, b, c in med))
        print(sid, "ok")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n")
    print("wrote", OUT, OUT.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
