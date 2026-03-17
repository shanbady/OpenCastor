# Workspace Bounds — SO-ARM101

## Reachable Zone
- Forward reach: 0.05m–0.55m from base
- Height range: table surface (0m) to approximately 0.35m above base
- Lateral range: ±45° from centre without joint limit contact

## Joint Limits (approximate)
- shoulder_pan: ±135°
- shoulder_lift: -90° to +45° (sensitive — move in ≤10° steps)
- elbow_flex: 0° to 135°
- wrist_flex: ±90°
- wrist_roll: ±180°
- gripper: 0 (closed) to 100 (fully open)

## Dead Zones
- Directly overhead: elbow cannot fully extend — avoid
- Below table level: not reachable without tilting base
- Within 0.05m of base: collision risk with the arm body itself

## Speed Guidelines
- Normal operation: 30% speed
- Near objects/humans: 10–15% speed
- Homing: 20% speed
- Never use 100% speed in an uncontrolled workspace
