---
status: published
repo: https://github.com/spinnakergit/a0-signal
index_pr: https://github.com/agent0ai/a0-plugins/pull/58
published_date: 2026-03-10
version: 1.0.0
---

# Release Status

## Publication
- **GitHub**: https://github.com/spinnakergit/a0-signal
- **Plugin Index PR**: [#58](https://github.com/agent0ai/a0-plugins/pull/58) (CI passed)
- **Published**: 2026-03-10

## Verification Completed
- **Automated Tests**: 75/75 PASS (133 assertions in regression suite)
- **Human Verification**: 36/36 PASS (2026-03-10)
  - Restricted mode conversational: 6/6
  - Auth flow: 6/6
  - Elevated mode (file ops, code exec, web ops): 9/9
  - Session management: 4/4
  - Security (injection + privilege separation): 6/6
  - Error handling + long response: 2/2
  - External mode verification: 3/3
- **Security Assessment**: Completed as part of verification (no separate formal report)

## Commit History
| Hash | Date | Description |
|------|------|-------------|
| `0af3ef8` | 2026-03-10 | Initial commit: Signal integration plugin v1.0.0 |

## Notes
- Dual-mode architecture: integrated (signal-cli native binary, JSON-RPC daemon) and external (bbernhard/signal-cli-rest-api).
- Factory pattern abstracts mode differences — tools work identically in both modes.
- First plugin to use the formal HUMAN_VERIFICATION_FRAMEWORK.
