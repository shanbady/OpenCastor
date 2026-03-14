"""
SO-ARM101 interactive assembly guide.

CLI: castor arm assemble [--arm follower|leader|both]
Web wizard: embedded as steps when SO-ARM101 selected in hardware picker.
"""

from __future__ import annotations

import textwrap
from typing import Callable

from castor.hardware.so_arm101.constants import (
    FOLLOWER_ASSEMBLY_STEPS,
)


def _hr(char: str = "─", width: int = 60) -> str:
    return char * width


def _wrap(text: str, indent: int = 4) -> str:
    prefix = " " * indent
    return textwrap.fill(text, width=76, initial_indent=prefix, subsequent_indent=prefix)


def run_assembly_guide(
    arm: str = "follower",
    print_fn: Callable = print,
    input_fn: Callable = input,
    start_step: int = 0,
) -> None:
    """
    Interactive terminal assembly guide for one arm.

    Walks through each assembly step, shows screw list and tips,
    and waits for user confirmation before advancing.
    """
    steps = FOLLOWER_ASSEMBLY_STEPS  # leader has same structure, different gear labels

    print_fn("\n" + _hr("═"))
    print_fn(f"  🤖 SO-ARM101 Assembly Guide — {arm.upper()} arm")
    print_fn(f"  {len(steps)} steps  |  Keep your screwdriver handy")
    print_fn(_hr("═"))
    print_fn(
        _wrap(
            "This guide walks you through physically assembling the arm. "
            "After assembly, run 'castor arm setup' to configure the motors.",
            indent=2,
        )
    )
    print_fn("")

    for i, step in enumerate(steps):
        if i < start_step:
            continue

        print_fn(_hr())
        print_fn(f"  Step {step.step + 1}/{len(steps)}: {step.title}")
        print_fn(_hr())
        print_fn(_wrap(step.description))

        if step.screws:
            print_fn("\n  Screws needed:")
            for s in step.screws:
                print_fn(f"    • {s}")

        if step.tips:
            print_fn("\n  💡 Tips:")
            for t in step.tips:
                print_fn(_wrap(f"• {t}", indent=4))

        if step.motor_id is not None:
            print_fn(f"\n  ⚙  Motor ID for this joint: {step.motor_id}")
            print_fn("  📌 Reference: https://huggingface.co/docs/lerobot/so101")

        print_fn("")
        resp = (
            input_fn(
                f"  [Step {i + 1}/{len(steps)}] Done? Press Enter to continue, or 'q' to quit: "
            )
            .strip()
            .lower()
        )
        if resp == "q":
            print_fn("\n  Assembly paused. Run 'castor arm assemble' to resume.")
            return

    print_fn("\n" + _hr("═"))
    print_fn("  ✅ Assembly complete!")
    print_fn(_hr("═"))
    print_fn(
        _wrap(
            "Next step: configure the motor IDs. Run: castor arm setup --arm follower",
            indent=2,
        )
    )
    print_fn("")


def assembly_steps_json(arm: str = "follower") -> list[dict]:
    """Return assembly steps as dicts for web wizard JSON API."""
    steps = FOLLOWER_ASSEMBLY_STEPS
    return [
        {
            "step": s.step,
            "joint": s.joint,
            "title": s.title,
            "description": s.description,
            "screws": s.screws,
            "tips": s.tips,
            "motor_id": s.motor_id,
            "image_url": (
                f"https://huggingface.co/docs/lerobot/so101#joint-{s.step}"
                if s.motor_id is not None
                else None
            ),
        }
        for s in steps
    ]
