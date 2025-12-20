import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
from enum import StrEnum

from aiohttp import ClientSession
from pydantic import BaseModel, TypeAdapter
from quart import url_for

from . import Group


class Dso(BaseModel):
    id: int
    name: str
    region: "Region" = None

    def link(self, group: Group) -> str:
        return url_for("yasno", region=self.region.id, dso=self.id, group=group)


class Region(BaseModel):
    id: int
    value: str
    dsos: list[Dso]

    def set_region(self):
        for dso in self.dsos:
            dso.region = self
        return self


class SlotType(StrEnum):
    DEFINITE = "Definite"
    NOT_PLANNED = "NotPlanned"


class DayName(StrEnum):
    TODAY = "today"
    TOMORROW = "tomorrow"
    MONDAY = "0"
    TUESDAY = "1"
    WEDNESDAY = "2"
    THURSDAY = "3"
    FRIDAY = "4"
    SATURDAY = "5"
    SUNDAY = "6"


class DayStatus(StrEnum):
    SCHEDULE_APPLIES = "ScheduleApplies"
    WAITING_FOR_SCHEDULE = "WaitingForSchedule"
    EMERGENCY_SHUTDOWNS = "EmergencyShutdowns"


class Slot(BaseModel):
    start: int
    end: int
    type: SlotType = SlotType.DEFINITE
    date_start: datetime = None
    date_end: datetime = None
    day_status: DayStatus = None

    @property
    def dt_start(self) -> datetime:
        return self.date_start + timedelta(minutes=self.start)

    @property
    def dt_end(self) -> datetime:
        return self.date_end + timedelta(minutes=self.end)

    @property
    def title(self) -> str:
        match self.day_status:
            case DayStatus.SCHEDULE_APPLIES:
                return "Заплановане відключення світла"
            case DayStatus.EMERGENCY_SHUTDOWNS:
                return "🚨 Екстрені відключення світла"
            case DayStatus.WAITING_FOR_SCHEDULE:
                return "Імовірне відключення світла"


class Day(BaseModel):
    slots: list[Slot]
    date: datetime
    status: DayStatus | None = None

    def get_slots(self) -> list[Slot]:
        match self.status:
            case DayStatus.SCHEDULE_APPLIES | DayStatus.WAITING_FOR_SCHEDULE:
                for slot in self.slots:
                    slot.date_start = slot.date_end = self.date
                    slot.day_status = self.status
            case DayStatus.EMERGENCY_SHUTDOWNS:
                slot = Slot(
                    start=0,
                    end=1440,
                    date_start=self.date,
                    date_end=self.date,
                    day_status=self.status,
                )
                self.slots = [slot]
        return [
            slot
            for slot in self.slots
            if slot.type == SlotType.DEFINITE
            and slot.day_status != DayStatus.WAITING_FOR_SCHEDULE
        ]


class YasnoBlackout:
    URL = "https://app.yasno.ua/api/blackout-service/public/shutdowns"

    _REGIONS_TA = TypeAdapter(list[Region])
    _DAY_TA = TypeAdapter(Day)

    async def _get(self, *path, **params):
        url = "/".join(map(str, (self.URL, *path)))
        async with ClientSession() as session:
            async with session.get(url, params=params) as response:
                return await response.json()

    async def regions(self) -> list[Region]:
        return [
            region.set_region()
            for region in self._REGIONS_TA.validate_python(
                await self._get("addresses/v2/regions")
            )
        ]

    async def planned_outages(self, region_id: int, dso_id: int):
        result = await self._get(
            "regions", region_id, "dsos", dso_id, "planned-outages"
        )

        groups: dict[Group, list[Slot]] = defaultdict(list)
        for group_id, day_data in result.items():
            for day_name in DayName:
                if day_name in day_data:
                    day_slots = self._DAY_TA.validate_python(
                        day_data[day_name]
                    ).get_slots()
                    slots = day_slots[:]
                    if groups[Group(group_id)] and slots:
                        last_slot = groups[Group(group_id)][-1]
                        next_slot = slots[0]
                        if (
                            last_slot.dt_end == next_slot.dt_start
                            and last_slot.type == next_slot.type
                            and last_slot.day_status == next_slot.day_status
                        ):
                            joined_slot = Slot(
                                start=last_slot.start,
                                end=next_slot.end,
                                date_start=last_slot.date_start,
                                date_end=next_slot.date_end,
                                day_status=last_slot.day_status,
                            )
                            groups[Group(group_id)] = groups[Group(group_id)][:-1]
                            slots = [joined_slot, *day_slots[1:]]
                    groups[Group(group_id)].extend(slots)

        return dict(groups)


if __name__ == "__main__":
    from pprint import pprint

    yb = YasnoBlackout()
    pprint(asyncio.run(yb.planned_outages(region_id=25, dso_id=902)))
