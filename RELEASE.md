---
status: published
repo: https://github.com/spinnakergit/a0-signal
index_pr: https://github.com/agent0ai/a0-plugins/pull/58
published_date: 2026-03-10
version: 1.1.0
---

# Release Status

## Publication
- **GitHub**: https://github.com/spinnakergit/a0-signal
- **Plugin Index PR**: [#58](https://github.com/agent0ai/a0-plugins/pull/58) (CI passed)
- **Published**: 2026-03-10

## v1.1.0 (2026-03-27)

### Changes
- Migrated config.html to Alpine.js framework pattern (outer Save button)
- Fixed elevated mode: scoped restricted-mode NEVER directives so they don't block elevated access
- Fixed chat bridge: contacts now reload each poll cycle (contacts added after bridge start were invisible)
- Added hooks.py for plugin lifecycle management
- Added thumbnail.png (256x256 indexed PNG, Signal blue)
- Improved install.sh to skip file copy when installed in-place via plugin manager

### Verification
- Chat bridge restricted mode: confirmed working on testing instance
- Chat bridge elevated mode: confirmed working (full agent access via `!auth`)
- Both modes end-to-end verified via live Signal messages

## v1.0.0 (2026-03-10)

### Verification
- **Automated Tests**: 75/75 PASS (133 assertions in regression suite)
- **Human Verification**: 36/36 PASS
  - Restricted mode conversational: 6/6
  - Auth flow: 6/6
  - Elevated mode (file ops, code exec, web ops): 9/9
  - Session management: 4/4
  - Security (injection + privilege separation): 6/6
  - Error handling + long response: 2/2
  - External mode verification: 3/3
- **Security Assessment**: Completed as part of verification

## Commit History
| Hash | Date | Description |
|------|------|-------------|
| `2e62cef` | 2026-03-27 | Fix: reload contacts each poll cycle instead of once at startup |
| `482cd68` | 2026-03-27 | Fix elevated mode: distinguish restricted vs elevated in system prompt |
| `89604aa` | 2026-03-27 | Migrate config.html to Alpine.js framework pattern |
| `d1865f5` | 2026-03-27 | Bump version to 1.1.0 |
| `32f6404` | 2026-03-27 | Add hooks.py, thumbnail, config UI hardening, install improvements |
| `2439376` | 2026-03-10 | Add RELEASE.md with publication status and verification summary |
| `0af3ef8` | 2026-03-10 | Initial commit: Signal integration plugin v1.0.0 |

## Notes
- Dual-mode architecture: integrated (signal-cli native binary, JSON-RPC daemon) and external (bbernhard/signal-cli-rest-api).
- Factory pattern abstracts mode differences — tools work identically in both modes.
- First plugin to use the formal HUMAN_VERIFICATION_FRAMEWORK.
