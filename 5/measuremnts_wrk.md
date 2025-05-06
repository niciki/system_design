| Сценарий                     | Метрика            | Без кэширования (исходные) | С кэшированием (Redis) |
|------------------------------|--------------------|---------------------------|-----------------------|
| **1 Thread, 10 Connections** | **Latency Avg**    | 242.00ms                  | 96.80ms              |
|                              | **Latency Stdev**  | 57.76ms                   | 40.43ms             |
|                              | **Latency Max**    | 454.74ms                  | 181.90ms            |
|                              | **Requests/sec**   | 40.60                     | 52.78               |
|                              | **Transfer/sec**   | 26.13KB                   | 33.98KB             |
|                              | **Total Requests** | 410                       | 533                 |
| **5 Threads, 10 Connections**| **Latency Avg**    | 256.02ms                  | 102.41ms            |
|                              | **Latency Stdev**  | 62.88ms                   | 44.02ms             |
|                              | **Latency Max**    | 572.47ms                  | 228.99ms            |
|                              | **Requests/sec**   | 38.31                     | 49.80               |
|                              | **Transfer/sec**   | 24.62KB                   | 32.00KB             |
|                              | **Total Requests** | 387                       | 503                 |
| **10 Threads, 10 Connections**| **Latency Avg**   | 258.41ms                  | 97.10ms             |
|                              | **Latency Stdev**  | 87.90ms                   | 37.67ms             |
|                              | **Latency Max**    | 620.74ms                  | 195.16ms            |
|                              | **Requests/sec**   | 37.64                     | 52.44               |
|                              | **Transfer/sec**   | 25.92KB                   | 33.69KB             |
|                              | **Total Requests** | 380                       | 529                 |