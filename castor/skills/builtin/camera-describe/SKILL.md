---
name: camera-describe
description: >
  Use when the user asks what the robot sees, requests a photo or snapshot,
  wants a description of the surroundings, asks about objects in view, or
  says "look at X", "what's in front of you", "describe your environment",
  "take a picture", "scan the area", "can you see X", "show me what you see".
version: "1.1"
requires:
  - vision
consent: none
tools:
  - get_camera_frame
  - get_distance
max_iterations: 2
---

# Camera Describe Skill

Capture and describe what the robot's camera sees.

## Steps

1. Call `get_camera_frame()` to capture a JPEG snapshot
2. If depth sensor available, call `get_distance()` to get nearest obstacle distance
3. Describe the scene naturally: objects, colours, layout, notable features
4. Include distance context: "The nearest object is approximately X metres away"
5. Note any obstacles within 1 metre as a safety observation

## If camera unavailable

Return: "My camera is not currently available. I can describe my sensor readings instead if helpful."

## Guidelines

- Be specific about what you see — avoid vague descriptions like "some objects"
- Mention spatial relationships: "to the left", "in the centre", "in the background"
- If asked about a specific object, focus your description on finding it
- Do not fabricate visual content if the frame is blank or unavailable
- For object identification questions ("is there a cup?"), scan carefully before answering

## References

See `references/scene-description-guide.md` for spatial vocabulary and lighting callouts.

## Gotchas

- **Blank/black frame ≠ nothing there** — camera may need a moment to warm up; retry once before reporting unavailable
- **Depth sensor returns 0.0** — means out-of-range (> sensor max), not "right in front of you"; treat 0.0 as "no valid reading"
- **OAK-D proxy latency** — first frame after startup can take 2–3s; if `get_camera_frame()` is slow, still wait for it rather than aborting
- **Confusing left/right** — camera's left is the user's right if facing the robot; always describe from the camera's perspective, not the user's
- **Low light** — if the description would be "dark room, can't see much", also report any distance readings as supplementary info

## Example

User: "What do you see?"
→ `get_camera_frame()` → `get_distance()`
→ "I can see a wooden table in the foreground with several Lego bricks scattered on it. The nearest object is approximately 0.4 metres away. In the background there's a white wall."
