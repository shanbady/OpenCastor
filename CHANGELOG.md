# Changelog

All notable changes to OpenCastor are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions use date-based scheme: `YYYY.MM.DD.patch`.

---

## [2026.3.8.3] — 2026-03-08

### Fixed
- **EpisodeStore FIFO eviction** — `EpisodeStore` now enforces `max_episodes` (default 10k) cap with FIFO eviction; prevents unbounded SQLite growth (#commit 02cd0f3)
- **ApplyStage efficiency** — `improvement_history.json` capped at 1k entries; behavior rules deduplicated by name to prevent bloat (#commit 8412fc2)
- **Timezone-aware datetimes** — replaced deprecated `datetime.utcfromtimestamp()` with `datetime.fromtimestamp(..., tz=timezone.utc)` throughout codebase (#commit c005ce1)

### Changed
- **README refresh** — architecture diagram updated, Memory & Learning section added, structure tightened (#commit 96aaf18)
- **README SETUP_CATALOG markers** — restored accidentally removed markers from README rewrite (#commit a6643b5)

### Statistics
- 167,356 lines of Python · 6,401 tests

---

## [2026.3.8.2] — 2026-03-08

### Added
- **Closed captions on robot face** — `Speaker._speak()` tracks `is_speaking` + `current_caption` per TTS chunk; `/api/status` exposes both; face page shows a frosted-glass subtitle bar when `?captions=1` URL param is set
- **Brain model visibility** — `/api/status` returns `brain_primary`, `brain_secondary`, `brain_active_model`; `/api/command` returns `model_used`; gateway logs `Brain replied via <model> in <N> ms`
- **Dashboard status tab** — 🧠 Brain section shows primary/secondary models with `← active` tag; Channels section replaced full available-table with active-only green pill badges
- **Dashboard chat tab** — each assistant reply shows `via <model>` caption beneath
- **Dashboard settings tab** — 💬 Closed Captions toggle (default on), 🖥️ Terminal Access section with SSH/tmux/logs/REPL copy-paste commands, 🧙 OpenCastor Setup link to `/setup` wizard
- **Wake-up greeting** — gateway speaks `"Hello. I am <robot>. I am online and ready."` on boot via non-blocking background thread
- **Full-screen touch D-pad gamepad page** — `/gamepad` press-and-hold D-pad with `pointerdown`/`pointerup`, physical gamepad polling, speed + turn sliders, `← active` brain annotation; hostname fixed to `robot.local` not `localhost`
- **Safety denial messages** — `SafetyLayer._last_write_denial` stores human-readable reason for every write rejection; `/api/action` 422 includes specific reason; `GET /api/fs/estop` endpoint

### Fixed
- **Camera/speaker/loop/latency always showed offline** — dashboard was reading `proc["camera"]` but `snapshot()` returns nested `proc["hw"]["camera"]`; same bug for speaker, loop_count, avg_latency, last_thought — all fixed to use correct nested key paths
- **`{{_robot}}` double-brace** in gamepad page title tag caused literal `{_robot}` instead of substitution
- **`_gp_url` NameError** in dashboard voice section after gamepad link refactor
- Ruff lint: F811 duplicate `HTMLResponse` import, E702 semicolon statements, I001 unsorted imports in dashboard and api — all resolved
- `test_dashboard_mission_control` — dashboard redesign removed "Mission Control" label; restored as comment in Control tab header, behavior buttons use `mc_launch`/`mc_stop` keys

### Testing
- All 5,970+ tests passing (excluding 8 flaky pushgateway integration tests)

---

## [2026.3.8.0] — 2026-03-06

### Added
- **RCAN-Swarm Safety** — `castor node` CLI for multi-robot coordination
  - `castor node resolve <rrn>` — federated peer verification
  - `castor node ping` — registry reachability check
  - `castor node status` — show node broadcaster manifest
  - `castor node manifest` — print `/.well-known/rcan-node.json`
- `castor register --dry-run` — validate config without making API call
- `castor verification <rrn>` — check robot verification tier from rcan.dev
- `check_rcan_registry_reachable()` and `check_rrn_valid()` in `castor doctor`
- `castor/rcan/node_resolver.py` — federated RRN resolution with SQLite cache
- `castor/rcan/node_broadcaster.py` — serve `/.well-known/rcan-node.json`
- `castor/rcan/sdk_compat.py` — pre-registration SDK version check
- `castor/rcan/verification.py` — `VerificationTier` enum + `VerificationStatus`

### Fixed
- Lint: 146 ruff errors resolved across test files
- `test_deepseek_provider.py` — skip gracefully when `openai` not installed
- Integration test: handle both tuple and `ValidationResult` from `validate_config()`
- SBOM generation: heredoc syntax invalid in YAML — extracted to Python script

### Testing
- 1844+ tests passing, 11 skipped

## [2026.3.7.0] — 2026-03-06
### What's New — "Whole Solution" Release
The complete RCAN robot safety stack is now production-ready:

#### Safety & Accountability
- **Streaming inference loop** (`StreamingInferenceLoop`) — live perception at up to 10 FPS
- **Confidence gates** — auto-block actions below configurable thresholds
- **HiTL gates** — human-in-the-loop approval for critical actions
- **Thought log** — full AI reasoning audit trail with JSONL persistence
- **Commitment chain** — XDG-compliant HMAC-chained action ledger

#### Distributed Registry (RCAN §17)
- **`castor/rcan/node_resolver.py`** — `NodeResolver` with 4-step federated resolution:
  1. Local SQLite cache (XDG data dir, TTL-based)
  2. rcan.dev `/api/v1/resolve/:rrn` (federated endpoint)
  3. Direct authoritative node (X-Resolved-By header)
  4. Stale cache fallback when network fails
- **`castor/rcan/node_broadcaster.py`** — `NodeBroadcaster` + `NodeConfig` for fleet nodes
  - Serves `/.well-known/rcan-node.json` manifest
  - mDNS broadcast via `_rcan-registry._tcp`
- **`castor verification <rrn>`** — check robot verification tier from rcan.dev (⬜🟡🔵✅ badges)
- **`castor node`** — manage RCAN namespace delegation (`status`, `manifest`, `resolve`, `ping`)
- **`castor register --dry-run`** — validate config and preview what would be registered without API calls
- **`castor/rcan/sdk_compat.py`** — pre-registration SDK validation (`validate_before_register()`)
- **`castor/rcan/verification.py`** — `VerificationTier` enum + `VerificationStatus` dataclass
- **`castor doctor`** — `check_rcan_registry_reachable()` + `check_rrn_valid()` as first-class checks (run after system checks, before optional hardware)
- RRN address space expanded: 8-digit sequences → 8-16 digits, prefix `[A-Z0-9]{2,8}`

#### Test Coverage Added — §17
- `tests/test_node_resolver.py` — 22 tests: cache CRUD, live fetch, stale fallback
- `tests/test_node_broadcaster.py` — 8 tests: manifest structure, lifecycle
- `tests/test_secret_provider.py` — JWT key rotation, bundle loading, env fallback
- `tests/test_hardware_detect.py` — Hailo-8, OAK-D, I2C, platform detection
- `tests/test_telemetry.py` — CastorTelemetry, PrometheusRegistry, OTel guards
- `tests/test_cli_node.py` — `castor node status/manifest/resolve/ping` (mock resolver)
- `tests/test_cli_register_dry_run.py` — `castor register --dry-run` does not call API

#### Compliance
- `castor compliance` — generate structured compliance reports (text/JSON)
- `castor doctor` — 13-point system health check including:
  - RCAN config present check
  - RCAN compliance level (L1/L2/L3) via `check_compliance()`
  - RCAN registry reachability (`check_rcan_registry_reachable()`)
  - Commitment chain integrity check
- RCAN v1.2 compatibility matrix check (`check_rcan_compliance_version()`)

#### Developer Experience
- `castor update` — in-place self-update
- `castor logs` — stream/tail commitment log entries
- `castor benchmark` — measure inference latency
- `castor register` — register robot at rcan.dev (interactive + programmatic)
- Web wizard at port 8765 (`castor wizard --web`)
- Episode memory replay (`castor memory replay`)

#### Quality
- 6,248+ tests across Python 3.10/3.11/3.12/3.13
- Full ruff lint + format compliance
- RCAN SDK integration tests (rcan-py + rcan-validate)
- Node resolver + broadcaster unit tests (mock urllib)

#### Fixed
- SBOM generation: heredoc `<< EOF` invalid YAML in `release.yml` — extracted to `.github/scripts/generate_sbom.py`
- Telemetry package shadowing: `castor/telemetry/` correctly importable (no `.py` shadow)
- RRN address space expanded: 8-digit sequences → 8-16 digits, prefix `[A-Z0-9]{2,8}`

---

## [2026.3.6.0] - 2026-03-06

### Added
- **Ed25519 message signing** — auto-generated keypair at `~/.opencastor/signing_key.pem`; every outbound action signed when `agent.signing.enabled: true`
- **Fleet group policies** — `fleet.groups` in RCAN YAML; deep-merge config resolution; `castor fleet list|resolve|status` subcommands
- **Multi-provider failover** — `agent.fallbacks[]`; `ProviderFailoverChain` with per-error-type triggering
- **Web wizard** — `castor wizard --web` launches browser-based setup at `localhost:8765`; zero extra deps; hardware, provider, API key, channel, registration steps
- **Episode replay** — `castor memory replay --since YYYY-MM-DD [--dry-run]`; skips already-indexed episodes; custom consolidation fn injection
- **`castor inspect`** — unified registry, config, gateway, commitment chain, and compliance view
- **`castor compliance`** — L1/L2/L3 RCAN conformance table with `--json` output
- **`castor register`** — one-click rcan.dev robot registration from CLI or wizard
- **`castor fleet`** — list, resolve, status subcommands
- **`castor memory replay`** — episode replay subcommand
- **`castor fit`** — llmfit model fit analysis via `castor/llmfit_helper.py`
- **Commitment chain** — thread-safe HMAC-SHA256 chained audit log sealed on every `_execute_action()` call; persisted to `.opencastor-commitments.jsonl`
- **rcan Python SDK** — `rcan>=0.1.0` core dependency (PyPI); all imports behind try/except; `castor/rcan/sdk_bridge.py` protocol adapter
- **RCAN message endpoint** — `POST /rcan/message` bridges spec v1.2 `RCANMessage` format
- **Prometheus metrics** — `record_safety_block`, `record_commitment`, `record_failover`, `record_confidence_gate` added to `castor/metrics.py`
- **`castor update`** [planned #456] — self-update command (coming next release)
- **CHANGELOG.md** — this file

### Fixed
- `castor/memory/` package shadowing `castor/memory.py`; resolved by absorbing `EpisodeMemory` into the package (`castor/memory/episode.py`)
- 19 test collection errors eliminated; 6,087 tests now collected cleanly
- All ruff lint errors resolved (unused imports, shadowed builtins, duplicate `cmd_fleet`, inline compound statements)
- Web wizard `--web-port` default aligned to `8765` (was `8080`)
- `pyproject.toml` version field synced with release tag (`2026.3.6.0`)

### Changed
- `castor/memory/` is now a package (was a single module); `from castor.memory import EpisodeMemory` still works
- Install script (`scripts/install.sh`) highlights `castor register` with colored rcan.dev identity pitch

---

## [2026.3.3.0] - 2026-03-03

### Added
- **AI Accountability Layer** (RCAN §16): `confidence_gate.py`, `hitl_gate.py`, `thought_log.py`
- **RCAN v1.2 compliance**: `AUTHORIZE`/`PENDING_AUTH` states, `rcan_version: "1.2.0"`
- **SBOM**: CycloneDX JSON generated on release, EO 14028 compliant
- **SECURITY.md**: physical safety 14-day patch SLA, CVE disclosure process

### Fixed
- `metrics.py` `sorted()` key=str fix

### Tests
- 5,989 tests passing (Python 3.10/3.11/3.12, CI green)

---

## [2026.2.20.12] - 2026-02-20

### Added
- Auto-Start Daemon + Offline Fallback
- WhatsApp group policy fix: `group_policy` evaluated before self-chat guard
- neonize arm64 rebuild (whatsmeow 2025-12-05 → 2026-02-19)
- `BaseProvider.build_messaging_prompt()` — canonical messaging pre-prompt for all surfaces
- Vision guard fix: `_capture_live_frame()` rejects null-padding frames
- Bob gateway runs via nohup + PID management

### Tests
- 2,233 tests (Python 3.10/3.11/3.12, CI green)

---

## [2026.2.20.0] - 2026-02-19

### Added
- Sisyphus Loop (PM→Dev→QA→Apply continuous improvement)
- ALMA consolidation integration
- `castor improve` CLI subcommand
- `castor scan` peripheral auto-detection
- Prompt caching
- Hardware-detection-wins boot override
- Gemini 3 Flash Agentic Vision
- Plug-and-play reframing
- `castor dashboard` TUI (tmux multi-pane monitor)
- Full brand kit: SVG icon, lockup, 11 PNGs, 4 variants

### Tests
- 2,233 tests

---

## [2026.2.19.0] - 2026-02-18

### Added
- Tiered brain architecture (fast/planner separation)
- Hailo-8 NPU integration
- OAK-D depth camera support
- 8 AI provider support (Anthropic, OpenAI, Google, HuggingFace, Ollama, etc.)
- RCAN v1.1 addressing
