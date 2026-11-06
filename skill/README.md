# AirBridge Agent Skill

Grok / agent skill for controlling air-gapped host B via AirBridge on host A (`http://127.0.0.1:8888`).

## Layout

```
skill/airbridge/
├── SKILL.md
├── references/api-reference.md
└── scripts/
    ├── diag_temp.py
    └── shell_exec.py
```

Copy `skill/airbridge` into your agent skills directory (e.g. `~/.grok/skills/airbridge`) or load `SKILL.md` as instructed by your agent runtime.

Requires AirBridge running on host A and host B with serial link up.
