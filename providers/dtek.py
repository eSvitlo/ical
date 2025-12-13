import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum, auto

from flask_caching import Cache
from playwright.sync_api import sync_playwright

from providers import Group

GROUP_MAP = {
    "GPV1.1": Group.G1_1,
    "GPV1.2": Group.G1_2,
    "GPV2.1": Group.G2_1,
    "GPV2.2": Group.G2_2,
    "GPV3.1": Group.G3_1,
    "GPV3.2": Group.G3_2,
    "GPV4.1": Group.G4_1,
    "GPV4.2": Group.G4_2,
    "GPV5.1": Group.G5_1,
    "GPV5.2": Group.G5_2,
    "GPV6.1": Group.G6_1,
    "GPV6.2": Group.G6_2,
}


class State(StrEnum):
    NO = auto()
    YES = auto()
    FIRST = auto()
    SECOND = auto()


@dataclass
class Slot:
    dt_start: datetime
    dt_end: datetime
    title: str = "Заплановане відключення світла"



class DtekShutdownBase:
    URL: str
    PATTERN: re.Pattern = re.compile(r"DisconSchedule\.fact\s*=\s*(\{.*})")

    def __init__(self, dtek):
        self.dtek = dtek

    def _get(self):
        with self.dtek.context.new_page() as page:
            page.goto(self.URL)
            html = page.content()

        if match:= self.PATTERN.search(html):
            data = match.group(1)
            return json.loads(data)["data"]

        return {}

    @staticmethod
    def _parse_group(dt: datetime, data) -> list[Slot]:
        slots = []

        for hour, state in data.items():
            hours = int(hour) - 1
            if state == State.NO:
                start = dt + timedelta(hours=hours)
                end = dt + timedelta(hours=hours, minutes=60)
            elif state == State.FIRST:
                start = dt + timedelta(hours=hours)
                end = dt + timedelta(hours=hours, minutes=30)
            elif state == State.SECOND:
                start = dt + timedelta(hours=hours, minutes=30)
                end = dt + timedelta(hours=hours, minutes=60)
            else:
                continue

            slots.append(Slot(dt_start=start, dt_end=end))

        return slots

    @staticmethod
    def _join_slots(slots: list[Slot]) -> list[Slot]:
        if not slots:
            return []

        joined = [slots[0]]

        for slot in slots[1:]:
            prev = joined[-1]

            if slot.dt_start == prev.dt_end:
                joined[-1] = Slot(dt_start=prev.dt_start, dt_end=slot.dt_end)
            else:
                joined.append(slot)

        return joined

    def planned_outages(self):
        data = self._get()
        if not data:
            return {}

        slots = {}
        for date, groups in data.items():
            dt = datetime.fromtimestamp(int(date), tz=timezone.utc)
            for g, days in groups.items():
                group = GROUP_MAP[g]
                slots[group] = self._join_slots(slots.get(group, []) + self._parse_group(dt, days))

        return slots


class DemDtekShutdown(DtekShutdownBase):
    URL = "https://www.dtek-dem.com.ua/ua/shutdowns"


class DnemDtekShutdown(DtekShutdownBase):
    URL = "https://www.dtek-dnem.com.ua/ua/shutdowns"


class KemDtekShutdown(DtekShutdownBase):
    URL = "https://www.dtek-kem.com.ua/ua/shutdowns"


class KremDtekShutdown(DtekShutdownBase):
    URL = "https://www.dtek-krem.com.ua/ua/shutdowns"


class OemDtekShutdown(DtekShutdownBase):
    URL = "https://www.dtek-oem.com.ua/ua/shutdowns"


class DtekNetwork(StrEnum):
    DEM = auto()
    DNEM = auto()
    KEM = auto()
    KREM = auto()
    OEM = auto()


class DtekShutdowns:
    def __init__(self, cache: Cache | None = None):
        self.map = {
            DtekNetwork.DEM: DemDtekShutdown(self),
            DtekNetwork.DNEM: DnemDtekShutdown(self),
            DtekNetwork.KEM: KemDtekShutdown(self),
            DtekNetwork.KREM: KremDtekShutdown(self),
            DtekNetwork.OEM: OemDtekShutdown(self),
        }
        if cache:
            self.planned_outages = cache.memoize(timeout=300, args_to_ignore=["self"])(self.planned_outages)
        self._pw = None
        self._browser = None
        self._context = None

    @property
    def context(self):
        if self._context:
            return self._context

        self._pw = sync_playwright()
        pw = self._pw.__enter__()
        self._browser = pw.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-sync",
                "--disable-translate",
                "--disable-features=site-per-process",
            ]
        )
        self._context = self._browser.new_context()

        def block(route):
            if route.request.resource_type in {"font", "image", "media", "stylesheet"}:
                route.abort()
            else:
                route.continue_()

        self._context.route("**/*", block)

        return self._context

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._context:
            self._context.close()
            self._browser.close()
            self._pw.__exit__()

            self._context = None
            self._browser = None
            self._pw = None

    def planned_outages(self, network: DtekNetwork):
        network = self.map.get(network)
        return network.planned_outages()


if __name__ == "__main__":
    from pprint import pprint

    with DtekShutdowns() as dtek:
        pprint(dtek.planned_outages(DtekNetwork.KEM))
