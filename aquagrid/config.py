"""Every threshold, limit and env value lives here. No logic."""
import os

from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("AQUAGRID_DB", "data/aquagrid.sqlite")
DEMO_NOW = "2026-11-20T14:00"  # simulated clock: tariff peak starts now

# Safety floors (HARD, always beat tariff savings)
RESERVE_FLOOR_PCT = 35.0
BLOOM_RESERVE_MARGIN_PCT = 5.0  # effective floor 40% if bloom active OR intake untrusted
RESERVOIR_MAX_PCT = 95.0

# U-5.2: Civil Defence emergency fire reserve
FIRE_RESERVE_M3 = 0.0           # 0 = no active request; set at runtime via app/cli
FIRE_RESERVE_DURATION_H = 3     # protected for 3 hours (per curveball brief)
MIN_SERVICE_PRESSURE_M = 15.0
CHLORINE_MIN_MGL, CHLORINE_MAX_MGL = 0.2, 5.0
TURBIDITY_MAX_NTU = 1.0

# Sensor checks
VALID_RANGE = {
    "pressure": (0, 100),
    "flow": (0, 600),
    "turbidity": (0, 50),
    "chlorine": (0, 5),
    "level": (0, 100),
}
FLATLINE_WINDOW = 15
FLATLINE_EPS = 1e-3  # stdev below this = stuck
DRIFT_WINDOW = 30
DRIFT_SLOPE_M_PER_MIN = 0.25  # residual-vs-neighbours slope
MAX_ROC = {"pressure": 20.0, "chlorine": 1.0}  # per-minute jump = impossible
STALE_AFTER_MIN = 10

# Leak detection
LEAK_PRESSURE_DROP_M = 8.0
LEAK_FLOW_RISE_LPS = 15.0
MASS_BALANCE_TOL_LPS = 10.0
NEIGHBOUR_DROP_FRACTION = 0.3
BASELINE_SAMPLES = 90  # first 90 minutes (12:00-13:30) = baseline
CURRENT_SAMPLES = 3  # latest 3 minutes = "now"
DRIFT_MAX_STEP_M = 3.0  # a ramp moves in small steps; a leak/step jumps more than this

SCHEDULE_BUFFER_PCT = 2.0  # scheduler stays this far above the floor

# Demo numbers (LOCKED at HC1, asserted by tests/test_demo_numbers.py)
PLANT_HOLD_M3H = 1900.0
PLANT_MIN_M3H, PLANT_MAX_M3H = 0.0, 2000.0
PLANT_KWH_PER_M3 = 4.0
THROTTLE_CANDIDATE_M3H = 500.0
PEAK_START_H, PEAK_END_H = 14, 18
RESERVOIR_CAPACITY_M3 = 40_000.0

# Hydraulics (stretch)
WAVE_SPEED_MS = 1000.0
DEFAULT_CLOSURE_S = 120
MAX_SURGE_M = 30.0
RESERVOIR_HEAD_M = 80.0  # supply head at R1; junctions sit at 11-30 m, so normal service pressure is about 50-69 m

# Agent caps
MAX_STEPS = 8
MAX_REPLANS = 2
MAX_PENDING_PER_RUN = 3
LLM_TIMEOUT_S = {"ollama": 90, "groq": 30, "gemini": 30}
LLM_MAX_RETRIES = 1
LLM_MAX_TOKENS = 900

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:4b")
# Model ids change often (llama-3.3-70b-versatile and gemini-2.5-flash are already gone for our keys).
# List what a key can use: GET <base_url>/models. Verified on 2026-11-20 with tool calling.
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
OPERATOR_TOKEN = os.getenv("OPERATOR_TOKEN")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")
PLANT_A_TOKEN = os.getenv("PLANT_A_TOKEN")
PLANT_B_TOKEN = os.getenv("PLANT_B_TOKEN")
PLANTS = {"A": "NAQAA Desalination Plant LLC", "B": "Al Kafaah Reverse Osmosis Desalination Company"}
# Cloud first for tool-calling quality; Ollama last as the offline safety net. Providers without a key are skipped.
LLM_CHAIN = os.getenv("LLM_CHAIN", "groq,gemini,ollama").split(",")
