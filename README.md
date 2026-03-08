<p align="center">
  <img src="docs/assets/opencastor-logo.png" alt="OpenCastor" width="200"/>
</p>

<h1 align="center">OpenCastor</h1>
<h3 align="center">The Universal Runtime for Embodied AI</h3>

<p align="center">
  <a href="https://pypi.org/project/opencastor/"><img src="https://img.shields.io/pypi/v/opencastor?color=blue&label=PyPI" alt="PyPI"></a>
  <a href="https://github.com/craigm26/OpenCastor/actions"><img src="https://img.shields.io/github/actions/workflow/status/craigm26/OpenCastor/ci.yml?label=CI" alt="CI"></a>
  <a href="https://github.com/craigm26/OpenCastor/blob/main/LICENSE"><img src="https://img.shields.io/github/license/craigm26/OpenCastor?color=green" alt="License"></a>
  <a href="https://pypi.org/project/opencastor/"><img src="https://img.shields.io/pypi/pyversions/opencastor" alt="Python"></a>
  <a href="https://discord.gg/jMjA8B26Bq"><img src="https://img.shields.io/discord/1234567890?label=Discord&color=5865F2" alt="Discord"></a>
  <a href="./sbom/"><img src="https://img.shields.io/badge/SBOM-CycloneDX-blue" alt="SBOM"></a>
</p>

<p align="center">
  <b>167,186 lines of Python · 6,392 tests · Python 3.10–3.13</b><br/>
  <i>Connect any AI model to any robot hardware through a single YAML config.</i>
</p>

---

OpenCastor is a universal runtime for embodied AI. Point it at any LLM (Gemini, GPT-4.1, Claude, Ollama, and 13 more) and any robot body (Raspberry Pi, Jetson, Arduino, ESP32, LEGO) via a single YAML config. The robot answers to WhatsApp, Telegram, Discord, Slack, and Home Assistant — and learns from its own experience.

## Quick Install

```bash
curl -sL opencastor.com/install | bash
castor wizard          # guided setup: API keys, hardware, channels
castor gateway         # start the API gateway + messaging
```

The wizard selects a device-aware stack profile:

<!-- SETUP_CATALOG:BEGIN -->
- `apple_native` — Apple Native (Recommended on eligible Mac)
- `mlx_local_vision` — MLX Local Vision
- `ollama_universal_local` — Ollama Universal Local

| Apple Profile | Meaning |
|---|---|
| `apple-balanced` | Apple Balanced |
| `apple-creative` | Apple Creative |
| `apple-tagging` | Apple Tagging |
<!-- SETUP_CATALOG:END -->

Or with Docker:
```bash
cp .env.example .env && nano .env
docker compose up
```

## Minimal Config

```yaml
rcan_version: "1.1.0"
metadata:
  robot_name: my-robot
agent:
  provider: google
  model: gemini-2.5-flash
drivers:
- id: wheels
  protocol: pca9685
```

That's it. `castor gateway --config my-robot.rcan.yaml` starts the REST API, messaging channels, and the self-improving loop.

## Architecture

```
[ WhatsApp / Telegram / Discord / Slack / Home Assistant ]
                    │
            [ API Gateway ]                   FastAPI (castor/api.py)
                    │
        ┌───────────────────────┐
        │   Safety Layer        │            Anti-subversion, BoundsChecker
        └───────────────────────┘
                    │
    [ Tiered Brain: Gemini / GPT-4.1 / Claude / Ollama … ]
                    │
     ┌──────────────────────────────────┐
     │  Offline Fallback / ALMA Learner │   Episode memory, self-improvement
     └──────────────────────────────────┘
                    │
              [ RCAN Config ]               rcan.dev/spec validation
                    │
    ┌──────────────────────────────────────────────┐
    │  VFS  │  Agents  │  Sisyphus  │  Fleet/Swarm │
    └──────────────────────────────────────────────┘
                    │
    [ PCA9685 / Dynamixel / ROS2 / ESP32 / mock ]
                    │
              [ Your Robot ]
```

### Key Components

