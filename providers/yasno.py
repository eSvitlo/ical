from collections import defaultdict
from datetime import datetime, timedelta
from enum import StrEnum

import requests
from flask import url_for
from pydantic import BaseModel, TypeAdapter


class Group(StrEnum):
    G1_1 = "1.1"
    G1_2 = "1.2"
    G2_1 = "2.1"
    G2_2 = "2.2"
    G3_1 = "3.1"
    G3_2 = "3.2"
    G4_1 = "4.1"
    G4_2 = "4.2"
    G5_1 = "5.1"
    G5_2 = "5.2"
    G6_1 = "6.1"
    G6_2 = "6.2"


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
    title: str = "Відсутність світла"
    date_start: datetime = None
    date_end: datetime = None

    @property
    def dt_start(self) -> datetime:
        return self.date_start + timedelta(minutes=self.start)

    @property
    def dt_end(self) -> datetime:
        return self.date_end + timedelta(minutes=self.end)


class Day(BaseModel):
    slots: list[Slot]
    date: datetime
    status: DayStatus | None = None

    def update_dt(self):
        for slot in self.slots:
            slot.date_start = slot.date_end = self.date
        return self



class YasnoBlackout:
    URL = "https://app.yasno.ua/api/blackout-service/public/shutdowns"

    _REGIONS_TA = TypeAdapter(list[Region])
    _DAY_TA = TypeAdapter(Day)

    def _get(self, *path, **params):
        url = "/".join(map(str, (self.URL, *path)))
        return requests.get(url=url, params=params).json()

    def regions(self) -> list[Region]:
        return [region.set_region() for region in self._REGIONS_TA.validate_python(self._get("addresses/v2/regions"))]

    def planned_outages(self, region_id: int, dso_id: int):
        result = self._get("regions", region_id, "dsos", dso_id, "planned-outages")

        groups: dict[Group, list[Slot]] = defaultdict(list)
        for group_id, day_data in result.items():
            for day_name in DayName:
                if day_name in day_data:
                    day = self._DAY_TA.validate_python(day_data[day_name]).update_dt()
                    if day.status is DayStatus.SCHEDULE_APPLIES:
                        slots = day.slots
                        if groups[Group(group_id)] and slots:
                            last_slot = groups[Group(group_id)][-1]
                            next_slot = slots[0]
                            if last_slot.dt_end == next_slot.dt_start and last_slot.type == next_slot.type:
                                joined_slot = Slot(
                                    start=last_slot.start,
                                    end=next_slot.end,
                                    type=last_slot.type,
                                    date_start=last_slot.date_start,
                                    date_end=next_slot.date_end,
                                )
                                groups[Group(group_id)] = groups[Group(group_id)][:-1]
                                slots = [joined_slot, *day.slots[1:]]
                        groups[Group(group_id)].extend(slots)
                    elif day.status is DayStatus.EMERGENCY_SHUTDOWNS:
                        if day.date < datetime.now(tz=day.date.tzinfo):
                            slot = Slot(start=0, end=1440, title="🚨 Екстрені відключення")
                            day = Day(slots=[slot], date=day.date).update_dt()
                            groups[Group(group_id)].extend(day.slots)

        return dict(groups)

if __name__ == "__main__":
    yb = YasnoBlackout()
    print(yb.planned_outages(region_id=25, dso_id=902))
