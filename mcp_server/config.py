"""Shared paths and constants for the plant-troubleshooting MCP server.

All paths are resolved relative to this file's location, not the process cwd, so the
server works the same way regardless of how the MCP client launches it.
"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

HISTORIAN_DB_PATH = PROJECT_ROOT / "boiler_historian.duckdb"
ALARMS_DB_PATH = PROJECT_ROOT / "boiler_alarms.duckdb"
KG_PATH = PROJECT_ROOT / "boiler_kg.json"
VECTOR_DB_PATH = PROJECT_ROOT / "rag_vector_db"
VECTOR_DB_COLLECTION = "plant_docs"
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
INVESTIGATIONS_DIR = PROJECT_ROOT / "investigations"

# The historian/alarm data is a static 5-day snapshot, not a live feed -- every
# historian/alarm tool validates requested time ranges against this window.
DATA_WINDOW_START = "2022-03-27 14:28:54"
DATA_WINDOW_END = "2022-04-01 14:28:49"

# Row-count safety cap for historian_get_series before auto-downsampling kicks in.
MAX_SERIES_POINTS_PER_TAG = 2000

REASONING_TYPES = (
    "hypothesis",
    "observation",
    "conclusion",
    "rejection",
    "corrective_action",
    "cause_vs_symptom_check",
)

FINAL_STATUSES = ("root_cause_found", "inconclusive", "escalated")

DOC_TYPES = ("controls", "datasheet", "maintenance", "safety", "sop", "troubleshooting")
