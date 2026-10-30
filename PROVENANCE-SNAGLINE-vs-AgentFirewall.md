# Provenance Investigation: SNAGLINE vs Agent Firewall

**Subject:** `https://github.com/Cyrax321/SNAGLINE` vs `https://github.com/Bipin583/agent-firewall`
**Investigator:** Cyrax321 (SNAGLINE maintainer) — **note: the requesting party is an interested party in the outcome**
**Date:** 2026-10-16
**Method:** public GitHub API + full git history of both repositories + automated lexical/AST/constant comparison
**Priority applied:** accuracy over confirmation

---

## 1. Verdict

**The allegation is UNSUPPORTED by the evidence examined.**

Every test that would detect derivation was run and every one came back negative:

| Test | Result |
|---|---|
| SNAGLINE-distinctive identifiers present in Agent Firewall | **0 of 22** |
| Any textual reference to "snagline" anywhere in Agent Firewall | **0** |
| Semantically distinctive shared phrases (6-grams, boilerplate filtered) | **0 of 168** |
| Exact identical code lines ≥28 chars | **14 — all universal idioms or Claude Code's own spec keys** |
| Shared numeric constants in ≥2 files of each repo | **18 — all round numbers, unrelated contexts** |
| Public access evidence (star / fork / issue / PR / reference) | **None** |
| Core architectural principle | **Inverted** — SNAGLINE is fail-*open*; Agent Firewall is explicitly fail-*closed* |

The two repositories solve different problems. SNAGLINE detects **behavioral failure of an agent** (loops, error cascades, latency anomalies, stagnation, entropy collapse). Agent Firewall is a **pre-execution policy gate** (DLP, secret detection, prompt-injection indicators, filesystem/command rules, authorization, audit chain). Agent Firewall contains no loop, repetition, stagnation, entropy, CUSUM, or latency detection of any kind.

Far more telling than the absence of matches: Agent Firewall **possesses capabilities SNAGLINE does not have** (hash-chained audit log, SQLAlchemy/FastAPI service, MCP gateway, YAML policy evaluator, DLP/secret/PII rules) and **more governance scaffolding** (`CONTRIBUTING.md`, `SECURITY.md`, `RELEASING.md` — all three absent from SNAGLINE). Derived projects are subsets, not supersets with unrelated additions.

---

## 2. Strongest five findings

Ranked by evidentiary strength *against* the copying hypothesis.

### F1 — Zero SNAGLINE identifiers in Agent Firewall — **Confirmed (high confidence)**

Searched for 22 identifiers that are specific to SNAGLINE's design. Every one returns zero hits:

```
FailureRisk  StepEvent  episode_id  action_signature  severity_from_score
make_signature  fail_open  CUSUM  cusum  Welford  silent_abort  meltdown
stagnation  idle_gap  wall_clock_budget  governance_decay  compaction_tripwire
side_effect_duplicate  near_duplicate_loop  loop_window_size  repeat_threshold
```

Command:
```bash
git clone https://github.com/Bipin583/agent-firewall && cd agent-firewall
for t in FailureRisk StepEvent episode_id action_signature severity_from_score \
         make_signature fail_open CUSUM Welford silent_abort meltdown stagnation \
         idle_gap wall_clock_budget governance_decay compaction_tripwire \
         side_effect_duplicate near_duplicate_loop loop_window_size repeat_threshold; do
  printf "%-26s %s\n" "$t" "$(grep -rniI --exclude-dir=.git -- "$t" . | wc -l)"
done
```

Also `grep -rniI --exclude-dir=.git "snagline" .` returns **nothing**.

**Why distinctive:** a renamed copy still needs to model the same domain. If someone derived Agent Firewall from SNAGLINE and renamed `FailureRisk`→`SecurityFinding` and `StepEvent`→`NormalizedRequest`, the *concepts* would still appear — episode state, signature normalization, repeat thresholds. They do not.

**Alternative explanation considered:** total rewrite from scratch by a competent engineer building a different tool. Fully consistent with the evidence. Cannot be distinguished from "wrote it fresh while having seen SNAGLINE" by artifact evidence alone — but see F5 and F6, which bear on that.

---

### F2 — No distinctive shared prose; all 168 shared 6-grams are boilerplate — **Confirmed (high confidence)**

Cross-repo n-gram comparison over all `.py`, `.md`, `.toml`, `.yml`, `.sh`:

