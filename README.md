# ⚕ Hermes Incident Commander

> **An autonomous SRE agent that detects, diagnoses, and heals production infrastructure — then learns from every incident it resolves.**

Built on [Hermes Agent](https://hermes-agent.nousresearch.com) by NousResearch.
Submitted for the *"Show us what Hermes Agent can do"* challenge.

---

## The Problem

When a production server goes down at 3 AM, an on-call engineer has to:

1. Wake up, check alerts
2. SSH in, run diagnostics manually
3. Piece together root cause from logs
4. Apply a fix - hopefully the right one
5. Verify it worked
6. Write a post-mortem nobody will read

**Mean time to resolve (MTTR) for P0 incidents averages 45–60 minutes.** Much of that is humans doing things a sufficiently capable agent could do faster and better.

Hermes Incident Commander does all of it - autonomously, in minutes, getting smarter with each incident it handles.

---

## Demo

```bash
# Install dependencies
pip install anthropic rich

# Set your API key
export ANTHROPIC_API_KEY=sk-ant-...

# Run a demo incident (disk full scenario)
python demo/demo_incident.py --scenario disk-full-logs

# Try other scenarios
python demo/demo_incident.py --scenario svc-crash-nginx
python demo/demo_incident.py --scenario cpu-runaway-process
```

**What you'll see:**
- Hermes detects the incident and classifies severity (P0/P1/P2/P3)
- Runs parallel diagnostics across CPU, memory, disk, and services
- Identifies root cause with explicit reasoning
- Applies the safest effective fix
- Verifies the fix worked
- Writes a structured post-incident report to `~/.hermes/incidents/`
- Creates a **new prevention skill** in `~/.hermes/skills/` so it handles this faster next time

---

## How It Uses Every Hermes Feature

This project was designed to push every capability of Hermes Agent:

| Hermes Feature | How It's Used |
|---|---|
| **Persistent Memory** | Builds a system topology map over time. Learns which services fail together, time-of-day patterns, and which remediations work on YOUR infrastructure. |
| **Skill Auto-Creation** | After every novel incident, writes a new `SKILL.md` prevention playbook. Hermes gets measurably better at your stack over weeks. |
| **Cron Scheduler** | Every 5 min: critical health check. Every hour: full audit. Daily 08:00: morning briefing to Telegram. |
| **Gateway (Telegram/Discord)** | Real-time P0 alerts, resolution notices, and daily briefings delivered to your phone. |
| **Subagent Spawning** | For multi-service environments, spawns parallel subagents to investigate nginx, database, and application layers simultaneously. |
| **Session Search (FTS5)** | "Have we seen this error before?" - searches past incidents for matching patterns. |
| **execute_code** | Collapses multi-step diagnostic pipelines into single inference turns, dramatically reducing latency. |
| **MCP Integration** | Connects to cloud provider APIs (AWS/GCP/Azure MCP servers) for auto-scaling and cloud-native remediation. |

---

## Architecture

```mermaid
flowchart TD
    ALERT([🚨 Incident Alert]) --> DETECT

    DETECT["🔍 DETECT <br/> Gather system vitals <br/> CPU • Memory • Disk • Services"]
    TRIAGE["⚖️ TRIAGE <br/> Classify severity\nP0 · P1 · P2 · P3"]
    DIAGNOSE["🔬 DIAGNOSE <br/> Root cause analysis\nLogs · Processes · Stack traces"]
    REMEDIATE["🔧 REMEDIATE<br/>Apply safest fix<br/>Tier 1 → 2 → 3"]
    VERIFY["✅ VERIFY\nConfirm resolution\nBefore vs after metrics"]

    DETECT --> TRIAGE --> DIAGNOSE --> REMEDIATE --> VERIFY

    CRON["⏱️ CRON\nEvery 5 min: health check\nEvery hour: full audit\nDaily 08:00: briefing"]
    CRON -->|triggers| DETECT

    LEARN["🧠 LEARN\nWrite post-incident report\nCreate prevention SKILL.md\nUpdate MEMORY.md\nSearch past incidents (FTS5)"]
    VERIFY --> LEARN

    GATEWAY["📲 GATEWAY\nTelegram · Discord · Slack"]
    TRIAGE -->|"🚨 P0/P1 alert"| GATEWAY
    VERIFY -->|"✅ resolved"| GATEWAY
    CRON -->|"📋 daily briefing"| GATEWAY

    style DETECT fill:#1e3a5f,color:#fff
    style TRIAGE fill:#7b2d00,color:#fff
    style DIAGNOSE fill:#1e3a5f,color:#fff
    style REMEDIATE fill:#1a4731,color:#fff
    style VERIFY fill:#1a4731,color:#fff
    style LEARN fill:#3d2068,color:#fff
    style CRON fill:#2d2d2d,color:#fff
    style GATEWAY fill:#2d2d2d,color:#fff
    style ALERT fill:#7b2d00,color:#fff
```

---

## Project Structure

```mermaid
graph LR
    ROOT["📁 hermes-incident-commander"]

    ROOT --> SKILLS["📁 skills/"]
    ROOT --> ENVS["📁 environments/"]
    ROOT --> DEMO["📁 demo/"]
    ROOT --> TESTS["📁 tests/"]
    ROOT --> DOCS["📁 docs/"]
    ROOT --> REQ["📄 requirements.txt"]

    SKILLS --> SKILL_MD["📄 incident-commander/SKILL.md\n← install into ~/.hermes/skills/"]

    ENVS --> ENV_PY["🐍 incident_env.py\n← Atropos RL environment"]
    ENVS --> ENV_CFG["⚙️ incident_config.yaml\n← training configuration"]

    DEMO --> DEMO_PY["🐍 demo_incident.py\n← standalone demo"]

    TESTS --> TEST_PY["🐍 test_incident_env.py\n← pytest test suite"]

    DOCS --> SETUP["📄 SETUP.md"]
    DOCS --> WRITEUP["📄 WRITEUP.md"]

    style ROOT fill:#1e3a5f,color:#fff
    style SKILL_MD fill:#1a4731,color:#fff
    style ENV_PY fill:#3d2068,color:#fff
    style DEMO_PY fill:#7b2d00,color:#fff
    style TEST_PY fill:#2d2d2d,color:#fff
```

---

## Installation (Full Hermes Setup)

### 1. Install Hermes Agent

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
```

### 2. Configure Hermes

```bash
hermes setup        # Interactive setup wizard
hermes model        # Choose your model (Nous Portal recommended)
hermes gateway setup  # Connect Telegram/Discord for alerts
```

### 3. Install the Incident Commander Skill

```bash
# Copy the skill to Hermes's skills directory
cp -r skills/incident-commander ~/.hermes/skills/

# Verify it's loaded
hermes
> /skills
```

### 4. Set Up Monitoring Cron Jobs

In your Hermes conversation:
```
Set up incident monitoring: run a health check every 5 minutes and alert me
on Telegram if anything is P0 or P1. Send me a daily briefing at 08:00.
```

Hermes will install the cron jobs automatically.

### 5. Run the RL Training Environment (Optional)

```bash
# Install Atropos
pip install atroposlib

# Generate SFT training data
python environments/incident_env.py process --config environments/incident_config.yaml

# Full RL training (requires VLLM)
python environments/incident_env.py serve --config environments/incident_config.yaml
```

---

## Reward Function (for RL Training)

The training environment uses a multi-component reward that captures real SRE quality:

```mermaid
pie title Reward Components
    "Resolution — Did the incident get fixed?" : 50
    "RCA Quality — Root cause explained?" : 15
    "Report Quality — Post-mortem written?" : 15
    "Skill Created — Prevention skill added?" : 10
    "Response Speed — Fast MTTR?" : 5
    "Tool Efficiency — Minimal tool calls?" : 5
```

---

## Incident Scenarios (Training Scenarios)

| ID | Severity | Category | Description |
|---|---|---|---|
| `svc-crash-nginx` | P0 | service | nginx crashed, website unreachable |
| `disk-full-logs` | P1 | disk | 95% disk usage from exploded log files |
| `memory-leak-process` | P1 | memory | Mystery process eating 150MB+ |
| `cpu-runaway-process` | P2 | cpu | 95% CPU from runaway computation |
| `failed-systemd-unit` | P2 | service | Custom worker service in failed state |

---

## Running Tests

```bash
# Install test dependencies
pip install pytest pytest-asyncio

# Run full test suite
pytest tests/ -v

# Run specific test classes
pytest tests/test_incident_env.py::TestScenarioDefinitions -v
pytest tests/test_incident_env.py::TestRewardFunction -v
pytest tests/test_incident_env.py::TestSkillFile -v
```

---

## Why This Wins

1. **Real problem, real impact.** P0 incidents cost companies thousands of dollars per minute. Shaving 30 minutes off MTTR with an autonomous agent is immediately valuable.

2. **Uses every Hermes capability.** Memory, skills, cron, gateway, subagents, session search, execute_code - all integrated into a coherent, meaningful workflow.

3. **Self-improving.** The longer Hermes runs, the better it gets at your specific infrastructure. This is Hermes's core promise - "the agent that grows with you" - demonstrated concretely.

4. **Closes the training loop.** The Atropos RL environment means this isn't just a demo - it's a path to training models that are genuinely better at agentic SRE tasks.

5. **Ships with working code.** The demo runs standalone, the tests pass, and the skill file installs in one command.

---

## License

MIT

---

*Built with [Hermes Agent](https://hermes-agent.nousresearch.com) - the agent that grows with you.*
