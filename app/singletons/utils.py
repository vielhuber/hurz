import asyncio
import os
import pytz
import re
from functools import partial
from datetime import datetime, timezone, time as time2
from typing import Any

from app.utils.helpers import singleton
from app.utils.singletons import store


@singleton
class Utils:

    def create_folders(self) -> None:
        # create folders if not available
        for ordner in ["tmp", "data", "models"]:
            os.makedirs(ordner, exist_ok=True)

    def correct_string_to_datetime(self, string: str, format: str) -> datetime:
        return (
            pytz.timezone("Europe/Berlin")
            .localize(datetime.strptime(string, format))
            .astimezone(timezone.utc)
        )

    def correct_datetime_to_string(
        self, timestamp: float, format: str, shift: bool = False
    ) -> str:

        if shift is False:
            return (
                datetime.fromtimestamp(timestamp, tz=timezone.utc)
                .astimezone(pytz.timezone("Europe/Berlin"))
                .strftime(format)
            )
        else:
            return (
                pytz.timezone("Europe/Berlin")
                .localize(
                    datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(
                        tzinfo=None
                    )
                )
                .strftime(format)
            )

    def file_modified_before_minutes(self, filename: str) -> float:
        if not os.path.exists(filename):
            return False

        return (
            (datetime.now(timezone.utc))
            - (datetime.fromtimestamp(os.path.getmtime(filename), tz=timezone.utc))
        ).total_seconds() / 60

    def format_waehrung(self, name: str) -> str:
        # step 1: _ -> space
        name = name.replace("_", " ")
        # step 2: replace 6 consecutive uppercase letters with xxx/xxx
        name = re.sub(r"\b([A-Z]{3})([A-Z]{3})\b", r"\1/\2", name)
        return name

    async def run_sync_as_async(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(func, *args, **kwargs))

    def ist_wochenende(self, row: Any) -> bool:
        wd = row["Zeitpunkt"].weekday()
        t = row["Zeitpunkt"].time()
        if wd == 5 and t >= time2(1, 0):  # saturday from 01:00
            return True
        if wd == 6:  # whole sunday
            return True
        if wd == 0 and t < time2(1, 0):  # monday before 01:00
            return True
        return False

    def print(self, msg: str, verbosity_level: int) -> None:
        if verbosity_level == 0 or not hasattr(store, "verbosity_level"):
            print(msg)
        elif verbosity_level == 1 and store.verbosity_level >= 1:
            print(msg)
        elif verbosity_level == 2 and store.verbosity_level >= 2:
            print(msg)
        # append message also in log file
        with open("tmp/log.txt", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} - {msg}\n")

    def clear_console(self) -> None:
        os.system("cls" if os.name == "nt" else "clear")
