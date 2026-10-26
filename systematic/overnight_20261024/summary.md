# Overnight testnet seasons, scored on the recorded books

Simulated fills against the real recorded order books; the bot's own code. `final` = default config (S3 from 120 s); `old` = S3 from 80 s. Engine storm library = seasons 251-600 (out of sample).

| Season | final | old (S3 80 s) | final - old | final by round | violations | exceptions |
|---|---|---|---|---|---|---|
| 242 | 12,777 | 16,591 | -3,814 | 6,827 / 3,930 / 2,020 | 0 | 0 |
| 243 | 15,628 | 16,455 | -827 | 10,881 / 370 / 4,376 | 0 | 0 |
| 244 | 19,582 | 18,868 | +713 | 12,823 / 5,249 / 1,510 | 0 | 0 |
| 245 | 614 | 216 | +398 | 3,511 / 7,912 / -10,810 | 0 | 0 |
| 246 | 3,517 | 12,962 | -9,444 | -2,454 / -522 / 6,494 | 0 | 0 |

**Total over 5 seasons:** final 52,118 (avg 10,424), old 65,092 (avg 13,018), difference -12,974.
