"""Private reviewed Asia/Karachi schedule and provisional slot calculation."""
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from .hospital_search import SearchModel

ZONE = "Asia/Karachi"
LOCAL = ZoneInfo(ZONE)


class SessionHours(SearchModel):
    start: time
    end: time
    minutes: Annotated[int, Field(ge=5, le=120)]

    @model_validator(mode="after")
    def validate_interval(self):
        if (self.start.tzinfo or self.end.tzinfo or self.start.second or self.end.second
                or self.start.microsecond or self.end.microsecond or self.start >= self.end
                or datetime.combine(date.min, self.end) - datetime.combine(date.min, self.start)
                < timedelta(minutes=self.minutes)):
            raise ValueError("Invalid local session interval")
        return self


class WeeklySession(SessionHours):
    weekday: Annotated[int, Field(ge=0, le=6)]


class ScheduleException(SearchModel):
    start_date: date
    end_date: date
    action: Literal["leave", "holiday", "closure", "cancelled", "suspended", "replacement"]
    sessions: Annotated[tuple[SessionHours, ...], Field(max_length=8)] = ()

    @model_validator(mode="after")
    def validate_exception(self):
        if self.end_date < self.start_date:
            raise ValueError("Invalid exception date range")
        if self.action == "replacement":
            if self.start_date != self.end_date or not self.sessions:
                raise ValueError("Replacement requires one day and affirmative sessions")
        elif self.sessions:
            raise ValueError("Closure cannot specify working hours")
        nonoverlapping(self.sessions)
        return self


def nonoverlapping(sessions):
    ordered = sorted(sessions, key=lambda item: item.start)
    if any(previous.end > current.start for previous, current in zip(ordered, ordered[1:])):
        raise ValueError("Overlapping sessions")


class Schedule(SearchModel):
    timezone: Literal["Asia/Karachi"]
    effective_from: date
    effective_until: date
    weekly: Annotated[tuple[WeeklySession, ...], Field(max_length=32)] = ()
    exceptions: Annotated[tuple[ScheduleException, ...], Field(max_length=32)] = ()

    @model_validator(mode="after")
    def validate_sessions(self):
        if not timedelta(0) <= self.effective_until - self.effective_from <= timedelta(days=180):
            raise ValueError("Invalid schedule effective range")
        for weekday in range(7):
            nonoverlapping(session for session in self.weekly if session.weekday == weekday)
        previous = None
        for exception in sorted(self.exceptions, key=lambda item: item.start_date):
            if (not self.effective_from <= exception.start_date <= exception.end_date <= self.effective_until
                    or previous is not None and previous >= exception.start_date):
                raise ValueError("Conflicting schedule exceptions")
            previous = exception.end_date
        return self

    def sessions_for(self, day: date):
        if not self.effective_from <= day <= self.effective_until:
            return ()
        for exception in self.exceptions:
            if exception.start_date <= day <= exception.end_date:
                return tuple(sorted(exception.sessions, key=lambda item: item.start))
        return tuple(sorted(
            (session for session in self.weekly if session.weekday == day.weekday()), key=lambda item: item.start))


class ProvisionalSlot(SearchModel):
    start_utc: datetime
    end_utc: datetime
    start_local: datetime
    end_local: datetime
    minutes: int
    status: Literal["provisional_unoccupied"] = "provisional_unoccupied"


def local_window(day: date, now: datetime) -> bool:
    if now.utcoffset() is None:
        raise ValueError("Clock must be timezone aware")
    return 0 <= (day - now.astimezone(LOCAL).date()).days <= 30


def provisional_slots(schedule: Schedule, day: date, now: datetime, lead_time: timedelta):
    if not local_window(day, now):
        return ()
    slots = []
    for session in schedule.sessions_for(day):
        start = datetime.combine(day, session.start, tzinfo=LOCAL)
        end = datetime.combine(day, session.end, tzinfo=LOCAL)
        step = timedelta(minutes=session.minutes)
        while start + step <= end:
            finish = start + step
            if start > now and start >= now + lead_time:
                slots.append(ProvisionalSlot(start_utc=start.astimezone(UTC), end_utc=finish.astimezone(UTC),
                                             start_local=start, end_local=finish, minutes=session.minutes))
            start = finish
    return tuple(slots)
