# Alethe verify budget

Locked for the verify-alethe delivery gate (2026-11-03 Asia/Shanghai).

| Item | Value |
|---|---|
| Baseline tip | `907269ad2f9e94ccd12a9c26c70379377adcb09f` (`907269a`) |
| How measured | clean `npm ci` + `npm run build`, then `du -sb dist` |
| Baseline `dist` size | **19,425,375** bytes |
| Fail threshold | baseline + 10% → fail if **> 21,367,912** bytes |
| lint | **0 errors** (warnings allowed; not a size rule) |

Any Alethe delivery verification must post real-run evidence against these numbers. Updating the baseline requires a fresh clean build on an agreed tip and an explicit 监工 update of this file.