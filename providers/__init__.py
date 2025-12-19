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
from enum import StrEnum

from playwright.async_api import Browser as PlaywrightBrowser
from playwright.async_api import BrowserContext, async_playwright
from playwright.async_api import Error as PlaywrightError


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
        async with async_playwright() as playwright:

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
                    future, url = await self._task_queue.get()
                except QueueShutDown:
                    break

                try:
                    async with self._browser_lock:
                        browser = await self.browser(playwright)
                        async with await browser.new_context() as context:
                            context: BrowserContext
                            await context.route("**/*", block)

                            async with await context.new_page() as page:
                                try:
                                    response = await page.goto(
                                        url,
                                        wait_until="domcontentloaded",
                                    )
                                except (CancelledError, PlaywrightError):
                                    break

                                if not response.ok:
                                    raise ConnectionError(response.status_text)

                                result = await page.content()
                                future.set_result(result)

                except Exception as e:
                    future.set_exception(e)

                finally:
                    self._requests += 1
                    self._task_queue.task_done()

            if self._restart_task:
                self._restart_task.cancel()
                with suppress(CancelledError):
                    await self._restart_task

            if self._browser:
                with suppress(Exception):
                    await self._browser.close()
                self._browser = None

    async def get(self, url):
        future = Future()
        self._task_queue.put_nowait((future, url))

        await future

        return future.result()

    async def shutdown(self):
        self._task_queue.shutdown()
        await self._task_queue.join()
