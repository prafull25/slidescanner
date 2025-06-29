# SlideScanner Backend

🚀 Backend service for controlling the **SlideScanner** — enabling smooth, intelligent movement and image capture using region-based inputs (e.g., arrow keys). Optimized for hardware that can either **move** or **focus**, not both simultaneously.

[Report Issues](https://github.com/prafull25/slidescanner/issues)

---

## 🔧 Features

- 🧭 Smart movement timing: `3 * sqrt(distance)` seconds
- ⏱️ Ignores unnecessary intermediate moves when keys are pressed quickly
- 🔴 Red Box: Captured image  
- 🟩 Green Box: Movement only  
- 🔲 Black Border: Starting position
- ⚙️ `.env`-based configuration with validation
- 📡 WebSocket-based real-time logs & state updates
- 🗃️ Async DB logging & position tracking

---

## 📦 Tech Stack

- **Python 3.10+** (3.11+ recommended)
- **FastAPI**
- **AsyncIO + SQLAlchemy**
- **PostgreSQL / SQLite**
- **WebSockets**

---

## 🚀 Setup

```bash
git clone https://github.com/prafull25/slidescanner.git
cd slidescanner
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
Create a .env file:
``` bash
DATABASE_URL=sqlite+aiosqlite:///./slidescanner.db
HOST=0.0.0.0
PORT=8000
GRID_SIZE=11
DEFAULT_POSITION_X=5
DEFAULT_POSITION_Y=5
```

uvicorn app.main:app --reload
🧪 Example Flow
Press → → → → quickly

Wait 3 * sqrt(4) seconds → movement completes

System captures only the final region, skipping intermediate stops

