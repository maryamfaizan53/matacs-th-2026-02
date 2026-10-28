# WRITEUP: AI Proposal Triage Design

**Design in one line:** AI *reads* each proposal and pulls out facts with word-for-word evidence; fixed rules *score* them; a human *decides*. Flow: [diagram/process-flow.png](diagram/process-flow.png).

## Result on the sample set

| Proposal | Status | Rank | Score | Why |
|---|---|---|---|---|
| A | ranked | 1 | 92.7 | Most experience, fastest schedule, complete team plan |
| E | ranked | 2 | 73.5 | Best warranty, complete team plan, least experience |
| D | flagged | – | 69.8 (provisional) | Lowest price, but no committed completion date |
| C | flagged | – | 61.8 (provisional) | Contains text telling automated reviewers to rank it first |
| B | ineligible | – | – | Licence held by a subcontractor "to be confirmed", not the bidder |

If D confirms 12 weeks it scores 84.8 and moves to 2nd, which is why the tool asks instead of guessing. C's hidden instruction changed nothing; its score comes from facts only.

**Scoring rules** (configured per criterion by the admin, chosen from a small menu):
- *Lower is better within a limit* (price, timeline): best eligible bid = full marks, the limit ($260k / 16 weeks) = zero, straight line between.
- *Higher is better* (experience, warranty): value ÷ best eligible value. Warranty is half panel, half workmanship.
- *Checklist* (team): named lead, crew, safety plan, performance monitoring; each is a quarter of the points.
- Only eligible bids set benchmarks, so B's low price punishes nobody.

**Assumptions:** the 16-week deadline and budget ceiling are hard limits (over = ineligible, unstated = flagged). "Monitoring" means system performance monitoring, so C's weekly progress reports do not count. C says "since 2020", which may include projects older than 5 years; the tool raises a warning for the reviewer.

## 1. AI vs. rules

- **AI (one step): extraction.** Turning free-text PDFs into fields ("licence held by a subcontractor to be confirmed" → `licence_holder: subcontractor`) needs language understanding. Rules would break on every new wording.
- **Code/rules: everything else.** Reading the PDF, checking quotes, mandatory pass/fail, budget and deadline limits, scoring, ranking, writing the reason line (a template filled from the score breakdown), and the audit log.
- **Why there:** AI is good at reading but weak at being consistent, explainable and hard to manipulate. Scores need all three, and are simple arithmetic once facts are known. So the AI never sees weights, never compares bids, never outputs a score.

## 2. Consistency and explainability

- **Repeatable:** temperature 0, strict JSON schema, pinned model and prompt version. Each extraction is stored, keyed by document hash + prompt version + criteria version. Ranking the same set twice reuses stored facts, and scoring is pure code, so **the result is identical**. Ties break by lower price, then letter.
- **Drift check:** the five samples are a regression test; a new model or prompt must reproduce the expected facts first.
- **Explainable:** for each proposal the reviewer sees a fact sheet (every value beside its quote from the PDF), points per criterion with the inputs used, and every flag. Example: "A: 92.7: full marks on experience (14 installs), timeline (12 wk) and team; biggest loss is price ($238,000, −6.0)." Because the reason is generated from the numbers, it cannot contradict them.

## 3. Human in the loop

- **Checkpoint A, facts:** the reviewer checks the fact sheet and can correct any value (a reason is required). Scores update instantly with no new AI call.
- **Checkpoint B, flags and ranking:** flagged proposals are not ranked until a person acts: request clarification (D), clear or disqualify (C), or confirm ineligibility (B; the tool proposes, a person confirms). The reviewer may override any rank, with a written reason.
- **Award:** always a human decision. The ranking is advice.
- **Logged for audit:** file hash; criteria version; model, prompt version and raw AI output; validation results; every score with its inputs and formula; every human action (who, when, old value → new value, reason); the final decision. Logs are append-only.

## 4. Changing criteria

- Criteria are **versioned, never edited in place**. Saving a change creates v2; every run stays pinned to the version it used, so old results remain explainable.
- Open runs scored on v1 are marked **"criteria changed – re-score needed"**; the reviewer chooses to re-score. One ranking never mixes versions.
- **Weight or limit change:** re-scored instantly from stored facts, no AI cost.
- **New or reworded criterion:** only the new fields are extracted again, then re-scored and reviewed.
- After an award, results lock; later edits apply only to new runs.
- The admin screen rejects weights not totalling 100.

## 5. Risks and safeguards

| Risk | Safeguard |
|---|---|
| **Wrong extraction** (bad PDF text, tables, OCR) | OCR fallback; empty-text check; range checks; human fact-sheet review; regression tests on known samples |
| **Made-up facts** | Every value needs a quote that code finds in the PDF, or it is dropped and flagged; "not stated" stays empty and is never guessed (D) |
| **Manipulation / prompt injection** (C) | Proposal marked as untrusted data in the prompt; the AI has no power to score, so injected text has nothing to change; separate keyword scan plus AI report → flag for a human |
| **Marketing language** ("most competitive", B) | Only verifiable facts are extracted; adjectives earn nothing |
| **Bias** (towards big firms, polished writing, long proposals) | Same fixed fields and formulas for all; checklist scoring ignores length and style; names can be hidden during review; reviewers can see score distributions per category |
| **Incomplete or tricky bids** (subcontracted licence, unclear period) | Mandatory rules say *the bidder* must hold the licence; date-window warnings |
| **Over-reliance on the tool** | Flags block ranking; overrides and the award stay human; the report shows provisional scores clearly |
| **Cost** | One small-model call per proposal, cached; re-scoring costs nothing |
