# heavy

A queue for the expensive commands on one Mac. Put `heavy` in front of a test run, a type-check or a build and it waits for one of a fixed number of slots before it starts. However many terminals and coding agents are busy, only that many heavy jobs run at once. A schedule changes the count by day and hour, and a watchdog stops the newest jobs if memory or load gets out of hand anyway.

```
$ heavy npx tsc --noEmit
heavy: queued: 5 of 5 slots busy (normal), 2 ahead in the queue
heavy: started after 3m12s in the queue
```

## Why I wrote it

I do most of my development by running ten or more agent threads at once, across a dozen projects, on a 16 GB fanless MacBook Air that runs headless and that I reach over the network. Each agent is well behaved by its own lights. It finishes a change and runs the tests, which is what I asked it to do. The trouble is that a dozen of them make that sensible choice in the same ten minutes, and nobody is counting.

The last time, three projects had Playwright suites going at once, one of them repeating every test ten times across two browser engines. Load average climbed past 250 on a 10-core machine, swap reached 17 GB of 18, and macOS spent its CPU on the security checks it runs for every new browser process. Eventually the networking stack starved, which on a machine I can only reach over the network is about as bad as it catching fire. The only way back was a reboot.

It wasn't the first time, and the pattern was always the same: a slow ratchet, where swap and load creep up over a day of work until one more test run tips it over. I'd already written a reaper for idle sessions, but it deliberately leaves busy processes alone, and busy processes were the problem. What was missing was a limit on the total. Each project had its own lock, and none of them knew about the others.

So: one queue for the whole machine, a schedule so I can turn it down when the household needs the machine's other services to stay snappy, and a watchdog for when something gets around the queue.

## Install

macOS only. It needs `/usr/bin/python3`, which comes with the Xcode Command Line Tools (`xcode-select --install`), and nothing else.

```
git clone https://github.com/bigblueboo/heavy.git
cd heavy
./install.sh
```

`install.sh` doesn't need sudo. It links `bin/heavy` into `~/.local/bin` (set `HEAVY_BIN_DIR` to put it somewhere else), writes a starter config to `~/.config/heavy/config.json` if you don't have one, and starts the watchdog as a launchd agent called `com.github.bigblueboo.heavy`, which logs to `~/Library/Logs/heavy.log`.

Leave the clone where it is, since the link and the agent point into it. To update, `git pull` and run `./install.sh` again. To remove it, `./install.sh --uninstall`.

## Use

```
heavy npx vitest run tests/unit/parser.test.ts
heavy npx playwright test e2e/login.spec.ts
heavy npm run build
heavy cargo test
```

Once a slot is free the command runs at low priority, with its own output and its own exit code. Commands start in the order they asked.

| Command | What it does |
| --- | --- |
| `heavy status` | Slots in use, what is running, what is queued, the pressure reading |
| `heavy config` | Where the config file is and every setting in effect |
| `heavy quiet [for]` | Drop to the quiet slot count for a while (default 3 hours) |
| `heavy cap <n> [for]` | Set the slot count by hand for a while |
| `heavy normal` | End a `quiet` or `cap` and go back to the schedule |

`[for]` is a length such as `90m` or `2h30m`, or a clock time to stop at, such as `19:00`. Lowering the count never interrupts a command that's already running. New ones wait.

A few things worth knowing:

- Waits can be long. In a script or an agent's tool call, allow a generous timeout or run the command in the background.
- Keep dev servers and anything long-lived out of `heavy`, because a slot is held until the command exits.
- A script that calls `heavy` from inside a `heavy` command takes one slot in total, so wrapping an npm script that already uses it is harmless.
- Exit code 75 with "stopped by the pressure breaker" means the watchdog shed the job. Rerun it once `heavy status` says the pressure is ok.
- `heavy -- status` runs a program that happens to be called `status`.

## Change the schedule

Everything lives in `~/.config/heavy/config.json`. Edit it and save. The change applies to the next command and to anything already waiting, with no restart. `heavy config` prints what's in effect and complains about any setting it couldn't use.

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

Read that as five slots normally, three during weekday working hours, one on weekend afternoons, and eight overnight, every night, when nobody's around to notice the fans (or, on a fanless machine, the warm aluminium).

The rules:

