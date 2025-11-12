from flask import Flask, Response, render_template, request, url_for
from flask import Flask, Response, render_template, url_for
from flask_caching import Cache
from icalendar import Calendar, Event

from providers.yasno import Group, SlotType, YasnoBlackout

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
    "CACHE_TYPE": "SimpleCache",
}
app.config.from_mapping(config)
cache = Cache(app)

yasno_blackout = YasnoBlackout()


@app.route("/")
@cache.cached(timeout=300)
def index() -> str | Response:
    try:
        regions = yasno_blackout.regions()
    except (IOError, KeyError, TypeError):
        return Response("", 204)

    data = {
        region.value: {
            dso.name: {
                group.value: dso.link(group) for group in Group
            } for dso in region.dsos
        } for region in regions
    }
    return render_template("index.html", data=data)


@app.route('/yasno/<int:region>/<int:dso>/<string:group>.ics')
@cache.cached(timeout=60)
def yasno(region: int, dso: int, group: str) -> Response:
    try:
        planned_outages = yasno_blackout.planned_outages(region_id=region, dso_id=dso)
        data = planned_outages[group]
    except (IOError, KeyError, TypeError):
        return Response("", 404)

    cal = Calendar()
    cal.add("prodid", f"-//eSvitlo//Yasno Blackout Calendar//UK")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", f"Світло (група {group})")
    cal.add("x-wr-timezone", "Europe/Kyiv")

    for slot in data:
        if slot.type is SlotType.DEFINITE:
            event = Event()
            event.add("summary", "Відсутність світла")
            event.add("dtstart", slot.dt_start)
            event.add("dtend", slot.dt_end)

            cal.add_component(event)

    return Response(cal.to_ical(), mimetype="text/calendar")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080, debug=True)
