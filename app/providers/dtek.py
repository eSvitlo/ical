import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum, auto

from aiocache import cached

from . import Browser, Group

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

    def __init__(self, browser):
        self.browser = browser

    async def _get(self):
        html = await self.browser.get(self.URL)

        if match := self.PATTERN.search(html):
            data = match.group(1)
            return json.loads(data)["data"]

        raise ValueError("No shutdown schedule found")

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
        data = await self._get()
        if not data:
            return {}

        slots = {}
        for date, groups in data.items():
            dt = datetime.fromtimestamp(int(date), tz=timezone.utc)
            for g, days in groups.items():
                group = GROUP_MAP[g]
                slots[group] = self._join_slots(
                    slots.get(group, []) + self._parse_group(dt, days)
                )

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
    def __init__(self, browser: Browser, cache_kwargs: dict | None = None):
        self.browser = browser

        self.map = {
            # DtekNetwork.DEM: DemDtekShutdown(self.browser),
            DtekNetwork.DNEM: DnemDtekShutdown(self.browser),
            DtekNetwork.KEM: KemDtekShutdown(self.browser),
            DtekNetwork.KREM: KremDtekShutdown(self.browser),
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


if __name__ == "__main__":
    from pprint import pprint

    browser = Browser()
    dtek = DtekShutdowns(browser)
    loop = asyncio.new_event_loop()
    loop.create_task(dtek.browser.run())
    pprint(loop.run_until_complete(dtek.planned_outages(DtekNetwork.KEM)))
