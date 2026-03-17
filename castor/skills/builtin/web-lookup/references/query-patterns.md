# Query Shaping Patterns

## Principle: Keywords over sentences

❌ "what is the latest version of the feetech sts3215 servo motor"
✅ "Feetech STS3215 servo specifications"
✅ "STS3215 latest firmware"

## Append modifiers for freshness
- Add year when recency matters: "robot manipulation research 2026"
- Add "docs" or "official" for authoritative: "LeRobot official docs getting started"
- Add "github" for code: "opencastor castor bridge github"

## Query refinement pattern

If first query returns irrelevant results:
1. Add more specificity: "STS3215" → "Feetech STS3215 TTL protocol"
2. Try alternate terminology: "servo motor" → "smart servo" / "bus servo"
3. Try the manufacturer directly: "site:feetechrc.com STS3215"

## Robot ecosystem queries

| Topic | Good query pattern |
|-------|--------------------|
| LeRobot | "lerobot huggingface [topic] github" |
| OAK-D | "luxonis oak-d [topic] docs" |
| RCAN | "rcan-py [topic] github" / "rcan.dev [topic]" |
| ROS2 | "ros2 [package] documentation" |
| Feetech | "feetech [model] [topic]" |
| SO-ARM101 | "so-arm101 [topic] github" |

## When to stop

After 2 attempts with no useful result:
> "I searched for [X] but couldn't find a reliable answer. You may want to check the official docs at [likely URL] directly."

Don't keep retrying with minor variations — it burns context tokens and rarely helps.
