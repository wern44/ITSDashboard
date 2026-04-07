# ITS Briefing

A slim, standalone Python web app that fetches 19 curated cybersecurity RSS feeds, classifies the articles via local Ollama, generates a structured AI executive summary once per day, and serves the result on a dark-mode web page.

No database. No authentication. One process.

## Requirements

- Python 3.11+
- A running [Ollama](https://ollama.com) instance with the model `llama3.1:8b` pulled (or any other model — set `OLLAMA_MODEL` in `.env`)

```bash
ollama pull llama3.1:8b
```

## Install

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
# or: source .venv/bin/activate  # Linux/macOS
pip install -e ".[dev]"
cp .env.example .env
```

## Run

Start the web app + daily scheduler (default 06:00 Europe/Berlin):

```bash
python -m its_briefing
```

Open http://127.0.0.1:8089 in your browser. If no briefing has been generated yet, click "Generate now".

Trigger a fresh briefing manually from the CLI:

```bash
python -m its_briefing.generate
```

Or click "Rebuild now" in the footer of the web page.

## Configuration

- **`config/sources.yaml`** — RSS feeds. Add or remove sources here.
- **`config/categories.yaml`** — topic categories used for classification + UI badges. Add a new category by appending an entry; no code change needed.
- **`.env`** — runtime settings (Ollama URL/model, schedule time, Flask host/port, log level).

## Output

Each daily run writes one file to `cache/briefing-YYYY-MM-DD.json`. The web page always serves the most recent successful briefing.

## Tests

```bash
pytest
```
# ITSDashboard
