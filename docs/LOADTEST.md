# Load test on AWS (report sections 5.2, 5.3 and 9)

Measured on Nov 19, 2026 on the EC2 instance of the demo (m7i-flex.large: 2 vCPU, 8 GB; us-east-2),
with the whole platform running (C1, C2, C3, C4, the simulated cars, MySQL, MongoDB, Mosquitto).
The load generator, `tools/loadtest.py`, ran in a container on the same host and reached the
platform through nginx (`http://web:8080`), so the numbers measure the servers, not the internet:

```bash
docker run --rm --network scicp_default -v ~/scicp:/repo:ro -v ~/loadtest:/out scicp/c1-api:local \
    python /repo/tools/loadtest.py --base http://web:8080 --duration 30 --concurrency 1,8,32,64 --json /out/run.json
```

Each virtual user signs in as the fleet manager of Bay Shuttle LLC and sends requests back to back,
cycling through five read endpoints: C1's overview, map and billing invoice, C2's vehicle list and
C3's open alerts. 30 seconds per level.

## One replica against two replicas of the API, C2 and C3

| Concurrent users | 1 replica: req/s | p95 (ms) | errors | 2 replicas: req/s | p95 (ms) | errors |
|---:|---:|---:|---:|---:|---:|---:|
| 1  | 110.4 | 16.1    | 0  | 104.9 | 18.1   | 0 |
| 8  | 149.1 | 120.3   | 0  | 130.7 | 144.1  | 0 |
| 32 | 11.1  | 1,125.7 | 17 | 104.8 | 924.8  | 0 |
| 64 | 3.2   | 29,974.4| 58 | 94.8  | 1,977.8| 0 |

- Up to 8 users both set-ups are close: the instance has 2 vCPUs, which also run the databases, the
  C4 worker and the load generator, so the second replica adds little raw CPU.
- At 32 and 64 users one replica fails: the API logged "QueuePool limit of size 5 overflow 10
  reached" 126 times. One API process holds at most 15 database connections; the other requests
  waited 30 s and timed out (75 errors, throughput 11 and 3 req/s). Two replicas have twice the
  connections and served every request (13,122 requests, 0 errors), at about 100 req/s.
- The requests were spread evenly: at 32 users, 950 and 950 requests on the two API replicas
  (the overview, map and billing calls), 315 and 316 on C2, 317 and 317 on C3.

## Failover

8 users for 40 s; after 12 s one replica each of the API, C2 and C3 was stopped (`docker stop`).
All 5,625 requests succeeded (140.5 req/s, p95 129.4 ms, 0 errors). nginx retried the 3 requests that
reached a stopped replica on the other one (they show both addresses in `X-Upstream`) and then
skipped the stopped replicas. A first run of this test had one failure, which was not caused by the
stopped replica: two requests inserted the default price list at the same moment and the losing one
read an expired object. That billing bug was fixed (the tenant is now loaded after the price list),
the price list was emptied again to recreate the race, and the run above had no error.

## Next steps

On one instance, the replicas give capacity under load (the database connections) and
availability; more CPU needs more instances: the same images behind an AWS Application Load
Balancer with an Auto Scaling group, MySQL on Amazon RDS and MongoDB on Atlas or DocumentDB, and
the MQTT broker as a managed service (AWS IoT Core).
