# AirBridge-related Agent Skills

| Skill | Path | Purpose |
|-------|------|---------|
| **airbridge** | `skill/airbridge/` | General serial transport to host B — raw data, file transfer, RFS, optional Shell |
| **orcad-pdf-export** | `skill/orcad-pdf-export/` | OrCAD schematic → PDF via Tcl + Ghostscript (no GUI) |

Copy a skill folder into your agent skills directory (e.g. `~/.grok/skills/<name>`).

Both assume AirBridge is running on host A at `http://127.0.0.1:8888` with serial link to host B.
