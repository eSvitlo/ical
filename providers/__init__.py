import asyncio
from asyncio import Future, Queue, QueueEmpty
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
    def __init__(self):
        self.task_queue = Queue()
        self._browser = None

    async def browser(self, playwright):
        if self._browser is None or not self._browser.is_connected():
            if self._browser is not None:
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
                ],
            )
        return self._browser

    async def worker(self):
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
                    task = self.task_queue.get_nowait()
                except QueueEmpty:
                    await asyncio.sleep(0.1)
                    continue

                if task is SHUTDOWN_SIGNAL:
                    await self._browser.close()
                    self._browser = None
                    self.task_queue.task_done()
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
                except Exception as e:
                    future.set_exception(e)
                finally:
                    await page.close()
                    await context.close()
                    self.task_queue.task_done()

    async def get(self, url):
        future = Future()
        self.task_queue.put_nowait((future, url))

        await future

        return future.result()