| Component | File | Role |
|---|---|---|
| API Gateway | `castor/api.py` | FastAPI entry point — REST, webhooks, SSE |
| BaseProvider | `castor/providers/base.py` | LLM adapter: `think(image, instruction)→Thought` |
| DriverBase | `castor/drivers/base.py` | Hardware driver: `move()`, `stop()`, `health_check()` |
| SisyphusLoop | `castor/learner/sisyphus.py` | PM→Dev→QA→Apply self-improvement cycle |
| ALMAConsolidation | `castor/learner/alma.py` | Cross-episode pattern analysis |
| EpisodeStore | `castor/learner/episode_store.py` | JSON-file episode storage (10k cap, FIFO eviction) |
| EpisodeMemory | `castor/memory.py` | SQLite episode store (10k cap, FIFO) |
| BehaviorRunner | `castor/behaviors.py` | YAML step sequences: waypoint/think/speak/stop |
| CastorFS | `castor/fs/__init__.py` | Unix-style VFS with capability permissions + e-stop |
| BaseChannel | `castor/channels/base.py` | Messaging integration: `start()`, `stop()`, `send_message()` |

## Memory & Learning

OpenCastor robots learn from experience through two interconnected systems.

### Episode Store

Every task execution is recorded as an `Episode`:

```python
@dataclass
class Episode:
    goal: str
    actions: list[dict]       # what the robot did
    sensor_readings: list[dict]
    success: bool
    duration_s: float
    metadata: dict
```

Episodes are stored as JSON files in `~/.opencastor/episodes/` (via `EpisodeStore`) and also in a SQLite database (via `EpisodeMemory`). Both are capped at **10,000 episodes with FIFO eviction** to prevent unbounded disk growth.

### The Sisyphus Loop

After each task, the self-improving PM→Dev→QA→Apply pipeline runs:

```
Episode
  │
  ▼ PM Stage    — analyze failure/inefficiency, score root cause
  │
  ▼ Dev Stage   — generate a config or behavior patch
  │
  ▼ QA Stage    — verify safety bounds, type correctness, semantic sense
  │
  ▼ Apply Stage — write patch to config or learned_behaviors.yaml
```

- **Disabled by default** — opt in via `castor wizard` or `improve.enabled: true` in RCAN
- **4 cost tiers** — free (local Ollama) to ~$5–15/mo (Claude/Gemini)
- **Rollback** — undo any change: `castor improve --rollback <id>`
- **Patch types**: `ConfigPatch` (YAML key/value), `BehaviorPatch` (named rule), `PromptPatch`
- **Applied patches** are deduplicated by `rule_name` — no duplicate accumulation

### ALMA Consolidation

After batches of episodes, the ALMA consolidator runs cross-episode pattern analysis:

```python
# Groups episodes by goal type, finds recurring failure patterns,
# scores by frequency × failure rate, returns top-5 patches.
patches = ALMAConsolidation().consolidate(episodes)
```

Pattern types detected: high failure rate for goal class, common failure action, sensor reading divergence between success/failure, long failure duration vs fast success.

```bash
castor improve --episodes 10    # analyze last 10 episodes
castor improve --status         # view improvement history
```

## Tiered Brain

```
Layer 3: Planner     Claude Opus / Gemini Pro    ~12s     complex multi-step
Layer 2: Fast Brain  HuggingFace / Gemini Flash  ~500ms   Q&A, classification
Layer 1: Reactive    Rule engine                 <1ms     e-stop, bounds
```

80% of decisions are handled free at the reactive layer. Only genuinely complex tasks escalate to the planner.

## Supported AI Providers

| Provider | Models | Best For |
|---|---|---|
| **Google** | `gemini-2.5-flash`, `gemini-2.5-pro` | Multimodal, speed, recommended default |
| **Anthropic** | `claude-opus-4-6`, `claude-sonnet-4-6` | Complex reasoning, safety-critical |
| **OpenAI** | `gpt-4.1`, `gpt-4.1-mini` | Instruction following, 1M context |
| **OpenRouter** | 100+ models | Multi-model fallback, cost optimization |
| **Ollama** | Any local model | Privacy, offline, zero cost |
| **llama.cpp** | GGUF models | Edge inference, Raspberry Pi |
| **MLX** | Apple Silicon native | Mac M1–M4, 400+ tok/s |
| **Groq** | `llama-3.3-70b`, `mixtral-8x7b` | LPU-accelerated, fastest API |
| **HuggingFace** | Any hosted model | Fast brain layer, classification |
| **VLA** | OpenVLA, Octo, pi0 | End-to-end action models |
| **DeepSeek** | `deepseek-chat`, `deepseek-reasoner` | Chinese/multilingual, R1 reasoning |
| **Groq / xAI / Mistral / Kimi / Qwen** | Various | Regional / specialized |

Swap with one YAML change:

```yaml
agent:
  provider: google
  model: gemini-2.5-flash
```

Provider pool with fallback:

```yaml
provider_fallback:
  enabled: true
  provider: ollama
  model: llama3.2:3b
  quota_cooldown_s: 3600
```

## Supported Hardware

