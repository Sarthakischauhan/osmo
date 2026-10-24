from __future__ import annotations

import httpx
import pytest

from symphony_server.tools import StockTool, ToolInputError, WeatherTool


def weather_api(request: httpx.Request) -> httpx.Response:
    if request.url.host == "geocoding-api.open-meteo.com":
        assert request.url.params["name"] == "Palo Alto"
        return httpx.Response(
            200,
            json={"results": [{"name": "Palo Alto", "latitude": 37.44, "longitude": -122.14, "admin1": "California", "country": "United States"}]},
        )
    assert request.url.host == "api.open-meteo.com"
    assert request.url.params["temperature_unit"] == "fahrenheit"
    return httpx.Response(
        200,
        json={
            "daily": {
                "time": ["2026-10-24", "2026-10-25"],
                "temperature_2m_max": [72.4, 67.6],
                "temperature_2m_min": [51.0, 50.2],
                "weather_code": [0, 2],
            }
        },
    )


async def test_weather_tool_geocodes_then_forecasts() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(weather_api)) as client:
        result = await WeatherTool(client).execute(sink=None, args={"location": "Palo Alto", "days": 2})
    assert result == {
        "place": "Palo Alto, California, United States",
        "days": [
            {"date": "2026-10-24", "high_f": 72, "low_f": 51, "conditions": "clear"},
            {"date": "2026-10-25", "high_f": 68, "low_f": 50, "conditions": "partly cloudy"},
        ],
    }


def stock_api(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/v8/finance/chart/TSLA"
    return httpx.Response(
        200,
        json={
            "chart": {
                "result": [
                    {
                        "meta": {"symbol": "TSLA", "currency": "USD", "regularMarketPrice": 251.3},
                        "timestamp": [1792540800, 1792627200, 1792713600],
                        "indicators": {"quote": [{"close": [240.123, None, 251.3]}]},
                    }
                ]
            }
        },
    )


async def test_stock_tool_drops_gaps_and_dates_points() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(stock_api)) as client:
        result = await StockTool(client).execute(sink=None, args={"symbol": "tsla", "range": "1mo"})
    assert result["symbol"] == "TSLA"
    assert result["closes"] == [{"date": "2026-10-21", "close": 240.12}, {"date": "2026-10-23", "close": 251.3}]


async def test_stock_tool_rejects_junk_symbols() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(stock_api)) as client:
        with pytest.raises(ToolInputError):
            await StockTool(client).execute(sink=None, args={"symbol": "../etc"})