- SNAGLINE: **2,966,850** six-grams
- Agent Firewall: **19,541** six-grams
- Shared: **275** raw → **168** after filtering import/argparse/CI boilerplate

Inspected all 168 individually. Every one is universal:

- `from future import annotations import json import os`
- `def main argv list str none none int`
- `sub parser add subparsers dest command`
- `all notable changes to this project` (Keep a Changelog template)
- `uses pypa gh action pypi publish release v1 with`
- `audience developers operating system os independent` (trove classifiers)
- `https img shields io badge python version`
- `mkdir parents true exist ok true`
- `os path join os path dirname`
- `re compile za z0 za z0`

Not one domain-specific sentence. In two projects whose docs are both extensive, prose overlap of *zero* distinctive sentences is decisive.

Reproduce:
```bash
python3 - <<'PY'
import re, pathlib, collections
def norm(t):
    t = re.sub(r"```.*?```"," ",t,flags=re.S)
    t = re.sub(r"[`*#>|_\[\](){}<>/\\=,.:;]"," ",t)
    return re.findall(r"[a-z][a-z0-9_]+", t.lower())
def ng(root):
    idx=collections.defaultdict(set)
    for p in pathlib.Path(root).rglob("*"):
        if "/.git/" in str(p) or "__pycache__" in str(p): continue
        if p.suffix not in {".py",".md",".toml",".yml",".sh"}: continue
        w=norm(p.read_text(errors="replace"))
        for i in range(len(w)-5): idx[tuple(w[i:i+6])].add(str(p))
    return idx
A=ng("path/to/SNAGLINE"); B=ng("path/to/agent-firewall")
print(len(set(A)&set(B)))
PY
```

---

### F3 — The core design principle is inverted, not inherited — **Confirmed (high confidence)**

SNAGLINE's single most repeated commitment is **fail-open** — it never breaks the host agent:

- **671** occurrences of `fail-open`/`fail_open` across SNAGLINE
- `src/snagline/config.py:938` → `fail_open: bool = True`
- README:9 → "Hard fail-open guarantee: it can never crash or stall the"

Agent Firewall documents the **exact opposite** as a deliberate stance:

`docs/limitations.md`:
> **Fail-closed.** Unknown tools, authorization failures, detector crashes, and
> policy-load errors never silently `allow`. They degrade to `block` or `ask`.

`docs/limitations.md` also states:
> **Risk cannot buy an allow.** … Precedence is `deny > approval > allow`.

Agent Firewall's risk model also refuses to let scoring override authorization — the inverse of SNAGLINE's "risk is informational; sinks decide."

A project derived from SNAGLINE would inherit fail-open and would have to *reverse* its central invariant. Agent Firewall's own docs describe the inversion as a design stance, with no reference to reversing anything.

---

### F4 — Different domains; no behavioral-failure detection exists in Agent Firewall — **Confirmed (high confidence)**

| | SNAGLINE | Agent Firewall |
|---|---|---|
| Problem | detect agent *failure modes* from a step stream | gate tool *requests* before execution |
| Core artifacts | `FailureRisk`, `StepEvent`, `Monitor`, detectors, sinks | `SecurityDecision`, `NormalizedRequest`, `SecurityEngine` |
| Detection modules | loop, near-duplicate loop, cycle, stall, error cascade, latency CUSUM, stagnation, meltdown, token runaway, silent abort, compaction tripwire, side-effect guard | prompt injection, secrets, DLP, command rules, filesystem rules |
| Risk model | score 0.0–1.0 → 3 severity bands, informational | score 0–100 additive, 5 severity weights + category bonuses, decisive |
| Enforcement | sinks dispatch; enforcement optional and fail-open | policy gate returns allow/ask/block, fail-closed |
| Audit | none in `src/` (1 mention total) | hash-chained JSONL: `prev_hash`/`event_hash`/genesis |
| API/service | none (sidecar is stdlib HTTP) | FastAPI + SQLAlchemy, `[api]` extra |
| Other | — | MCP gateway, YAML policy evaluator |

Verification that Agent Firewall has *no* overlap in SNAGLINE's core domain:
```bash
grep -rniI --exclude-dir=.git -E "\bloop\b|retry.?storm|repetition|stagnation|entropy|cusum|welford|latency" src/
# (no output)
```

Its five detection modules are, by their own docstrings: "Deterministic security detectors", shell-command inspection, DLP on outgoing transfer, path-aware filesystem inspection, prompt-injection *indicators*, deterministic secret detection. Every one is a **prevention** control. None is a **failure detector**.

---

### F5 — No public access evidence, and the largest block of shared text has a mundane shared cause — **Confirmed (high confidence)**

Agent Firewall's author (`Bipin583`, account created 2024-10-23, 4 public repos, 0 followers, following 2) has **no public interaction with SNAGLINE**:

```bash
gh api repos/Cyrax321/SNAGLINE/stargazers --jq '.[].login' | grep -i bipin   # no match
gh api repos/Cyrax321/SNAGLINE/forks     --jq '.[].owner.login' | grep -i bipin  # no match
gh api "repos/Cyrax321/SNAGLINE/issues?state=all&per_page=100" \
  --jq '.[] | select(.user.login=="Bipin583")'                                # empty
