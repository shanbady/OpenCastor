# Named Locations

This file lists pre-mapped location names the robot recognises.
Edit this file to add your own room/area labels.

## Default Locations

These are descriptive labels only — the robot will navigate by visual/sensor feedback,
not by stored coordinates. Use them to communicate intent clearly.

| Name | Description |
|------|-------------|
| "home" / "base" | Robot's charging position / starting point |
| "table" / "work table" | Primary work surface in the workspace |
| "user" / "me" / "here" | Navigate toward the user (requires visual locate) |
| "door" | Nearest door in the current room |
| "centre" / "middle" | Open centre of the current room |

## Adding Custom Locations

To add a named location:
1. Navigate the robot to that spot manually
2. Call `get_telemetry()` to capture current position
3. Add an entry to this file with the telemetry coordinates

Example:
```
| "charging dock" | {x: 0.0, y: 0.0, heading: 0} — home base |
| "lego table"    | {x: 1.2, y: 0.4, heading: 90} — table in workshop |
```

When the user refers to a named location, consult this file to orient the navigation plan.
