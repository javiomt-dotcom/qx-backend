# QX backend

Proyecto de trading cuantitativo de Javier. Responde siempre en español, en frases completas y sencillas. Nada de esto es asesoría financiera.

- Contexto completo del proyecto (piezas, modelos, workflows, secretos, cómo hacer cambios): skill `qx-trading-terminal` en `.claude/skills/qx-trading-terminal/SKILL.md`. Léela antes de cambiar algo.
- Pruebas antes de subir: `python tests/test_daily.py`.
- Nunca escribas llaves ni tokens en el código o el chat; llegan por secretos de GitHub (ver la skill).
- Toda idea nueva para el modelo se prueba primero con backtest walk-forward de 3 años contra el swing actual y SPY.

## Skills de terceros

`.claude/skills/trader-*` son el paquete Day Trader de Jake Schincariol (MIT, ver `.claude/skills/DAY-TRADER-LICENSE`), copiado de github.com/Jakeschincariol/day-trader-skill (commit 3d0f4ef). Son para day trading de un día y usan datos de Yahoo; sirven como consultas sueltas, no como parte del motor QX (en QX el plazo de 1 día perdió contra SPY). Escriben en `trader/`, que está fuera de git. `trader-broker` sólo manda órdenes con el "sí" explícito de Javier, en cuenta paper por default.
