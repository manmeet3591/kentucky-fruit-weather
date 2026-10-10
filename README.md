# Kentucky Fruit Weather

Weather decision tools for Kentucky grape and peach growers, with S2S outlooks (WKU prototype).

**Live site:** https://manmeet3591.github.io/kentucky-fruit-weather/

Each tool runs a published crop model on observed station weather, then carries it forward with a 16-day forecast and a 60-day ensemble outlook. The outlook is the slot for the WKU AI sub-seasonal-to-seasonal (S2S) forecast.

| Tool | Model and thresholds |
|---|---|
| Grape bud hardiness | Ferguson et al. (2014) *Am. J. Enol. Vitic.* 65:59, Table 4 parameters; run per ensemble member |
| Peach chill and freeze | Chill hours (32–45°F) or Utah units (Richardson et al. 1974); MSU/WSU critical bud temperatures |
| Frost dates | 30-year first/last freeze climatology plus forecast |
| Spray windows | Hourly wind, rain chance and temperature rules of thumb |

## How it works

- `index.html` is the whole app: plain HTML, CSS and JavaScript, no build step.
- `data/data.txt` holds the weather it reads. A GitHub Action runs `scripts/update_data.py` (with `REGION=ky`) every morning and commits fresh data from [ACIS](https://www.rcc-acis.org/) daily observations for Kentucky NWS stations (Bowling Green, Lexington, Paducah, Louisville) and [Open-Meteo](https://open-meteo.com/) forecast and seasonal ensemble APIs.
- To run locally: `pip install requests && REGION=ky python scripts/update_data.py`, then `python -m http.server` and open `http://localhost:8000`.

## Plugging in the WKU S2S model

`scripts/update_data.py` writes the outlook as `S0`…`S20` lines (daily mean °F per ensemble member, 60 days) and `SMED` (median daily max/min). Replace the `seasonal()` call with the AI forecast output in the same shape and every tool picks it up.

## Status

Research prototype from Western Kentucky University; not an official Extension product.