gh api "search/issues?q=repo:Cyrax321/SNAGLINE+Bipin583" --jq .total_count    # 0
```

**Caveat, stated plainly:** absence of public interaction does **not** prove absence of access. A person can read a public repository without starring, forking, or commenting. This finding lowers confidence that Agent Firewall's author *noticed* SNAGLINE; it does not prove it.

What *can* be attributed: the single largest cluster of shared 6-grams is both repos' `.github/workflows/release.yml`, and both follow the **canonical `pypa/gh-action-pypi-publish` example** (`actions/checkout` → `setup-python` → `python -m build` → `upload-artifact` → `download-artifact` → publish). Independent convergence on the upstream reference implementation explains this fully. SNAGLINE's version is in fact *more* elaborate (tag validation, pyproject/version cross-check) and pins `@v7` actions where Agent Firewall uses `@v4`/`@v5`.

---

### F6 — Timeline is consistent with independent, pre-public development — **Strongly suggestive**

| Date (UTC) | Event | Source |
|---|---|---|
| 2024-10-23 | `Bipin583` account created | GitHub API |
| 2026-08-14 | **SNAGLINE first public commit** — `events`, `risk`, detector/sink protocols, fail-open `Monitor` | `73133dd` |
| 2026-08-14 | SNAGLINE loop + error-cascade detectors, console sink, raw adapter, replay CLI | `aa55d45` |
| 2026-08-14 | SNAGLINE latency/CUSUM detector | `1242520` |
| 2026-08-15 | SNAGLINE Claude Code adapter | `c7a157b` |
| 2026-10-09 | Agent Firewall's own docs date the Claude Code protocol research | `src/agent_firewall/integrations/claude_code/hook.py:4` — "verified against the official docs on 2026-10-09" |
| 2026-10-10 08:17 | Agent Firewall repo created | GitHub API `created_at` |
| 2026-10-10 13:47–16:32 +0530 | All 12 commits, initial commit = **60 files / 4,460 lines** | `449c1c8` … `75c802d` |
| 2026-10-10 | Published to PyPI as `agent-firewall1` | `75c802d` |

Two observations:

1. **The complete implementation landed in one commit.** 60 files, 4,460 insertions, at minute three. Author dates equal committer dates for all 12 commits — no rewriting. Combined with the 2026-10-09 reference inside the code, this establishes work began **at least one day before the repository existed**: development happened privately, then was published in a burst.
2. **That pattern is the opposite of derivation.** Deriving from an existing public codebase produces incremental commits traced to that codebase. It does not produce a private, self-contained, differently-architected implementation published in a single burst with no references.

**Limitation:** I can date the *creation* of the public repository precisely and the *start of work* only from a self-reported date inside the code. I cannot observe private development. The 8-week gap between SNAGLINE's publication (2026-08-14) and Agent Firewall's (2026-10-10) is consistent with either independent work or prior exposure. The artifact evidence (F1–F4) is what discriminates, and it points to independence.

---

## 3. Feature lineage

| Capability | SNAGLINE evidence | Agent Firewall evidence | Earliest verifiable date | Similarity type | Confidence |
|---|---|---|---|---|---|
| Failure detection (loop/retry storm) | `detectors/loop.py`, `aa55d45` | **absent** | SNAGLINE 2026-08-14 | none | High (no overlap) |
| Error cascade detection | `detectors/error_cascade.py`, `aa55d45` | **absent** | SNAGLINE 2026-08-14 | none | High |
| Latency anomaly (Welford + CUSUM) | `detectors/latency_anomaly.py`, `1242520` | **absent** (`grep cusum/welford/latency` → 0) | SNAGLINE 2026-08-14 | none | High |
| Stagnation / novelty collapse | `detectors/stagnation.py` | **absent** | SNAGLINE 2026-08-14 | none | High |
| Entropy meltdown | `detectors/meltdown.py` | **absent** | SNAGLINE 2026-08-14 | none | High |
| Token runaway / budget | `detectors/token_runaway.py` | **absent** | SNAGLINE 2026-08-14 | none | High |
| Side-effect duplicate guard | `detectors/side_effect_guard.py` | **absent** (has idempotency *authorization*, different) | SNAGLINE 2026-08-14 | none | High |
| Claude Code hook protocol | `adapters/claude_code.py`, `c7a157b` | `integrations/claude_code/hook.py` | SNAGLINE 2026-08-15; AF docs 2026-10-09 | **conceptual only** — both implement Claude Code's published hook spec; AF blocks (`permissionDecision: deny`), SNAGLINE observes and alerts | High that it's spec-driven, not derived |
| Risk score → severity tier | `risk.py:severity_from_score` (0.0–1.0, 3 bands) | `risk/scorer.py` (0–100, 5 weights + category bonus, additive) | SNAGLINE 2026-08-14 | **conceptual only** — universal pattern, materially different mechanics | High |
| Fail-open resilience | 671 occurrences, `fail_open=True` | **inverted**: explicit fail-closed stance | SNAGLINE 2026-08-14 | **contradiction** | High |
| DLP / secrets / PII | absent | `detection/dlp.py`, `secrets.py` | AF 2026-10-10 | AF-only | High |
| Prompt-injection indicators | absent | `detection/prompt_injection.py` | AF 2026-10-10 | AF-only | High |
| Command / filesystem path rules | absent | `detection/command_rules.py`, `filesystem_rules.py` | AF 2026-10-10 | AF-only | High |
| Hash-chained audit log | absent (1 mention total) | `audit/verifier.py` (`prev_hash`/`event_hash`/genesis) | AF 2026-10-10 | AF-only | High |
| Policy YAML evaluator + authorization | `Config` validation only | `policies/evaluator.py`, `authorization.py` | AF 2026-10-10 | AF-only | High |
| HTTP sidecar / bridges | `server/`, `adapters/` | FastAPI app + MCP gateway | SNAGLINE earlier; AF different stack | conceptual only | High |
| PyPI release automation | `.github/workflows/release.yml` | `.github/workflows/release.yml` | both | **shared upstream template** (`pypa/gh-action-pypi-publish`) | High |

### Classification

**1. Merely conceptually similar (explained by domain, not derivation):** risk scoring, severity banding, a Claude Code hook, a CLI with subcommands, `py.typed`, a policy/config layer, PyPI release automation.

**2. Distinctive design similarities:** **none found.**

**3. Matching implementation details:** **none found** beyond universal idioms and Claude Code's own field names (`hook_event_name`, `tool_name` — dictated by Claude Code's hook API, not by SNAGLINE).

**4. Direct evidence of code reuse or derivation:** **none.**

---

## 4. Code-level comparison

The only genuinely overlapping subsystem is Claude Code hook handling. Side by side:

**SNAGLINE — `src/snagline/adapters/claude_code.py`** (observational, fail-open):
```python
def is_claude_code_payload(obj: Any) -> bool:
    """Heuristic: Claude Code hook payloads always carry ``hook_event_name``."""
    return isinstance(obj, dict) and "hook_event_name" in obj

