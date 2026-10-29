| Command | native | cold | prewarm+0 | prewarm+100 | prewarm+300 | warm |
|---|---|---|---|---|---|---|
| `aws --version` | 230 ms (15/15) | 273 ms (0/15) | 270 ms (0/15) | 165 ms (15/15) | 37 ms (15/15) | 36 ms (15/15) |
| `aws configure list` | 239 ms (15/15) | 269 ms (0/15) | 268 ms (0/15) | 168 ms (15/15) | 44 ms (15/15) | 45 ms (15/15) |
| `aws ec2 run-instances --generate-cli-skeleton` | 328 ms (15/15) | 343 ms (0/15) | 355 ms (0/15) | 209 ms (15/15) | 69 ms (15/15) | 68 ms (15/15) |

Daemon start-up to first spare: [249.4, 255.2, 227.4, 234.3, 254.9] ms
