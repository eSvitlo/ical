from asyncio import Future, Queue, QueueShutDown, create_task, sleep
from enum import StrEnum

from playwright.async_api import async_playwright


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


SHUTDOWN_SIGNAL = object()


class Browser:
    MAX_INACTIVITY = 60
    MAX_REQUESTS = 50

    def __init__(self):
        self._task_queue = Queue()
        self._browser = None
        self._requests = 0
        self._restart_task = None

    async def _restart(self):
        await sleep(self.MAX_INACTIVITY)

        if self._browser:
            await self._browser.close()
            self._browser = None

    def schedule_restart(self):
        if self._restart_task:
            self._restart_task.cancel()
        self._restart_task = create_task(self._restart())

    async def browser(self, playwright):
        if (
            self._browser is None
            or not self._browser.is_connected()
            or self._requests > self.MAX_REQUESTS
        ):
            if self._browser:
                await self._browser.close()

            self._browser = await playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-extensions",
                    "--disable-default-apps",
                    "--disable-component-update",
                    "--mute-audio",
                    "--no-zygote",
                    "--single-process",
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
                    task = await self._task_queue.get()
                except QueueShutDown:
                    break

                if task is SHUTDOWN_SIGNAL:
                    await self._browser.close()
                    self._browser = None
                    self._task_queue.task_done()
                    break

                future, url = task

                try:
                    browser = await self.browser(playwright)
                    context = await browser.new_context()
                    await context.route("**/*", block)

                    page = await context.new_page()
                    response = await page.goto(url, wait_until="domcontentloaded")
                    if not response.ok:
                        raise ConnectionError(response.status_text)

                    result = await page.content()
                    future.set_result(result)

                    await page.close()
                    await context.close()

                    self._requests += 1

                except Exception as e:
                    future.set_exception(e)

                finally:
                    self._task_queue.task_done()

            if self._restart_task:
                self._restart_task.cancel()
            await self._browser.close()
            self._browser = None

    async def get(self, url):
        future = Future()
        self._task_queue.put_nowait((future, url))

        await future

        return future.result()

    def shutdown(self):
        self._task_queue.shutdown()
