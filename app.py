import asyncio
import logging
import os
from contextlib import suppress
from datetime import datetime
from typing import Protocol

from aiocache import Cache, cached
from aiocache.serializers import PickleSerializer
from aiohttp import ClientSession
from icalendar import Calendar, Event
from quart import (
    Quart,
    Response,
    render_template,
    request,
    send_from_directory,
)
from redis.connection import parse_url

from gcal import get_gcals
from logger import HealthCheckFilter
from providers import Browser, Group
from providers.dtek import DtekNetwork, DtekShutdowns
from providers.yasno import YasnoBlackout

logging.getLogger("hypercorn.access").addFilter(HealthCheckFilter())


app = Quart(__name__)
app.json.ensure_ascii = False

app.add_url_rule(
    "/healthz",
    endpoint="health_check",
    view_func=lambda: "",
)


if redis_url := os.getenv("REDIS_URL"):
    cache_kwargs = {
        "cache": Cache.REDIS,
        **parse_url(redis_url),
        "serializer": PickleSerializer(),
    }
    cache_kwargs["endpoint"] = cache_kwargs.pop("host")
else:
    cache_kwargs = {"cache": Cache.MEMORY}

BROWSER_MAX_INACTIVITY = os.getenv("BROWSER_MAX_INACTIVITY")
BROWSER_MAX_REQUESTS = os.getenv("BROWSER_MAX_REQUESTS")

yasno_blackout = YasnoBlackout()
browser = Browser(
    max_inactivity=BROWSER_MAX_INACTIVITY,
    max_requests=BROWSER_MAX_REQUESTS,
)
dtek_shutdowns = DtekShutdowns(browser, cache_kwargs)


class Slots(Protocol):
    title: str
    dt_start: datetime
    dt_end: datetime


@app.route("/favicon.ico")
@app.route("/robots.txt")
async def static_root() -> Response:
    return await send_from_directory(app.static_folder, request.path.lstrip("/"))


def response_filter(response: Response) -> bool:
    return response.status_code != 200


@app.route("/")
@cached(ttl=3600, skip_cache_func=response_filter, **cache_kwargs)
async def index() -> Response:
    try:
        regions = await yasno_blackout.regions()
        gcals = await get_gcals()
    except TimeoutError:
        return Response(status=504)
    except (IOError, KeyError, TypeError) as e:
        app.logger.exception(e)
        return Response(status=204)

    data = {
        region.value: {
            dso.name: {group.value: dso.link(group) for group in Group}
            for dso in region.dsos
        }
        for region in regions
    }
    return Response(await render_template("index.html", data=data, gcals=gcals))


@app.route("/yasno/<int:region>/<int:dso>/<string:group>.ics")
@cached(ttl=60, skip_cache_func=response_filter, **cache_kwargs)
async def yasno(region: int, dso: int, group: str) -> Response:
    try:
        planned_outages = await yasno_blackout.planned_outages(
            region_id=region, dso_id=dso
        )
        slots = planned_outages[group]
    except TimeoutError:
        return Response(status=504)
    except (IOError, KeyError, TypeError) as e:
        app.logger.exception(e)
        return Response(status=204)

    return create_calendar("Yasno Blackout", group, slots)


@app.route("/dtek/<string:network>/<string:group>.ics")
@cached(ttl=60, skip_cache_func=response_filter, **cache_kwargs)
async def dtek(network: str, group: str) -> Response:
    try:
        network = DtekNetwork(network)
        planned_outages = await dtek_shutdowns.planned_outages(network=network)
        slots = planned_outages[group]
    except TimeoutError:
        return Response(status=504)
    except (IOError, KeyError, ValueError) as e:
        app.logger.exception(e)
        return Response(status=204)

    return create_calendar("DTEK Shutdowns", group, slots)


def create_calendar(name: str, group: str, slots: list[Slots]) -> Response:
    cal = Calendar()
    cal.add("prodid", f"-//eSvitlo//{name} Calendar//UK")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", f"Світло (група {group})")
    cal.add("x-wr-timezone", "Europe/Kyiv")
    cal.add("x-published-ttl", "PT1H")
    cal.add("refresh-interval;value=duration", "PT1H")

    for slot in slots:
        event = Event()
        event.add("summary", slot.title)
        event.add("dtstart", slot.dt_start)
        event.add("dtend", slot.dt_end)

        cal.add_component(event)

    return Response(cal.to_ical(), mimetype="text/calendar")


@app.before_serving
async def startup():
    @app.add_background_task
    async def refresh_index_cache():
        async with app.test_request_context("/"):
            while True:
                with suppress(Exception):
                    await index()
                await asyncio.sleep(60)

    @app.add_background_task
    async def refresh_dtek_cache():
        while True:
            with suppress(Exception):
                for network in dtek_shutdowns.map:
                    await dtek_shutdowns.planned_outages(network=network)
            await asyncio.sleep(60)

    if public_healthcheck_endpoint := os.getenv("PUBLIC_HEALTHCHECK_ENDPOINT"):

        @app.add_background_task
        async def spin_up():
            async with ClientSession() as session:
                while True:
                    with suppress(Exception):
                        await session.get(public_healthcheck_endpoint)
                    await asyncio.sleep(60)

    app.add_background_task(browser.run)


@app.after_serving
async def shutdown():
    browser.shutdown()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080, debug=True)
