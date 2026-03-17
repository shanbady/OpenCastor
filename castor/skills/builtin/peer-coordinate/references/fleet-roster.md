# Fleet Roster

Current robots in the fleet. Edit this file when adding new robots.

## Active Members

| Name | RRN | Location | Capabilities | Notes |
|------|-----|----------|--------------|-------|
| Bob  | RRN-000000000001 | robot.local (Raspberry Pi 4) | vision, chat, drive | Primary home robot; OAK-D camera |
| Alex | RRN-000000000005 | alex.local (Raspberry Pi 5) | vision, control, arm, gripper | SO-ARM101 leader arm; Docker runtime |

## Capability Summary

**Bob** — Good at: observation, conversation, driving, web lookup. No arm.
**Alex** — Good at: arm manipulation, precision tasks. Has physical control capability.

## Coordination Patterns

**Observe + Act** — Bob describes scene → Alex manipulates
  Example: "Bob, what's on the table? ... Alex, pick up the red brick Bob described."

**Parallel tasks** — Both robots work independently on separate objects
  Example: Bob navigates to mark a boundary while Alex picks up objects in her zone.

**Handoff** — Bob navigates to drop-off zone, Alex picks up from there
  Requires prior agreement on a shared coordinate (e.g., "centre of table").
