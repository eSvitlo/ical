from asyncio import new_event_loop, set_event_loop
from concurrent.futures import Future
from enum import StrEnum
from queue import Empty, Queue
from threading import Event, Thread

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


class Browser(Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.task_queue = Queue()
        self.loop = new_event_loop()
        self.is_ready = Event()

    def run(self):
        set_event_loop(self.loop)
        self.loop.run_until_complete(self.worker())
        self.loop.close()

    async def worker(self):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-extensions",
                    "--disable-background-networking",
                    "--disable-sync",
                    "--disable-translate",
                    "--disable-features=site-per-process",
                    "--single-process",
                    "--no-zygote",
                    "--disable-software-rasterizer",
                    "--disable-default-apps",
                    "--disable-component-update",
                    "--disable-renderer-backgrounding",
                    "--disable-background-timer-throttling",
                    "--disable-backgrounding-occluded-windows",
                    "--mute-audio",
                ]
            )
            context = await browser.new_context()

            async def block(route):
                if route.request.resource_type in {"font", "image", "media", "stylesheet"}:
                    await route.abort()
                else:
                    await route.continue_()

            await context.route("**/*", block)

            self.is_ready.set()

            while True:
                try:
                    task = self.task_queue.get(timeout=0.1)
                except Empty:
                    continue

                if task is SHUTDOWN_SIGNAL:
                    self.task_queue.task_done()
                    break

                future, url = task
                page = await context.new_page()
                try:
                    response = await page.goto(url, wait_until="domcontentloaded")
                    if not response.ok:
                        raise ConnectionError(response.status_text)
                    result = await page.content()
                    future.set_result(result)
                except Exception as e:
                    future.set_exception(e)
                finally:
                    self.task_queue.task_done()
                    await page.close()


    def get(self, url):
        if not self.is_ready.wait(timeout=30):
            raise TimeoutError

        future = Future()
        self.task_queue.put((future, url))

        return future.result(timeout=30)

    def stop(self):
        self.task_queue.put(SHUTDOWN_SIGNAL)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.join()
