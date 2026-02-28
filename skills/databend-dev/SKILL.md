---
name: databend-dev
description: >
  Automates the Databend local development workflow: build, clean, deploy, and verify.
  Use this skill whenever the user wants to build and test Databend locally, deploy a
  standalone or cluster instance, restart the local environment, or run the full
  build-deploy-verify cycle. Trigger on phrases like "build and deploy", "restart databend",
  "redeploy", "start standalone", "start cluster", "rebuild and test", or any request
  to get a local Databend instance running.
---

# Databend Dev

Local build → clean → deploy → verify workflow for the Databend project.

## Workflow

Execute these steps in order. Stop and report if any step fails after retries.

### Step 1: Build

```bash
make build
```

- Timeout: 1200000ms (20 minutes). Debug builds are slow.
- If this is an incremental build after small changes, it will be much faster.
- Build artifacts land in `target/debug/databend-query` and `target/debug/databend-meta`.

### Step 2: Kill Old Processes

```bash
killall -15 databend-query && killall -15 databend-meta
```

### Step 3: Clean Old Data

```bash
rm -rf ./.databend
```

### Step 4: Deploy

Choose the deployment mode based on context. Default to standalone unless the user
asks for cluster mode or the test being run requires it.

**Standalone:**
```bash
./scripts/ci/deploy/databend-query-standalone.sh
```

**3-node cluster:**
```bash
./scripts/ci/deploy/databend-query-cluster-3-nodes.sh
```

#### Handling Startup Timeout

Debug builds start slowly. The deploy script's internal `wait_tcp.py --timeout 30`
often isn't enough. When the script exits with an error:

1. Check if the process is still running: `ps aux | grep databend-query | grep -v grep`
2. If the process IS running, wait manually with a longer timeout:
   ```bash
   python3 scripts/ci/wait_tcp.py --timeout 120 --port 8000
   ```
3. If the process is NOT running, that's a real failure — check logs:
   ```bash
   tail -100 .databend/logs_1/databend-query-*.0 | grep -iE "error|panic|fatal"
   ```

Retry the full deploy (Steps 2-4) up to 2 times on timeout failures.

### Step 5: Verify

```bash
echo "SELECT 1" | bendsql
```

Expected output: `1`. If this succeeds, the environment is ready.


## Troubleshooting

- Process alive but no port → debug build still initializing, wait longer
- Port 8000 already in use → forgot to `kd` first
- Meta connection refused → databend-meta didn't start, check `.databend/logs1/`
- bendsql not found → install with `cargo install bendsql`