- `slots` is the count whenever no schedule window applies. It defaults to half the CPU cores.
- Each window in `schedule` has `from` and `to` as 24-hour `HH:MM` times and the `slots` to allow between them.
- `days` is a list drawn from `mon tue wed thu fri sat sun`. Leave it out and the window applies every day.
- A window whose `to` comes before its `from` runs past midnight and belongs to the day it starts on.
- Where windows overlap, the one with the fewest slots wins.
- `"slots": 0` holds everything until the window ends.

For a one-off, skip the file and run `heavy quiet 2h` or `heavy cap 1 19:00`. That beats the schedule until it expires or you run `heavy normal`.

[config.example.json](config.example.json) has every setting. The ones not covered above:

| Setting | Default | Meaning |
| --- | --- | --- |
| `quietSlots` | 2 | The count `heavy quiet` drops to, and a window's count if it doesn't give one |
| `tightSlots` | 1 | The count while the breaker says the machine is tight |
| `nice` | 10 | Priority for commands, from 0 (normal) to 20 (lowest) |
| `breaker` | | See below |

A mistake in the file never blocks work. A setting that doesn't check out is reported on stderr and skipped, and the rest still apply. I wanted that badly: a typo in a config that every command on the machine reads would otherwise be its own kind of outage.

## The breaker

A queue only limits what goes through it, and agents forget instructions. The watchdog covers the rest. Every 20 seconds it reads swap in use, the one-minute load average and the kernel's memory pressure level, and settles on one of three states.

| State | When | Effect |
| --- | --- | --- |
| ok | Below both thresholds | The schedule decides |
| tight | Swap ≥ `tightSwapGb` or load ≥ `tightLoad` | At most `tightSlots` commands run |
| critical | Swap ≥ `criticalSwapGb`, load ≥ `criticalLoad`, or the kernel reports critical memory pressure | Nothing new starts. After two critical readings in a row the newest heavy job is stopped, then one more per reading until things improve |

The default thresholds scale with the machine. Tight is swap at 60% of RAM or a load of 8 per core, and critical is 85% of RAM or 15 per core. Set absolute numbers under `breaker` to tune them and check the result with `heavy config`.

A stopped job gets SIGTERM, then SIGKILL five seconds later, along with everything it started. Its `heavy` wrapper exits 75 and says why, and the stop goes in `~/Library/Logs/heavy.log`. The newest job goes first because it has the least work to lose.

With `"killUngated": true`, the default, the breaker also stops test runs and builds that were started without `heavy`. It spots them by command line, using the regular expressions in `breaker.ungated`. The built-in list covers Playwright, vitest, jest, `tsc`, `next build` and `vite build` run from a project's `node_modules/.bin`. Each pattern matches from the start of the command line as `ps` prints it, so dev servers and processes that merely mention a test runner in their arguments are left alone. To cover other tools, set `breaker.ungated` to your own list, which replaces the built-in one. With `killUngated` set to `false`, the breaker only ever stops commands that went through `heavy`.

To see what the breaker would stop right now, without stopping anything:

```
heavy watchdog --once --dry-run
```

It only touches your own processes, and only jobs that are safe to rerun. If the machine is critical and there's nothing heavy left to stop, the log says so, and you'll have to look elsewhere. (In my case, once, a browser extension that had been pinning a core for five hours.)

## Tell your agents

Agents won't use the queue unless their instructions say to. Put something like this where every session reads it: `~/.claude/CLAUDE.md` for Claude Code, `~/.codex/AGENTS.md` for Codex.

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

The line about being queued is there because an agent that sees a wait will otherwise reason its way around it.

## How it works

A running command holds a file lock on `/tmp/heavy-<uid>/run/<pid>`, and a waiting one holds a lock on a timestamped ticket in `queue/`. The kernel releases a lock when the process holding it dies, so a crashed or killed command can't leave a slot taken, and there's nothing to clean up after a reboot. Once a second, under one short mutex, each waiter checks whether it's close enough to the front of the queue for the slots that are free.

The wrapper stays alive as the parent of your command so it can pass on SIGTERM, SIGINT and SIGHUP and report a breaker stop. If the wrapper itself gets SIGKILL, its slot is freed and the command carries on outside the queue.

It's one Python file with no dependencies, written for the system Python so that a project switching Node or Python versions can't break it.

## Quality gates

```
/usr/bin/python3 -m unittest discover -s tests -v
```

The tests run real `heavy` processes against a temporary state and config directory, with `killUngated` off, so they never touch anything else on the machine.
