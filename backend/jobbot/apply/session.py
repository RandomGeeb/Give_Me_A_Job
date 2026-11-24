"""One visible browser, owned by one thread. The dashboard sends it commands through a queue."""
import queue
import threading
from concurrent.futures import Future

from playwright.sync_api import sync_playwright

from .. import config
from . import fill


def read_form(url: str) -> list[dict]:
    """Read a public application form in a throwaway headless browser with writes blocked."""
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            ctx = b.new_context(service_workers="block")
            ctx.route("**/*", fill.read_only)
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            fill.wait_ready(page)
            return fill.extract(page)
        finally:
            b.close()


class Session:
    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.pages: dict[int, object] = {}

    def run(self, fn, timeout: float = 240):
        with self.lock:
            if not self.thread or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._loop, daemon=True)
                self.thread.start()
        fut: Future = Future()
        self.q.put((fn, fut))
        return fut.result(timeout)

    def _loop(self):
        with sync_playwright() as p:
            ctx = None
            while True:
                fn, fut = self.q.get()
                try:
                    try:
                        alive = ctx is not None and bool(ctx.pages is not None) and ctx.browser is not False
                        if alive:
                            ctx.cookies("https://example.com")     # raises if the window was closed
                    except Exception:
                        alive = False
                    if not alive:
                        self.pages.clear()
                        # A dedicated profile, so employer logins persist without touching your own browser.
                        ctx = p.chromium.launch_persistent_context(
                            str(config.DATA / "browser"), headless=config.HEADLESS, no_viewport=True)
                    fut.set_result(fn(ctx))
                except Exception as e:
                    fut.set_exception(e)

    def open(self, job_id: int, url: str):
        def go(ctx):
            page = self.pages.get(job_id)
            if page is None or page.is_closed():
                page = ctx.new_page()
                self.pages[job_id] = page
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.bring_to_front()
            fill.wait_ready(page)
        return self.run(go)

    def extract(self, job_id: int) -> list[dict]:
        return self.run(lambda ctx: fill.extract(self._page(ctx, job_id)))

    def fill(self, job_id: int, fs, answers, files) -> dict:
        return self.run(lambda ctx: fill.fill(self._page(ctx, job_id), fs, answers, files))

    def _page(self, ctx, job_id):
        page = self.pages.get(job_id)
        if page is None or page.is_closed():
            raise RuntimeError("The application window is closed. Open it again.")
        # Logins often move to a new tab; use the newest one this page opened.
        kids = [p for p in ctx.pages if not p.is_closed() and p.opener() == page]
        return kids[-1] if kids else page


session = Session()
