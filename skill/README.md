# AirBridge-related Agent Skills

| Skill | Path | Purpose |
|-------|------|---------|
| **airbridge** | `skill/airbridge/` | General remote control of host B — RFS, Shell, EDA dispatch |
| **orcad-pdf-export** | `skill/orcad-pdf-export/` | OrCAD schematic → PDF via Capture Tcl + Ghostscript (no manual GUI operation; Capture GUI session is managed by AirBridge) |

Copy a skill folder into your agent skills directory (e.g. `~/.grok/skills/<name>`).

Both assume AirBridge is running on host A at `http://127.0.0.1:8888` with serial link to host B.
