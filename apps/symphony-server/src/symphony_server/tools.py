"""Data tools Symphony runs for the voice model.

They are core_harness Tools, so the same objects serve core_server runs
(`/api/runs`) and the realtime tool loop. Web search is Grok's built-in
server-side `web_search` tool, enabled in the session config.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from core_harness import Tool
from pydantic import BaseModel, Field

WMO_CODES: dict[int, str] = {
    0: "clear",
    1: "mostly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "fog",
    51: "drizzle",
    53: "drizzle",
    55: "drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    80: "showers",
    81: "showers",
    82: "heavy showers",
    95: "thunderstorms",
}


class ToolInputError(ValueError):
    """The model passed arguments a tool cannot use."""


class _GeoResult(BaseModel):
    name: str
    latitude: float
    longitude: float
    admin1: str | None = None
    country: str | None = None


class _GeoResponse(BaseModel):
    results: list[_GeoResult] = Field(default_factory=list)


class _Daily(BaseModel):
    time: list[str]
    temperature_2m_max: list[float]
    temperature_2m_min: list[float]
    weather_code: list[int]


class _Forecast(BaseModel):
    daily: _Daily


class WeatherTool(Tool):
    def __init__(self, client: httpx.AsyncClient) -> None:
        super().__init__(
            name="get_weather",
            description="Daily forecast (up to 7 days, Fahrenheit) for a place name.",
            parameters={
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City, e.g. 'Palo Alto'"},
                    "days": {"type": "integer", "minimum": 1, "maximum": 7},
                },
                "required": ["location"],
            },
        )
        self.client = client

    async def execute(self, *, sink: Any, args: dict[str, Any]) -> Any:
        location = str(args.get("location", "")).strip()
        if not location:
            raise ToolInputError("location is required")
        days = max(1, min(7, int(args.get("days", 3))))
        geo = await self.client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": 1},
        )
        geo.raise_for_status()
        places = _GeoResponse.model_validate(geo.json()).results
        if not places:
            raise ToolInputError(f"no place called {location!r}")
        place = places[0]
        forecast = await self.client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": place.latitude,
                "longitude": place.longitude,
                "daily": "temperature_2m_max,temperature_2m_min,weather_code",
                "temperature_unit": "fahrenheit",
                "timezone": "auto",
                "forecast_days": days,
            },
        )
        forecast.raise_for_status()
        daily = _Forecast.model_validate(forecast.json()).daily
        return {
            "place": ", ".join(p for p in (place.name, place.admin1, place.country) if p),
            "days": [
                {
                    "date": date,
                    "high_f": round(high),
                    "low_f": round(low),
                    "conditions": WMO_CODES.get(code, f"code {code}"),
                }
                for date, high, low, code in zip(
                    daily.time, daily.temperature_2m_max, daily.temperature_2m_min, daily.weather_code
                )
            ],
        }


class _ChartMeta(BaseModel):
    symbol: str
    currency: str | None = None
    regularMarketPrice: float | None = None


class _Quote(BaseModel):
    close: list[float | None]


class _Indicators(BaseModel):
    quote: list[_Quote]


class _ChartResult(BaseModel):
    meta: _ChartMeta
    timestamp: list[int]
    indicators: _Indicators


class _ChartBody(BaseModel):
    result: list[_ChartResult] | None = None


class _ChartResponse(BaseModel):
    chart: _ChartBody


STOCK_RANGES = ("5d", "1mo", "3mo", "6mo", "1y")


class StockTool(Tool):
    def __init__(self, client: httpx.AsyncClient) -> None:
        super().__init__(
            name="get_stock_history",
            description="Daily closing prices for a stock ticker over a range.",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Ticker, e.g. TSLA"},
                    "range": {"type": "string", "enum": list(STOCK_RANGES)},
                },
                "required": ["symbol"],
            },
        )
        self.client = client

    async def execute(self, *, sink: Any, args: dict[str, Any]) -> Any:
        symbol = str(args.get("symbol", "")).strip().upper()
        if not symbol.replace(".", "").replace("-", "").isalnum():
            raise ToolInputError("symbol must be a ticker like TSLA")
        span = str(args.get("range", "1mo"))
        if span not in STOCK_RANGES:
            span = "1mo"
        response = await self.client.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": span, "interval": "1d"},
            headers={"User-Agent": "Mozilla/5.0 (osmo symphony-server)"},
        )
        response.raise_for_status()
        results = _ChartResponse.model_validate(response.json()).chart.result
        if not results:
            raise ToolInputError(f"no data for {symbol}")
        result = results[0]
        closes = result.indicators.quote[0].close if result.indicators.quote else []
        points = [
            {"date": datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat(), "close": round(close, 2)}
            for ts, close in zip(result.timestamp, closes)
            if close is not None
        ]
        return {
            "symbol": result.meta.symbol,
            "currency": result.meta.currency,
            "last": result.meta.regularMarketPrice,
            "range": span,
            "closes": points,
        }
