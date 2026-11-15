# heavy

A queue for the expensive commands on one Mac. Prefix a test run, a type-check or a build with `heavy` and it waits its turn for one of a fixed number of slots, so the total stays bounded however many terminals and coding agents are busy at once. A schedule changes the number of slots by day and hour, and a watchdog stops the newest jobs if memory or load still gets out of hand.

It exists because several agent sessions, each sensibly running its own tests, will together swap a laptop to a standstill.

```
$ heavy npx tsc --noEmit
heavy: queued: 5 of 5 slots busy (normal), 2 ahead in the queue
heavy: started after 3m12s in the queue
```

## Install

macOS only. It needs `/usr/bin/python3`, which comes with the Xcode Command Line Tools (`xcode-select --install`), and nothing else.

```
git clone https://github.com/bigblueboo/heavy.git
cd heavy
./install.sh
```

`install.sh` does three things, none of which needs sudo:

- links `bin/heavy` into `~/.local/bin` (set `HEAVY_BIN_DIR` to put it elsewhere)
- writes a starter config to `~/.config/heavy/config.json`, if there isn't one
- starts the watchdog as a launchd agent, `com.github.bigblueboo.heavy`, logging to `~/Library/Logs/heavy.log`

Keep the clone where it is: the link and the agent point into it. To update, `git pull` and run `./install.sh` again. To remove, `./install.sh --uninstall`.

## Use

```
heavy npx vitest run tests/unit/parser.test.ts
heavy npx playwright test e2e/login.spec.ts
heavy npm run build
heavy cargo test
```

The command runs at low priority once a slot is free, with its own output and exit code. Commands start in the order they asked.

| Command | What it does |
| --- | --- |
| `heavy status` | Slots in use, what is running, what is queued, the pressure reading |
| `heavy config` | Where the config file is and every setting in effect |
| `heavy quiet [for]` | Drop to the quiet slot count for a while (default 3 hours) |
| `heavy cap <n> [for]` | Set the slot count by hand for a while |
| `heavy normal` | End a `quiet` or `cap` and go back to the schedule |

`[for]` is a length such as `90m` or `2h30m`, or a clock time to stop at, such as `19:00`. Lowering the count never interrupts a command that is already running; new ones wait.

Things to know:

- A wait can be long. In a script or an agent tool call, allow a long timeout or run the command in the background.
- Don't put dev servers or anything long-lived behind `heavy`. A slot is held until the command exits.
- A script that calls `heavy` from inside a `heavy` command takes one slot in total.
- Exit code 75 with "stopped by the pressure breaker" means the watchdog shed the job. Rerun it once `heavy status` shows the pressure is ok.
- `heavy -- status` runs a program that happens to be called `status`.

## Change the schedule

Everything is in `~/.config/heavy/config.json`. Edit it and save: the change applies to the next command and to commands already waiting, with no restart. `heavy config` prints what is in effect, and warns about any setting it could not use.

```json
{
  "slots": 5,
  "schedule": [
    { "days": ["mon", "tue", "wed", "thu", "fri"], "from": "09:00", "to": "18:00", "slots": 3 },
    { "days": ["sat", "sun"], "from": "14:00", "to": "17:00", "slots": 1 },
    { "from": "23:00", "to": "06:00", "slots": 8 }
  ]
}
```

That reads: five slots normally; three during weekday working hours; one on weekend afternoons; eight overnight, every night.

- `slots` is the count whenever no schedule window applies. The default is half the CPU cores.
- Each `schedule` window has `from` and `to` as 24-hour `HH:MM`, and the `slots` to allow between them.
- `days` is a list from `mon tue wed thu fri sat sun`. Leave it out for every day.
- A window whose `to` is earlier than its `from` runs past midnight and belongs to the day it starts on.
- Where windows overlap, the one with the fewest slots wins.
- `"slots": 0` in a window holds everything until the window ends.

For a one-off, skip the file: `heavy quiet 2h` or `heavy cap 1 19:00`. That beats the schedule until it expires or you run `heavy normal`.

[config.example.json](config.example.json) has every setting. The rest:

| Setting | Default | Meaning |
| --- | --- | --- |
| `quietSlots` | 2 | The count `heavy quiet` drops to, and a window's count if it has no `slots` |
| `tightSlots` | 1 | The count while the breaker reports the machine is tight |
| `nice` | 10 | Priority given to commands, 0 (normal) to 20 (lowest) |
| `breaker` | | See below |

A mistake in the file never blocks work. A setting that doesn't check out is reported on stderr and skipped, and the others still apply.

## The breaker

A queue only limits what goes through it. The watchdog covers the rest: every 20 seconds it reads swap in use, the one-minute load average and the kernel's memory pressure level, and sets one of three states.

| State | When | Effect |
| --- | --- | --- |
| ok | Below both thresholds | The schedule decides |
| tight | Swap ≥ `tightSwapGb` or load ≥ `tightLoad` | At most `tightSlots` commands run |
| critical | Swap ≥ `criticalSwapGb`, load ≥ `criticalLoad`, or the kernel reports critical memory pressure | Nothing new starts. After two critical readings in a row, the newest heavy job is stopped, then one more on each reading until the state improves |

The defaults scale with the machine: tight at swap equal to 60% of RAM or a load of 8 per core, critical at 85% of RAM or 15 per core. Set absolute numbers under `breaker` to tune them, and check the result with `heavy config`.

A stopped job gets SIGTERM, then SIGKILL five seconds later, along with every process it started. Its `heavy` wrapper exits 75 and says why. Each stop is logged to `~/Library/Logs/heavy.log`.

With `"killUngated": true`, the default, the breaker also stops test runs and builds that were started without `heavy`. It recognises them by command line, using the regular expressions in `breaker.ungated`. The built-in list covers Playwright, vitest, jest, `tsc`, `next build` and `vite build` run from a project's `node_modules/.bin`. Each pattern is matched from the start of the command line as `ps` prints it, which keeps dev servers and unrelated processes out. To cover other tools, set `breaker.ungated` to your own list; it replaces the built-in one. Set `killUngated` to `false` and the breaker only ever stops commands that went through `heavy`.

To see what the breaker would stop right now, without stopping anything:

```
heavy watchdog --once --dry-run
```

It only acts on your own processes, and only on jobs that are safe to rerun. If the machine is critical and no heavy job is left, the log says so, and the cause is something else.

## Tell your agents

Coding agents won't use the queue unless their instructions say to. Put something like this where every session reads it: `~/.claude/CLAUDE.md` for Claude Code, `~/.codex/AGENTS.md` for Codex.

```markdown
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
```

## How it works

A running command holds a file lock on `/tmp/heavy-<uid>/run/<pid>`, and a waiting one holds a lock on a timestamped ticket in `queue/`. The kernel releases a lock when its holder dies, so a crashed or killed command can't leave a slot taken, and nothing needs cleaning up after a reboot. Waiters check once a second, under one short mutex, whether they are near enough to the front of the queue for the slots that are free.

The wrapper stays alive as the parent of your command so it can pass on SIGTERM, SIGINT and SIGHUP and report a breaker stop. If the wrapper itself is killed with SIGKILL, its slot is freed and the command carries on outside the queue.

It is one Python file with no dependencies, written for the system Python so that a project switching Node or Python versions can't break it.

## Quality gates

```
/usr/bin/python3 -m unittest discover -s tests -v
```

The tests run real `heavy` processes against a temporary state and config directory, with `killUngated` off, so they never touch anything else on the machine.
