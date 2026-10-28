# Prototype (optional extra)

A small Python version of the design. It proves the ranking in `../ranking.json` comes from rules, not hand-picking, and lets you try "what if" changes.

| File | Role in the design |
|---|---|
| `criteria/commercial_solar_v1.json` | What the admin screens would save: fields to extract, mandatory rules, weights, limits. Versioned. |
| `prompts.py` | Builds the extraction prompt and strict JSON schema **from** the criteria file (the only AI input) |
| `extract.py` | The one AI call (OpenAI SDK, temperature 0, structured output) |
| `pipeline.py` | Rules only: keyword injection scan, evidence-quote check, range checks, mandatory gate, scoring, ranking, reason template |
| `run.py` | Runs everything; writes `output/ranking.json`, `output/report.md` (reviewer view) and `output/audit.json` |
| `fixtures/extractions_v1.json` | Expected extraction for A–E (golden set / regression test). Evidence quotes omitted because the brief asks not to copy proposal text. |
| `tests/` | Golden ranking, determinism, made-up value dropped, injection caught, weights must total 100 |

## Run

```bash
pip install -r requirements.txt
python run.py                                   # offline, no API key needed
python -m pytest -q                             # 5 tests

# what-ifs (no AI calls, re-scores stored facts)
python run.py --set D.completion_weeks=12       # reviewer records D's clarification -> D moves to 2nd
python run.py --budget 240000                   # tighter budget -> C and E become ineligible
python run.py --weights price=40,experience=15  # new weights (must total 100)

# live extraction
cp .env.example .env                            # add your key; .env is git-ignored
# put the sample proposal text in proposals/A.txt ... E.txt (git-ignored)
python run.py --live
```

Offline result (see `output/report.md` for the full breakdown):

| Proposal | Status | Score | Experience 30 | Price 25 | Timeline 15 | Warranty 15 | Team 15 |
|---|---|---|---|---|---|---|---|
| A | ranked 1 | 92.7 | 30.0 | 18.97 | 15.0 | 13.75 | 15.0 |
| E | ranked 2 | 73.5 | 17.14 | 15.09 | 11.25 | 15.0 | 15.0 |
| D | flagged | 69.8 | 23.57 | 25.0 | 0.0 | 13.75 | 7.5 |
| C | flagged | 61.8 | 19.29 | 13.79 | 7.5 | 13.75 | 7.5 |
| B | ineligible | – | – | – | – | – | – |
