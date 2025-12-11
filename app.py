import os
from datetime import datetime
from typing import Protocol

import requests
from flask import Flask, Response, render_template, url_for
from flask_apscheduler import APScheduler
from flask_caching import Cache
from icalendar import Calendar, Event

from gcal import get_gcals
from providers.dtek import DtekNetwork, DtekShutdowns
from providers.yasno import YasnoBlackout
from providers import Group

app = Flask(__name__)
app.json.ensure_ascii = False
app.add_url_rule(
    "/favicon.ico",
    endpoint="favicon",
    redirect_to=lambda _: url_for("static", filename="favicon.ico"),
)
app.add_url_rule(
    "/healthz",
    endpoint="health_check",
    view_func=lambda: "",
)

config = {
    "CACHE_TYPE": "RedisCache" if os.getenv("REDIS_URL") else "FileSystemCache",
    "CACHE_DIR": "/tmp/ical",
    "CACHE_REDIS_URL": os.getenv("REDIS_URL"),
}
app.config.from_mapping(config)
cache = Cache(app)

scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()

yasno_blackout = YasnoBlackout()
dtek_shutdowns = DtekShutdowns()


class Slots(Protocol):
    title: str
    dt_start: datetime
    dt_end: datetime


def response_filter(response: Response) -> bool:
    return response.status_code == 200


@app.route("/")
@cache.cached(timeout=3600, key_prefix="index", response_filter=response_filter)
def index() -> Response:
    try:
        regions = yasno_blackout.regions()
        gcals = get_gcals()
    except (IOError, KeyError, TypeError) as e:
        app.logger.exception(e)
        return Response("", 204)

    data = {
        region.value: {
            dso.name: {
                group.value: dso.link(group) for group in Group
            } for dso in region.dsos
        } for region in regions
    }
    return Response(render_template("index.html", data=data, gcals=gcals))


@app.route('/yasno/<int:region>/<int:dso>/<string:group>.ics')
@cache.cached(timeout=60, response_filter=response_filter)
def yasno(region: int, dso: int, group: str) -> Response:
    try:
        planned_outages = yasno_blackout.planned_outages(region_id=region, dso_id=dso)
        slots = planned_outages[group]
    except (IOError, KeyError, TypeError) as e:
        app.logger.exception(e)
        return Response("", 404)

    return create_calendar(group, slots)


@app.route('/dtek/<string:network>/<string:group>.ics')
@cache.cached(timeout=60, response_filter=response_filter)
def dtek(network: DtekNetwork, group: str) -> Response:
    try:
        planned_outages = dtek_shutdowns.planned_outages(network=network)
        slots = planned_outages[group]
    except (IOError, KeyError, TypeError) as e:
        app.logger.exception(e)
        return Response("", 404)

    return create_calendar(group, slots)


def create_calendar(group: str, slots: list[Slots]) -> Response:
    cal = Calendar()
    cal.add("prodid", f"-//eSvitlo//Yasno Blackout Calendar//UK")
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


@scheduler.task("interval", minutes=1, next_run_time=datetime.now())
def refresh_index_cache():
    with app.test_request_context():
        index()


if url := os.getenv("PUBLIC_HEALTHCHECK_ENDPOINT"):
    @scheduler.task("interval", minutes=1)
    def spin_up():
        requests.get(url)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080, debug=True)
