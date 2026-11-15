## Tests, type-checks and builds go through `heavy`

This machine is shared by many projects and agent sessions. Prefix every test run, type-check
and build with `heavy`, for example `heavy npx tsc --noEmit`.

- It waits for a free slot, then runs the command and exits with the command's own code. While it
  waits it prints `heavy: queued`. The wait can be many minutes: allow a long timeout or run it in
  the background. Being queued is never a reason to run the command without `heavy`.
- One heavy command per session at a time, scoped as tightly as the change allows.
- Exit 75 with "stopped by the pressure breaker" means the machine ran out of headroom. Check
  `heavy status` and rerun once the pressure is ok.
- Dev servers and anything long-lived stay outside `heavy`.
- If the user asks for quiet, run `heavy quiet 2h`. `heavy normal` undoes it.
