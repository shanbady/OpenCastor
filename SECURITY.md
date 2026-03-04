# Security Policy

## Supported Versions

| Version | Status | Supported Until |
|---------|--------|-----------------|
| v2026.3.x (current) | ✅ Active | Until v2026.6 released |
| v2026.2.x | 🔶 Security fixes only | 2026-09-01 |
| Earlier | ❌ End of life | — |

## Reporting a Vulnerability

**Do not file a public GitHub issue for security vulnerabilities.**

Report privately:
- **GitHub Security Advisories** (preferred): [github.com/craigm26/OpenCastor/security/advisories/new](https://github.com/craigm26/OpenCastor/security/advisories/new)
- **Email**: security@continuon.ai

Include: version affected, description, reproduction steps, impact assessment, and proposed fix if you have one.

## Response Timeline

| Stage | Commitment |
|-------|-----------|
| Acknowledgement | Within 48 hours |
| Triage | Within 7 days |
| Updates | Every 14 days |
| Critical/High patch | Within 30 days |
| Medium patch | Within 90 days |
| CVE coordination | On request |

## Scope

**In scope:**
- Prompt injection bypasses — natural language inputs that cause OpenCastor to execute unsafe motor commands
- Safety invariant bypasses — any path that allows remote commands to override on-device safety checks
- RCAN RBAC bypass — ways to issue commands beyond a principal's declared role
- Authentication weaknesses — token forgery, session fixation, credential exposure
- Audit log tampering — attacks against the commitment chain or HMAC integrity
- §16 AI accountability bypass — ways to issue AI commands without model identity being recorded
- HiTL gate bypass — paths that execute PENDING_AUTH commands without authorization
- Dependency vulnerabilities with direct exploitability (not just theoretical)

**Physical safety vulnerabilities are treated as Critical regardless of exploitability complexity.** Any finding that could cause a robot to move unsafely is immediately escalated.

**Out of scope:**
- Theoretical vulnerabilities without a realistic attack path
- Hardware-level attacks requiring physical access (out of software scope)
- Social engineering
- Denial of service that does not affect physical safety

## Dependency Vulnerabilities

OpenCastor uses Dependabot for automated dependency scanning. If you discover a vulnerability in a dependency before Dependabot flags it, please report it privately — we'll coordinate with the upstream maintainer.

## SBOM

A Software Bill of Materials (CycloneDX 1.6 JSON) is attached to each release. Verify the SBOM hash matches the release artifact before use in production environments.

## Physical Safety Disclosure Policy

Vulnerabilities that could cause physical harm (uncontrolled robot motion, safety system bypass) follow a **shorter embargo**: we commit to a patch within **14 days** and will coordinate disclosure with the reporter, relevant hardware vendors, and CISA if warranted.

## CVE Process

Critical and High findings: we request a CVE via GitHub's CVE numbering authority partnership and coordinate a 90-day maximum embargo with the reporter.

## Responsible Disclosure Hall of Fame

*(None yet — be the first.)*

---

Policy aligned with ISO/IEC 29147 (vulnerability disclosure), ISO/IEC 30111 (vulnerability handling), and CISA coordinated vulnerability disclosure guidelines.
