import argparse
import plistlib
import subprocess
import sys
import webbrowser
from pathlib import Path
from threading import Timer

from . import config

PLIST = Path.home() / "Library/LaunchAgents/local.jobbot.discover.plist"


def main() -> None:
    p = argparse.ArgumentParser(prog="jobbot")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="start the dashboard")
    s.add_argument("--no-browser", action="store_true")
    f = sub.add_parser("discover", help="look for new roles once")
    f.add_argument("--web", action="store_true", help="search the web with Claude instead (uses your Claude plan)")
    d = sub.add_parser("schedule", help="turn daily discovery on or off (macOS)")
    d.add_argument("state", choices=["on", "off"])
    a = p.parse_args()
    if a.cmd == "serve":
        import uvicorn

        from . import auto, pipeline
        pipeline.recover()
        if not config.FRONTEND_DIST.exists():
            sys.exit("Build the dashboard first: cd frontend && pnpm install && pnpm build")
        if not a.no_browser:
            Timer(1.2, webbrowser.open, [f"http://{config.HOST}:{config.PORT}"]).start()
        auto.resume()
        uvicorn.run("jobbot.api:app", host=config.HOST, port=config.PORT, log_level="warning")
    elif a.cmd == "discover":
        from .sources import discover
        from .costs import LimitReached
        from .llm.client import LLMError
        try:
            n = discover.run_web() if a.web else discover.run()
        except (LLMError, LimitReached) as e:
            sys.exit(str(e))
        print(f"{n['seen']} postings read, {n['new']} new, {n['kept']} worth a look")
        for e in discover.state["errors"]:
            print("  problem:", e)
    elif a.state == "on":
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        PLIST.write_bytes(plistlib.dumps({
            "Label": "local.jobbot.discover", "ProgramArguments": [sys.executable, "-m", "jobbot.cli", "discover"],
            "WorkingDirectory": str(config.ROOT), "StartCalendarInterval": {"Hour": 8, "Minute": 0},
            "StandardOutPath": str(config.DATA / "discover.log"), "StandardErrorPath": str(config.DATA / "discover.log")}))
        subprocess.run(["launchctl", "load", "-w", str(PLIST)], check=True)
        print("Daily discovery at 08:00 is on.")
    else:
        if PLIST.exists():
            subprocess.run(["launchctl", "unload", "-w", str(PLIST)], check=False)
            PLIST.unlink()
        print("Daily discovery is off.")


if __name__ == "__main__":
    main()
