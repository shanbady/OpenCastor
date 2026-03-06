"""castor.doctor — health check: hardware, config, deps, gateway, RCAN compliance."""

from __future__ import annotations

import importlib
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

try:
    from rich.console import Console
    from rich.table import Table

    HAS_RICH = True
except ImportError:
    HAS_RICH = False


@dataclass
class CheckResult:
    name: str
    status: str  # "ok" | "warn" | "fail" | "skip"
    detail: str = ""
    fix: str = ""


@dataclass
class DoctorReport:
    checks: List[CheckResult] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "ok")

    @property
    def warn_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "warn")

    @property
    def fail_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "fail")

    @property
    def all_ok(self) -> bool:
        return self.fail_count == 0


# ── Individual checks ────────────────────────────────────────────────────────


def _check_python() -> CheckResult:
    v = sys.version_info
    if v >= (3, 10):
        return CheckResult("Python version", "ok", f"{v.major}.{v.minor}.{v.micro}")
    return CheckResult(
        "Python version",
        "fail",
        f"{v.major}.{v.minor}.{v.micro} — requires 3.10+",
        fix="Install Python 3.10 or newer",
    )


def _check_dep(pkg: str, import_name: Optional[str] = None) -> CheckResult:
    import_name = import_name or pkg
    try:
        mod = importlib.import_module(import_name)
        ver = getattr(mod, "__version__", "?")
        return CheckResult(f"dep:{pkg}", "ok", f"v{ver}")
    except ImportError:
        return CheckResult(f"dep:{pkg}", "warn", "not installed", fix=f"pip install {pkg}")


def _check_config() -> CheckResult:
    candidates = [
        Path.cwd() / "bob.rcan.yaml",
        Path.cwd() / "robot.rcan.yaml",
        Path.home() / ".opencastor" / "config.yaml",
    ]
    for p in candidates:
        if p.exists():
            return CheckResult("RCAN config", "ok", str(p))
    return CheckResult(
        "RCAN config",
        "warn",
        "no .rcan.yaml found",
        fix="Run: castor wizard  or  castor wizard --web",
    )


def _check_opencastor_dir() -> CheckResult:
    d = Path.home() / ".opencastor"
    if d.exists():
        files = list(d.iterdir())
        return CheckResult("~/.opencastor/", "ok", f"{len(files)} files")
    return CheckResult(
        "~/.opencastor/", "warn", "directory missing", fix="Run: castor wizard to create it"
    )


def _check_signing_key() -> CheckResult:
    key = Path.home() / ".opencastor" / "signing_key.pem"
    if key.exists():
        return CheckResult("Ed25519 signing key", "ok", str(key))
    return CheckResult(
        "Ed25519 signing key",
        "warn",
        "not generated",
        fix="Enable signing in RCAN YAML: agent.signing.enabled: true",
    )


def _check_env_var(var: str, sensitive: bool = True) -> CheckResult:
    val = os.environ.get(var)
    if val:
        display = f"{val[:4]}…" if sensitive and len(val) > 4 else val
        return CheckResult(f"env:{var}", "ok", display)
    # Check ~/.opencastor/env
    env_file = Path.home() / ".opencastor" / "env"
    if env_file.exists() and var in env_file.read_text():
        return CheckResult(f"env:{var}", "ok", "set in ~/.opencastor/env")
    return CheckResult(f"env:{var}", "warn", "not set")


def _check_hardware_hailo() -> CheckResult:
    if Path("/dev/hailo0").exists():
        return CheckResult("Hailo-8 NPU", "ok", "/dev/hailo0")
    return CheckResult("Hailo-8 NPU", "skip", "not detected (optional)")


