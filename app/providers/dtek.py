import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import StrEnum, auto
from zoneinfo import ZoneInfo

from aiocache import cached
from bs4 import BeautifulSoup
from playwright.async_api import Page
from quart import url_for

from . import Browser, BrowserJobBase, EventTitle, Group

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
    title: str = EventTitle.SCHEDULED


class EmergencyShutdown(Exception):
    pass


class BrowserJob(BrowserJobBase):
    WAIT_FUNCTION = "() => typeof DisconSchedule !== 'undefined' && DisconSchedule.fact"
    EVALUATE_FUNCTION = "() => DisconSchedule.fact"

    async def execute(self, page: Page):
        html = await page.content()

        bs = BeautifulSoup(html, "lxml")
        text = bs.get_text(separator=" ", strip=True)
        if "екстрені відключення" in text:
            raise EmergencyShutdown

        await page.wait_for_function(self.WAIT_FUNCTION)

        if fact := await page.evaluate(self.EVALUATE_FUNCTION):
            return fact["data"]

        raise ValueError("No shutdown schedule found")


class DtekShutdownBase:
    REGION: str
    NAME: str
    URL: str

    def __init__(self, browser):
        self.browser = browser

    async def _get(self):
        return await self.browser.execute(BrowserJob(self.URL))

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

    async def planned_outages(self):
        try:
            data = await self._get()
        except EmergencyShutdown:
            zone_info = ZoneInfo("Europe/Kyiv")
            today = datetime.combine(date.today(), time(), tzinfo=zone_info)
            after_tomorrow = today + timedelta(days=2)
            slot = Slot(
                dt_start=today,
                dt_end=after_tomorrow,
                title=EventTitle.EMERGENCY,
            )
            return {group: [slot] for group in Group}

        if not data:
            return {}

        slots = {}
        for timestamp, groups in data.items():
            dt = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
            for g, days in groups.items():
                group = GROUP_MAP[g]
                slots[group] = self._join_slots(
                    slots.get(group, []) + self._parse_group(dt, days)
                )

        return slots


class DemDtekShutdown(DtekShutdownBase):
    REGION = "Донеччина"
    NAME = "АТ «ДТЕК Донецькі електромережі»"
    URL = "https://www.dtek-dem.com.ua/ua/shutdowns"


class DnemDtekShutdown(DtekShutdownBase):
    REGION = "Дніпро"
    NAME = "АТ «ДТЕК Дніпровські електромережі»"
    URL = "https://www.dtek-dnem.com.ua/ua/shutdowns"


class KemDtekShutdown(DtekShutdownBase):
    REGION = "Київ"
    NAME = "ПрАТ «ДТЕК Київські електромережі»"
    URL = "https://www.dtek-kem.com.ua/ua/shutdowns"


class KremDtekShutdown(DtekShutdownBase):
    REGION = "Київщина"
    NAME = "ПрАТ «ДТЕК Київські регіональні електромережі»"
    URL = "https://www.dtek-krem.com.ua/ua/shutdowns"


class OemDtekShutdown(DtekShutdownBase):
    REGION = "Одеса"
    NAME = "АТ «ДТЕК Одеські електромережі»"
    URL = "https://www.dtek-oem.com.ua/ua/shutdowns"


class DtekNetwork(StrEnum):
    DEM = auto()
    DNEM = auto()
    KEM = auto()
    KREM = auto()
    OEM = auto()

    def link(self, group: str):
        return url_for("dtek", network=str(self), group=group)


class DtekShutdowns:
    def __init__(self, browser: Browser, cache_kwargs: dict | None = None):
        self.browser = browser

        self.map = {
            DtekNetwork.KEM: KemDtekShutdown(self.browser),
            DtekNetwork.KREM: KremDtekShutdown(self.browser),
            # DtekNetwork.DEM: DemDtekShutdown(self.browser),
            DtekNetwork.DNEM: DnemDtekShutdown(self.browser),
            DtekNetwork.OEM: OemDtekShutdown(self.browser),
        }
        if cache_kwargs:
            self.planned_outages = cached(
                ttl=300,
                noself=True,
                **cache_kwargs,
            )(self.planned_outages)

    async def planned_outages(self, network: DtekNetwork):
        network = self.map[network]
        return await network.planned_outages()

    def networks(self):
        networks = defaultdict(dict)
        for network, shutdown in self.map.items():
            networks[shutdown.REGION] = {
                shutdown.NAME: {group.value: network.link(group) for group in Group}
            }
        return dict(networks)


if __name__ == "__main__":
    from pprint import pprint

    browser = Browser()
    dtek = DtekShutdowns(browser)
    loop = asyncio.new_event_loop()
    loop.create_task(dtek.browser.run())
    pprint(loop.run_until_complete(dtek.planned_outages(DtekNetwork.KEM)))
