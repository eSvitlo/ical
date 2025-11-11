from flask import Flask, Response, render_template, request
from google.appengine.api import wrap_wsgi_app
from icalendar import Calendar, Event

from cache import cache_route
from yasno import Group, SlotType, YasnoBlackout

app = Flask(__name__)
app.json.ensure_ascii = False
app.wsgi_app = wrap_wsgi_app(app.wsgi_app)

yasno_blackout = YasnoBlackout()


@cache_route(timeout=3600)
@app.route("/")
def index() -> str:
    regions = yasno_blackout.regions()
    data = {
        region.value: {
            dso.name: {
                group.value: dso.link(request.host_url, group) for group in Group
            } for dso in region.dsos
        } for region in regions
    }
    return render_template("index.html", data=data)


@cache_route()
@app.route('/ical/<int:region>/<int:dso>/<string:group>.ics')
def ical(region: int, dso: int, group: str) -> Response:
    planned_outages = yasno_blackout.planned_outages(region_id=region, dso_id=dso)
    data = planned_outages[group]

    cal = Calendar()
    for slot in data:
        if slot.type is SlotType.DEFINITE:
            event = Event()
            event.add('summary', "Відсутність світла")
            event.add("dtstart", slot.dt_start)
            event.add("dtend", slot.dt_end)

            cal.add_component(event)

    return Response(cal.to_ical(), mimetype="text/calendar")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080, debug=True)