def _check_hardware_oakd() -> CheckResult:
    try:
        import depthai as dai  # noqa: F401

        return CheckResult("OAK-D (DepthAI)", "ok", f"depthai v{dai.__version__}")
    except ImportError:
        if shutil.which("lsusb"):
            res = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=5)
            if "03e7" in res.stdout:  # Intel Myriad X VID
                return CheckResult(
                    "OAK-D (DepthAI)",
                    "warn",
                    "device detected, depthai not installed",
                    fix="pip install depthai",
                )
        return CheckResult("OAK-D (DepthAI)", "skip", "not detected (optional)")


def _check_gateway(port: int = 18789) -> CheckResult:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return CheckResult("Gateway port", "ok", f"localhost:{port} reachable")
    except (ConnectionRefusedError, OSError):
        return CheckResult(
            "Gateway port",
            "warn",
            f"localhost:{port} not reachable",
            fix="castor run --config <yaml>",
        )


def _check_rcan_compliance() -> CheckResult:
    try:
        from castor.rcan.sdk_bridge import check_compliance

        level = check_compliance()
        status = "ok" if level >= 1 else "warn"
        return CheckResult("RCAN compliance", status, f"L{level}")
    except Exception as e:
        return CheckResult(
            "RCAN compliance", "warn", f"could not check: {e}", fix="castor compliance"
        )


def _check_commitments() -> CheckResult:
    try:
        from castor.rcan.commitment_chain import get_commitment_chain

        chain = get_commitment_chain()
        count = chain.count() if hasattr(chain, "count") else "?"
        return CheckResult("Commitment chain", "ok", f"{count} records")
    except Exception:
        return CheckResult("Commitment chain", "skip", "rcan not installed (optional)")


# ── Main ─────────────────────────────────────────────────────────────────────


def run_doctor(full: bool = False) -> DoctorReport:
    report = DoctorReport()
    add = report.checks.append

    # Core
    add(_check_python())
    add(_check_config())
    add(_check_opencastor_dir())
    add(_check_signing_key())

    # Core deps
    for pkg in ["anthropic", "openai", "httpx", "yaml", "rich", "zeroconf"]:
        add(_check_dep(pkg, import_name="yaml" if pkg == "yaml" else pkg))

    # Optional SDK
    add(_check_dep("rcan"))

    # Env vars
    for var in ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "RCAN_API_KEY"]:
        add(_check_env_var(var))

    # Hardware (optional)
    add(_check_hardware_hailo())
    add(_check_hardware_oakd())

    # Runtime
    add(_check_gateway())

    if full:
        add(_check_rcan_compliance())
        add(_check_commitments())

    return report


def print_report(report: DoctorReport) -> None:
    STATUS_ICON = {"ok": "✅", "warn": "⚠️ ", "fail": "❌", "skip": "⏭️ "}
    STATUS_COLOR = {"ok": "green", "warn": "yellow", "fail": "red", "skip": "dim"}

    if HAS_RICH:
        con = Console()
        t = Table(show_header=True, header_style="bold dim", box=None, pad_edge=False)
        t.add_column("", width=2)
        t.add_column("Check", style="bold")
        t.add_column("Detail")
        t.add_column("Fix", style="dim")
        for c in report.checks:
            icon = STATUS_ICON.get(c.status, "?")
            color = STATUS_COLOR.get(c.status, "white")
            t.add_row(icon, f"[{color}]{c.name}[/{color}]", c.detail, c.fix)
        con.print(t)
        con.print()
        summary_color = (
            "green" if report.all_ok else ("yellow" if report.fail_count == 0 else "red")
        )
        con.print(
            f"[{summary_color}]{'✅ All good' if report.all_ok else '⚠️  Issues found'}[/{summary_color}]"
            f" — {report.ok_count} ok, {report.warn_count} warnings, {report.fail_count} failures"
        )
    else:
        for c in report.checks:
            icon = STATUS_ICON.get(c.status, "?")
            line = f"{icon} {c.name}: {c.detail}"
            if c.fix:
                line += f"  → {c.fix}"
            print(line)
        print(f"\n{report.ok_count} ok, {report.warn_count} warnings, {report.fail_count} failures")
