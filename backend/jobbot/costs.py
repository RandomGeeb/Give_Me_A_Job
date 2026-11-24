"""How much of the Claude plan the app may use: calls per day, and a stop before the plan's own limits."""
import time
from datetime import datetime

from . import db

WINDOWS = {"five_hour": "5-hour", "seven_day": "weekly"}


class LimitReached(RuntimeError):
    pass


def calls_today(con) -> int:
    start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    # Stored times are UTC, so compare in UTC.
    from datetime import timezone
    return con.execute("SELECT COUNT(*) c FROM usage WHERE at>=?",
                       (start.astimezone(timezone.utc).isoformat(timespec="seconds"),)).fetchone()["c"]


def plan(con) -> dict:
    """Last reported plan use, ignoring windows that have since reset."""
    saved = db.get_setting(con, "plan_usage", {})
    return {k: v for k, v in saved.items() if k in WINDOWS and v.get("resetsAt", 0) > time.time()}


def usage(con) -> dict:
    """What Claude last said about each window. `used` is None once that window has reset: the next call tells us the new one."""
    saved, now = db.get_setting(con, "plan_usage", {}), time.time()
    return {k: {"used": v["utilization"] if v.get("resetsAt", 0) > now else None, "resets": v.get("resetsAt"), "at": v.get("at")}
            for k, v in saved.items() if k in WINDOWS}


def check(con, calls: int = 0, percent: float = 0) -> None:
    """`calls` and `percent` ask for that much room to spare."""
    s = db.settings(con)
    n = calls_today(con)
    cap = s["daily_calls"]      # 0 means no daily limit
    if cap and (n + calls > cap or n >= cap):
        raise LimitReached("Daily limit reached. This is the app's own limit, not your Claude plan's. It resets at midnight, or you can change it on Profile.")
    for k, v in plan(con).items():
        if v["utilization"] * 100 + percent >= s["plan_stop_percent"]:
            when = datetime.fromtimestamp(v["resetsAt"]).strftime("%a %H:%M")
            raise LimitReached(f"Your Claude plan's {WINDOWS[k]} limit is {v['utilization']:.0%} used, and the app stops at "
                               f"{s['plan_stop_percent']}%. It resets {when}.")


def record(con, purpose, job_id, model, usage: dict) -> None:
    g = lambda k: usage.get(k) or 0
    con.execute("INSERT INTO usage(at,purpose,job_id,model,input,output,cache_read,cache_write,usd) VALUES(?,?,?,?,?,?,?,?,0)",
                (db.now(), purpose, job_id, model, g("input_tokens"), g("output_tokens"),
                 g("cache_read_input_tokens"), g("cache_creation_input_tokens")))


def save_plan(con, info: dict) -> None:
    win = {k: {"utilization": float(v["utilization"]), "resetsAt": v["resetsAt"], "at": time.time()}
           for k, v in (info.get("unifiedWindows") or {}).items() if k in WINDOWS and v.get("utilization") is not None and v.get("resetsAt")}
    if win:
        db.put_setting(con, "plan_usage", {**db.get_setting(con, "plan_usage", {}), **win})
