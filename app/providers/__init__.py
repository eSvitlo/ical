from abc import ABC, abstractmethod
from asyncio import (
    CancelledError,
    Future,
    Lock,
    Queue,
    QueueShutDown,
    create_task,
    sleep,
)
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from playwright.async_api import Browser as PlaywrightBrowser
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, Playwright, async_playwright


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


class EventTitle(StrEnum):
    SCHEDULED = "Заплановане відключення світла"
    EMERGENCY = "🚨 Екстрені відключення світла"


class Slots(Protocol):
    title: str
    dt_start: datetime
    dt_end: datetime


@dataclass
class BrowserJobBase(ABC):
    url: str
    _future: Future = field(init=False, default_factory=Future)

    @abstractmethod
    async def execute(self, page: Page) -> Any:
        raise NotImplementedError

    @property
    def result(self):
        return self._future.result()

    @result.setter
    def result(self, value):
        self._future.set_result(value)

    @property
    def exception(self):
        return self._future.exception()

    @exception.setter
    def exception(self, value):
        self._future.set_exception(value)

    def __await__(self):
        return self._future.__await__()


class Browser:
    def __init__(self, max_inactivity=None, max_requests=None):
        self.max_inactivity = max_inactivity or 30
        self.max_requests = max_requests or 50
        self._task_queue = Queue()
        self._browser: PlaywrightBrowser | None = None
        self._browser_lock = Lock()
        self._requests = 0
        self._restart_task = None

    async def _restart(self, browser):
        await sleep(self.max_inactivity)

        async with self._browser_lock:
            if self._browser is browser:
                with suppress(Exception):
                    await self._browser.close()
                self._browser = None

    def schedule_restart(self):
        if self._restart_task:
            self._restart_task.cancel()
        self._restart_task = create_task(self._restart(self._browser))

    async def browser(self, playwright) -> PlaywrightBrowser:
        if self._browser is not None:
            if not self._browser.is_connected() or self._requests >= self.max_requests:
                with suppress(Exception):
                    await self._browser.close()
                self._browser = None

        if self._browser is None:
            self._browser = await playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-background-networking",
                    "--disable-background-timer-throttling",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-breakpad",
                    "--disable-gpu",
                    "--disable-hang-monitor",
                    "--disable-renderer-backgrounding",
                    "--disable-sync",
                    "--disable-extensions",
                    "--disable-default-apps",
                    "--mute-audio",
                ],
            )
            self._requests = 0

        self.schedule_restart()
        return self._browser

    async def run(self):
        while True:
            playwright = await async_playwright().start()
            try:
                await self._run(playwright)
            except CancelledError:
                break
            except Exception:
                pass
            finally:
                if self._restart_task:
                    self._restart_task.cancel()
                    with suppress(CancelledError):
                        await self._restart_task

                if self._browser:
                    with suppress(Exception):
                        await self._browser.close()
                    self._browser = None

                await playwright.stop()

    async def _run(self, playwright: Playwright):
        async def block(route):
            if route.request.resource_type in {
                "font",
                "image",
                "media",
                "stylesheet",
            }:
                await route.abort()
            else:
                await route.continue_()

        while True:
            try:
                job = await self._task_queue.get()
            except QueueShutDown:
                self._task_queue.task_done()
                raise CancelledError

            try:
                async with self._browser_lock:
                    browser = await self.browser(playwright)
                    async with await browser.new_context() as context:
                        await context.route("**/*", block)

                        async with await context.new_page() as page:
                            response = await page.goto(
                                job.url,
                                wait_until="domcontentloaded",
                            )
                            if not response.ok:
                                raise ConnectionError(response.status_text)

                            result = await job.execute(page)
                            job.result = result

            except PlaywrightError as e:
                job.exception = e
                break

            except Exception as e:
                job.exception = e

            finally:
                self._requests += 1
                self._task_queue.task_done()

    async def execute(self, job):
        self._task_queue.put_nowait(job)
        await job
        return job.result

    async def shutdown(self):
        self._task_queue.shutdown()
        await self._task_queue.join()
