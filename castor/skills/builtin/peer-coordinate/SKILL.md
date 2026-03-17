---
name: peer-coordinate
description: >
  Use when the user asks to consult, communicate with, or delegate to another
  robot in the fleet. Triggers on "ask Alex", "ask Bob", "check with [robot]",
  "what does [robot name] think", "coordinate with", "tell [robot] to",
  "split the task with", "collaborate with", "get Bob's opinion", "loop in Alex",
  "let Alex handle", "what can Bob do", "relay this to".
version: "1.1"
requires: []
consent: none
tools:
  - send_rcan_message
max_iterations: 3
---

# Peer Coordinate Skill

Communicate with peer robots via RCAN to consult, delegate, or collaborate.

## Steps

### 1. Identify the target robot
Map the robot name mentioned by the user to an RRN:
- "Bob" → RRN-000000000001
- "Alex" → RRN-000000000005
- Unknown name: ask user to clarify or check with `get_telemetry()` for fleet list

### 2. Compose the message
Frame a clear, concise message to the peer robot. Include:
- What you need (question, task delegation, status check)
- Relevant context (what the user asked, what you've already done)

### 3. Send via RCAN
Call `send_rcan_message(rrn="RRN-XXXXXXXXXXXX", message="...")`.
Timeout: 10 seconds. If no response: report timeout and offer alternatives.

### 4. Synthesise response
Combine the peer's response with your own context and present a unified answer
to the user. Attribute clearly: "Alex says: ..."

## Protocol 66 note
This skill only sends `scope: chat` messages. Physical actions on the peer
robot are governed by that robot's own P66 layer — you cannot override them.
Do NOT attempt to command a peer robot to perform physical actions through chat.

## References

See `references/fleet-roster.md` for current fleet members and their capabilities.

## Gotchas

- **Peer offline** — `send_rcan_message` timeout doesn't mean the robot is broken; it may be busy, low-battery, or in a non-chat mode; always report "I couldn't reach Alex — she may be occupied" rather than "Alex is offline"
- **Scope = chat only** — you can ASK a peer to do something, but the peer's own P66 layer controls whether it acts; never phrase a peer message as a command you expect to be auto-executed ("do X now") — ask instead ("would you be able to X?")
- **RRN format** — must use full `RRN-000000000001` format; short names ("Bob") are NOT valid RRN values; always resolve before calling `send_rcan_message`
- **Message length** — keep peer messages concise (< 300 chars); long context dumps are expensive and the peer's model may truncate them; summarise what you need
- **Cascading delegation** — if you ask Alex to ask Bob, this creates a chain that can timeout; for three-robot coordination, the user should coordinate directly rather than daisy-chaining messages
- **Gemini ADC on Alex** — Alex's brain uses Google OAuth; she may occasionally have slower cold-start responses on the first message of a session

## Example

User: "Ask Alex what she sees"
→ `send_rcan_message("RRN-000000000005", "What do you currently see with your camera?")`
→ Alex responds: "I see a workbench with tools and a Lego set."
→ "Alex says she can see a workbench with tools and a Lego set."

User: "Coordinate with Bob to sort the bricks — you handle red, Bob handles blue"
→ `send_rcan_message("RRN-000000000001", "Sorting task: please pick up all blue Lego bricks. I will handle the red ones.")`
→ Bob: "Understood, starting blue brick collection."
→ Report plan to user, then proceed with red brick task using arm-manipulate skill
