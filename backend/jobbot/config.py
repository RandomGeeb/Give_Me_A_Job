"""Paths and environment. Everything personal lives under DATA or DOCS (both gitignored)."""
import os
from pathlib import Path

ROOT = Path(os.environ.get("JOBBOT_HOME") or Path(__file__).resolve().parents[2])
DATA = ROOT / "data"
DOCS = ROOT / "documents"
DB_PATH = DATA / "jobbot.db"
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
HOST, PORT = "127.0.0.1", int(os.environ.get("JOBBOT_PORT", "8765"))
HEADLESS = os.environ.get("JOBBOT_HEADLESS") == "1"    # tests only
USER_AGENT = "jobbot/0.1 (personal internship search)"

# Anything stored in the settings table overrides these.
DEFAULT_SETTINGS = {
    "model": "claude-opus-5-5",
    "daily_calls": 150,          # 0 means no daily limit
    "plan_stop_percent": 80,     # leave the rest of the Claude plan for you
    "max_per_company": 2,
    "reuse_percent": 75,         # reuse a CV that shows this much of what the advert asks for; 0 always writes a new one
    "web_search_calls": 20,      # Claude calls in one web search run
    "regions": ["uk", "sg"],
    "families": ["software", "data_ml_quant", "project", "product", "analyst"],
    # Assumed first-interview and offer rates per band. Assumptions, not measurements.
    "priors": {
        "Reach": {"interview": 0.02, "offer": 0.003},
        "Possible": {"interview": 0.05, "offer": 0.01},
        "Competitive": {"interview": 0.10, "offer": 0.02},
        "Strong": {"interview": 0.18, "offer": 0.04},
    },
    "prior_weight": 20,
    "profile": {
        "first_name": "", "last_name": "", "preferred_name": "", "email": "",
        "phone": "", "city": "", "country": "United Kingdom",
        "linkedin": "", "github": "", "website": "",
        "university": "", "degree": "", "discipline": "",
        "degree_level": "", "grade": "", "study_start": "", "graduation": "",
        "work_rights": "", "needs_sponsorship_uk": None,          # UK
        "work_rights_sg": "", "needs_sponsorship_sg": None,       # Singapore
        "availability": "", "notice": "",
        "extra_answers": [],   # [{"question": "...", "answer": "..."}]
        # Only ever filled from here, never by the model. Blank means you do it.
        "sensitive": {},       # {"gender": "...", "ethnicity": "..."}
    },
}


def ensure_dirs() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