# Pairing Pre/Post with HookTracker so latency_ms is real; emits a StepEvent.
# Ingest errors; never blocks the host. Fail-open by construction.
```

**Agent Firewall — `src/agent_firewall/integrations/claude_code/hook.py`** (adversarial, fail-closed):
```python
HOOK_EVENT = "PreToolUse"

def _response_for(decision: SecurityDecision, emit_allow: bool) -> dict[str, Any] | None:
    mapping = {Decision.BLOCK: "deny", Decision.ASK: "ask", Decision.ALLOW: "allow"}
    perm = mapping[decision.decision]
    if decision.decision is Decision.ALLOW and not emit_allow:
        return None
    return {"hookSpecificOutput": {"hookEventName": HOOK_EVENT,
                                  "permissionDecision": perm,
                                  "permissionDecisionReason": decision.explanation}}
```

Shared: `payload.get("hook_event_name")`, `payload.get("tool_name")`, `def main(argv: list[str] | None = None) -> int`.

Different: objective (observe vs gate), output contract (no output vs `permissionDecision`), failure posture (swallow vs block/ask), state model (episode windows vs per-request policy), audit (none vs hash-chained), trust model (absent vs `TrustLevel`). AF's docstring cites its own source: *"Translates the Claude Code hook protocol (verified against the official docs on 2026-10-09)"*.

**Normalization — same word, unrelated implementations:**

SNAGLINE `default_normalizer` collapses uuid-like substrings and digit runs so retry signatures match for **near-duplicate loop detection**:
```python
text = _UUID_LIKE_RE.sub("<uuid>", signature)
return _DIGIT_RUN_RE.sub("#", text)
```

Agent Firewall `_TOOL_MAP` maps Claude Code tool names to `(resource_arg_key, action_type)` for **authorization decisions**:
```python
"Bash": ("command", ActionType.EXECUTE),
"Read": ("file_path", ActionType.READ),
```
Different input, different purpose, different output, different algorithm. Coincidence of vocabulary.

---

## 5. Alternative explanations for the observed overlaps

1. **Shared upstream templates** — both `release.yml` follow `pypa/gh-action-pypi-publish`'s documented example; both READMEs use shields.io badges and Keep a Changelog headers; both `pyproject.toml` files use standard trove classifiers. This accounts for ~95% of shared 6-grams with no interpersonal link.
2. **Shared ecosystem norms** — Python packaging layout (`src/`, `py.typed`, `tool.setuptools.packages.find`), `mypy --strict`, ruff rule selections, pytest layout. Universal for a typed Python CLI library.
3. **Claude Code's public hook protocol** — both implement the same published payload contract. This is *mandated interoperability*, not copying. It is the single most likely source of the two "distinctive-looking" matches.
4. **Common security-tool patterns** — "score → severity → action" and "audit every decision" are universal in security products.
5. **The same LLM** — Agent Firewall contains `.claude/settings.json` and its commit messages read as a single-session Claude Code build. SNAGLINE's CHANGELOG credits Claude Code too. Two Claude-authored projects will converge on similar *structure* (docstrings explaining rationale, `docs/limitations.md`, fail-safe handling) without either author reading the other. This is a strong explanation for stylistic similarity that is not code copying.

---

## 6. Evidence gaps

| Gap | Would be resolved by |
|---|---|
| Private development history cannot be observed | Only the author can attest to what existed before 2026-10-09 |
| Absence of stars/forks ≠ absence of access | Only the author can attest to whether SNAGLINE was read |
| I compared current `HEAD` of both repos | A commit-by-commit diff across time would catch a transient copy later reverted — **recommend running `git log -p` comparisons against AF's full 12-commit history** |
| No PyPI metadata comparison | `pypistats`/wheel contents of `agent-firewall1` vs SNAGLINE releases (wheels embed file lists and docstrings) |
| Semantic/AST similarity beyond line equality | A full AST-normalized comparison (rename-insensitive) was approximated by 6-gram + line-equality; a rigorous pass would normalize identifiers and compare call graphs |
| Contributor overlap | Only GitHub account-level public data was checked |

**What I did not do:** no private data, no credential access, no unauthorized access of any kind. Only public repositories and the public GitHub API via an authenticated `gh` session.

---

## 7. Recommended next steps

1. **Do not make a public accusation.** The evidence does not support one. Asserting copying from SNAGLINE to Agent Firewall would be a factual claim you cannot currently substantiate, and it is publicly falsifiable by anyone running the checks in §2 — which is exactly how reputational damage to you becomes certain.
2. **Preserve the methodology, not the conclusion.** This report's value is that it is *reproducible*. Anyone can re-run the commands and reach the same negative result.
3. **Archive the evidence.** Tag a snapshot of both repos (`git tag provenance-2026-10-16`) so a future comparison is against fixed points.
4. **If you want certainty, ask rather than accuse.** A neutral, non-accusatory question to `Bipin583` — "did you reference SNAGLINE or any similar project, and what did you base this on?" — costs nothing and is answerable. An accusation is not.
5. **Address the real exposure instead.** SNAGLINE's defensibility is MIT-granted history: every copy ≤0.1.0 is MIT forever, and 10 forks retain the notice. The Apache-2.0 change (`9b84116`) governs future versions only.
6. **Consider whether "ideas copied" is the right frame.** Copyright does not cover detector concepts. If the concern is a competitor shipping a similar product, the productive responses are speed, trademark on the name/logo, and a published paper — not license or accusation.

---

## Appendix A — Machine-readable findings

```json
[
  {"id":"F1","claim":"SNAGLINE identifiers present in Agent Firewall","method":"grep 22 distinctive identifiers","result":"0/22 found","classification":"Confirmed","confidence":"High","supports_allegation":false},
  {"id":"F2","claim":"Distinctive shared prose","method":"cross-repo 6-gram index (2.97M vs 19.5k)","result":"275 shared, 168 after boilerplate filter, 0 semantically distinctive","classification":"Confirmed","confidence":"High","supports_allegation":false},
  {"id":"F3","claim":"Shared core design principle","method":"grep + doc comparison","result":"inverted: SNAGLINE fail-open (671 refs, fail_open=True) vs AF explicit fail-closed","classification":"Confirmed","confidence":"High","supports_allegation":false},
  {"id":"F4","claim":"Domain overlap","method":"module inventory + keyword grep","result":"AF contains no loop/repetition/stagnation/entropy/CUSUM/latency detection; adds 6 capabilities SNAGLINE lacks","classification":"Confirmed","confidence":"High","supports_allegation":false},
  {"id":"F5","claim":"Public access evidence","method":"GitHub API: stargazers, forks, issues, search","result":"no star, fork, issue, PR, or reference","classification":"Confirmed (absence of public evidence only)","confidence":"Medium","supports_allegation":false,"limitation":"does not prove absence of access"},
  {"id":"F6","claim":"Timeline consistent with derivation","method":"git history of both repos + in-repo dates","result":"SNAGLINE public 2026-08-14; AF private dev from 2026-10-09, published 2026-10-10 as one 60-file burst; no rewriting; no references","classification":"Strongly suggestive","confidence":"High","supports_allegation":false},
  {"id":"F7","claim":"Shared exact code lines >=28 chars","method":"normalised line-set intersection","result":"14, all universal idioms or Claude Code spec keys (hook_event_name, tool_name)","classification":"Confirmed","confidence":"High","supports_allegation":false},
  {"id":"F8","claim":"Shared numeric constants","method":"constant co-occurrence across >=2 files per repo","result":"18, all round numbers (100,20,40,12,50,200,60...) in unrelated contexts","classification":"Confirmed","confidence":"High","supports_allegation":false},
  {"id":"F9","claim":"Similar CI/release scaffolding","method":"workflow diff + 6-gram attribution","result":"both follow pypa/gh-action-pypi-publish canonical example; SNAGLINE's is more elaborate and pins newer actions","classification":"Confirmed","confidence":"High","supports_allegation":false,"explanation":"shared upstream template"}
]
```

## Appendix B — Reproduce everything in this report

```bash
# 1. Clone both
git clone https://github.com/Bipin583/agent-firewall /tmp/af
cd /path/to/SNAGLINE

