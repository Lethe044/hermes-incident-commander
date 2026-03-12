# Setup Guide — Hermes Incident Commander

## Quick Start (Demo Only — No Hermes Required)

```bash
# 1. Clone the repo
git clone https://github.com/YOUR_USERNAME/hermes-incident-commander
cd hermes-incident-commander

# 2. Install demo dependencies
pip install anthropic rich

# 3. Set API key
export ANTHROPIC_API_KEY=sk-ant-...

# 4. Run a demo incident
python demo/demo_incident.py --scenario disk-full-logs
```

---

## Full Setup (With Hermes Agent)

### Step 1: Install Hermes Agent

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
```

This installs Python 3.11, uv, and Hermes. Takes about 60 seconds.

### Step 2: Run Setup Wizard

```bash
hermes setup
```

Choose your model provider:
- **Nous Portal** (recommended) — OAuth login, access to Hermes models
- **OpenRouter** — API key, access to all models
- **Custom endpoint** — VLLM, Ollama, or any OpenAI-compatible API

### Step 3: Install the Incident Commander Skill

```bash
# Copy skill to Hermes's skills directory
cp -r skills/incident-commander ~/.hermes/skills/

# Verify it loaded
hermes
# In the Hermes CLI:
> /skills
# You should see "incident-commander" in the list
```

### Step 4: Set Up Messaging Gateway (for alerts)

```bash
hermes gateway setup
```

Follow the prompts to connect:
- **Telegram** (recommended) — Create a bot via @BotFather, paste the token
- **Discord** — Create a bot in Discord Developer Portal
- **Slack** — Create a Slack app with webhook URL

Then start the gateway:
```bash
hermes gateway install  # Installs as systemd service (runs on boot)
hermes gateway          # Or run manually
```

### Step 5: Configure Monitoring Cron Jobs

Open a Hermes conversation and say:

```
Set up incident monitoring with these schedules:
- Every 5 minutes: run a critical health check, alert me on Telegram if severity is P0 or P1
- Every hour: run a comprehensive system audit and save it to ~/.hermes/incidents/
- Every day at 08:00: send me a morning briefing on Telegram with any trends or risks
```

Hermes will create the cron jobs automatically.

### Step 6: Test It

```bash
# Trigger a test incident
hermes
> I'm testing the incident response system. 
> Please investigate the current system health and generate a test incident report.
```

---

## RL Training Setup (Advanced)

### Prerequisites
- Hermes Agent installed
- Atropos: `pip install atroposlib`
- For GPU training: VLLM installed

### Generate SFT Training Data

```bash
# Set your API key
export OPENROUTER_API_KEY=sk-or-...

# Generate 100 training trajectories (SFT mode)
python environments/incident_env.py process \
    --config environments/incident_config.yaml \
    --num-episodes 100
```

Output: ShareGPT-format JSONL in `~/.hermes/trajectories/`

### Full RL Training

```bash
# Edit config to point to your VLLM server
vim environments/incident_config.yaml
# Set: server_type: vllm, model_name: your-model

# Start training
python environments/incident_env.py serve \
    --config environments/incident_config.yaml
```

Metrics logged to Weights & Biases (configure `wandb.entity` in config).

---

## Troubleshooting

**"hermes: command not found"**
```bash
source ~/.bashrc  # or ~/.zshrc
# Or: ~/.local/bin/hermes
```

**"Skill not loading"**
```bash
ls ~/.hermes/skills/incident-commander/SKILL.md  # Should exist
hermes doctor  # Runs full diagnostics
```

**"Cron jobs not running"**
```bash
hermes gateway  # Gateway must be running for cron
systemctl status hermes-gateway  # If installed as service
```

**Demo script errors**
```bash
pip install anthropic rich  # Make sure dependencies are installed
python -c "import anthropic; print(anthropic.__version__)"  # Should be >= 0.49
```