| Kit | Price | Preset |
|---|---|---|
| Waveshare AlphaBot / JetBot | ~$45 | `waveshare_alpha.rcan.yaml` |
| Adeept RaspTank / DarkPaw | ~$55 | `adeept_generic.rcan.yaml` |
| SunFounder PiCar-X | ~$60 | `sunfounder_picar.rcan.yaml` |
| Robotis Dynamixel (X-Series) | Varies | `dynamixel_arm.rcan.yaml` |
| LEGO Mindstorms EV3 / SPIKE Prime | $30–150 used | `lego_mindstorms_ev3.rcan.yaml` |
| Arduino + L298N (DIY) | ~$8–15 | `arduino_l298n.rcan.yaml` |
| ESP32 + Motor Driver (DIY) | ~$6–12 | `esp32_generic.rcan.yaml` |
| OAK-4 Pro + Depth + IMU | ~$150 | `oak4_pro.rcan.yaml` |
| DIY (any) | Any | `castor wizard` |

18 presets total in `config/presets/`. OpenCastor explicitly supports second-hand hardware — donated school kits, eBay finds, makerspace bins. If you found it at Goodwill, there's probably a preset for it.

## Messaging Channels

| Channel | Auth | Notes |
|---|---|---|
| WhatsApp | neonize QR or Twilio | group JID routing per robot |
| Telegram | Bot token | |
| Discord | Bot token | |
| Slack | App token | |
| Home Assistant | HA webhook | |
| MQTT | Broker URL | |

## Safety

Defense-in-depth, RCAN spec compliant:

| Layer | What It Does |
|---|---|
| **Prompt injection guard** | `_check_instruction_safety()` on every `think()` call |
| **Physical bounds** | Dynamixel 0–4095, PCA9685 duty-cycle clamping, `BoundsChecker` |
| **Rate limiting** | 5 req/s/IP on `/api/command`, 10 req/min/sender on webhooks |
| **E-stop** | `POST /api/stop`, `fs.estop()`, or any messaging channel |
| **GuardianAgent** | Veto authority over unsafe actions |
| **Audit chain** | Hash-chained tamper-evident log; optional quantum commitment layer |

```bash
castor audit --verify        # verify chain integrity
castor approvals             # view/approve pending dangerous commands
```

## CLI Reference

```bash
# Setup
castor wizard                       # interactive setup
castor doctor                       # system health check
castor fix                          # auto-fix common issues

# Run
castor run --config robot.rcan.yaml          # perception-action loop
castor gateway --config robot.rcan.yaml      # API + channels
castor dashboard                             # Streamlit web UI
castor status                                # provider/channel readiness

# Fleet
castor fleet [--watch]              # discover + monitor robots
castor swarm status/command/sync    # multi-node ops
castor deploy pi@192.168.1.10 --config robot.rcan.yaml

# Learning
castor improve --episodes 10        # analyze + apply improvements
castor improve --rollback <id>      # undo a patch

# Safety
castor audit --verify               # verify audit chain
castor approvals                    # review pending commands
castor token --role operator        # issue JWT
```

Full reference: [`docs/claude/cli-reference.md`](docs/claude/cli-reference.md)

## What's New in v2026.3.8.3

- **EpisodeStore FIFO cap** — memory store now enforces 10k episode limit with automatic FIFO eviction; prevents unbounded SQLite growth
- **ApplyStage dedup** — `improvement_history.json` capped at 1k entries; behavior rules deduplicated by name for a leaner learning pipeline
- **Timezone-aware datetimes** — deprecated `datetime.utcfromtimestamp()` replaced throughout with timezone-aware equivalents
- **README refresh** — architecture diagram, Memory & Learning section, and overall structure tightened

_Previous: [v2026.3.8.2](#) — animated robot face, gamepad, closed captions, brain model visibility, dashboard UX_

## Contributing

OpenCastor is Apache 2.0, community-driven.

- **Discord**: [discord.gg/jMjA8B26Bq](https://discord.gg/jMjA8B26Bq)
- **Issues**: [GitHub Issues](https://github.com/craigm26/OpenCastor/issues)
- **PRs**: See [CONTRIBUTING.md](CONTRIBUTING.md)

Reference docs: [`docs/claude/structure.md`](docs/claude/structure.md) · [`docs/claude/api-reference.md`](docs/claude/api-reference.md) · [`docs/claude/env-vars.md`](docs/claude/env-vars.md)

---

<p align="center">
  Built on the <a href="https://rcan.dev/spec/">RCAN Spec</a> by <a href="https://github.com/craigm26">Craig Merry</a> · Apache 2.0
</p>