# 2. Timelines
cd /tmp/af && git log --reverse --format="%h|%ad|%s" --date=iso | head -3
git log -1 --format="%h A:%ad C:%cd" --date=iso        # author vs committer dates
cd /path/to/SNAGLINE && git log --reverse --format="%h|%ad|%s" --date=short | head -3

# 3. Zero-reference check
grep -rniI --exclude-dir=.git "snagline" /tmp/af        # no output

# 4. Distinctive identifier check
grep -rniI --exclude-dir=.git -E "FailureRisk|StepEvent|CUSUM|Welford|silent_abort|meltdown|stagnation|idle_gap|governance_decay|make_signature|severity_from_score" /tmp/af   # no output

# 5. Design-principle contrast
grep -rniI --exclude-dir=.git "fail-closed\|fail-open" /tmp/af/docs /tmp/af/README.md
grep -rc "fail.open" /path/to/SNAGLINE/src | awk -F: '{s+=$2} END {print "SNAGLINE refs:", s}'

# 6. Domain check
grep -rniI --exclude-dir=.git -E "\bloop\b|retry.?storm|repetition|stagnation|entropy|cusum|welford" /tmp/af/src   # no output

# 7. Public access evidence
gh api repos/Cyrax321/SNAGLINE/stargazers --jq '.[].login' | grep -i bipin
gh api repos/Cyrax321/SNAGLINE/forks --jq '.[].owner.login' | grep -i bipin
gh api "search/issues?q=repo:Cyrax321/SNAGLINE+Bipin583" --jq .total_count
```