"""Tests for bin/heavy. Run with the system Python the tool itself uses:

    /usr/bin/python3 -m unittest discover -s tests -v

Every test points heavy at a temporary state and config directory, and every
config here turns `killUngated` off, so nothing outside the test is touched.
"""
import importlib.machinery
import importlib.util
import json
import os
import signal
import subprocess
import tempfile
import time
import unittest

HEAVY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "heavy")


def at(year, month, day, hour, minute):
    return time.mktime((year, month, day, hour, minute, 0, 0, 0, -1))


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = os.path.join(self.tmp.name, "state")
        self.conf = os.path.join(self.tmp.name, "conf")
        os.makedirs(self.conf)
        self.env = dict(os.environ, HEAVY_STATE_DIR=self.state, HEAVY_CONFIG_DIR=self.conf)
        self.env.pop("HEAVY_HELD", None)
        self.write_config({})

    def write_config(self, settings):
        settings.setdefault("breaker", {})["killUngated"] = False
        with open(os.path.join(self.conf, "config.json"), "w") as f:
            json.dump(settings, f)

    def module(self):
        """bin/heavy imported against this sandbox."""
        saved = dict(os.environ)
        os.environ.update(HEAVY_STATE_DIR=self.state, HEAVY_CONFIG_DIR=self.conf)
        try:
            loader = importlib.machinery.SourceFileLoader("heavy_under_test", HEAVY)
            module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
            loader.exec_module(module)
            return module
        finally:
            os.environ.clear()
            os.environ.update(saved)

    def heavy(self, *args, **kwargs):
        return subprocess.run([HEAVY, *args], env=self.env, capture_output=True, text=True, **kwargs)

    def spawn(self, *args):
        p = subprocess.Popen([HEAVY, *args], env=self.env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (p.poll() is None and p.kill(), p.wait(), p.stdout.close(), p.stderr.close()))
        return p

    def wait_for_status(self, text, seconds=10):
        deadline = time.time() + seconds
        while time.time() < deadline:
            out = self.heavy("status").stdout
            if text in out:
                return out
            time.sleep(0.2)
        self.fail("status never showed %r:\n%s" % (text, out))


class Schedule(Sandbox):
    def capacity(self, settings, when):
        self.write_config(settings)
        h = self.module()
        return h.capacity(h.config(), when)

    def test_normal_count_outside_any_window(self):
        self.assertEqual(self.capacity({"slots": 6}, at(2026, 11, 16, 12, 0)), (6, "normal"))

    def test_window_applies_on_its_days_and_hours_only(self):
        cfg = {"slots": 6, "schedule": [{"days": ["sat", "sun"], "from": "16:00", "to": "19:00", "slots": 2}]}
        sat, mon = (2026, 11, 14), (2026, 11, 16)
        self.assertEqual(self.capacity(cfg, at(*sat, 15, 59))[0], 6)
        self.assertEqual(self.capacity(cfg, at(*sat, 16, 0)), (2, "schedule: sat, sun 16:00-19:00"))
        self.assertEqual(self.capacity(cfg, at(*sat, 18, 59))[0], 2)
        self.assertEqual(self.capacity(cfg, at(*sat, 19, 0))[0], 6)
        self.assertEqual(self.capacity(cfg, at(*mon, 17, 0))[0], 6)

    def test_window_without_days_is_daily_and_without_slots_uses_quiet_slots(self):
        cfg = {"slots": 6, "quietSlots": 3, "schedule": [{"from": "09:00", "to": "10:00"}]}
        self.assertEqual(self.capacity(cfg, at(2026, 11, 18, 9, 30)), (3, "schedule: daily 09:00-10:00"))

    def test_window_past_midnight_belongs_to_the_day_it_starts(self):
        cfg = {"slots": 6, "schedule": [{"days": ["fri"], "from": "22:00", "to": "02:00", "slots": 1}]}
        self.assertEqual(self.capacity(cfg, at(2026, 11, 13, 23, 0))[0], 1)  # Friday night
        self.assertEqual(self.capacity(cfg, at(2026, 11, 14, 1, 0))[0], 1)   # early Saturday
        self.assertEqual(self.capacity(cfg, at(2026, 11, 14, 2, 0))[0], 6)
        self.assertEqual(self.capacity(cfg, at(2026, 11, 14, 23, 0))[0], 6)  # Saturday night
        self.assertEqual(self.capacity(cfg, at(2026, 11, 13, 1, 0))[0], 6)   # early Friday

    def test_tightest_of_overlapping_windows_wins(self):
        cfg = {"slots": 6, "schedule": [{"from": "08:00", "to": "20:00", "slots": 4},
                                     {"from": "12:00", "to": "13:00", "slots": 1}]}
        self.assertEqual(self.capacity(cfg, at(2026, 11, 18, 12, 30))[0], 1)
        self.assertEqual(self.capacity(cfg, at(2026, 11, 18, 14, 0))[0], 4)

    def test_bad_settings_are_skipped_and_the_rest_still_apply(self):
        self.write_config({"slots": "many", "slotz": 3, "schedule": [
            {"days": ["someday"], "from": "09:00", "to": "10:00"},
            {"from": "9am", "to": "10:00"},
            {"from": "11:00", "to": "12:00", "slots": 1},
        ]})
        h = self.module()
        cfg = h.config()
        self.assertEqual(cfg["slots"], h.defaults()["slots"])
        self.assertEqual([w["label"] for w in cfg["schedule"]], ["daily 11:00-12:00"])
        out = self.heavy("config")
        self.assertEqual(out.returncode, 0)
        for complaint in ("ignoring slots", "ignoring slotz", "schedule window 1", "schedule window 2"):
            self.assertIn(complaint, out.stderr)
        self.assertNotIn("schedule window 3", out.stderr)

    def test_unreadable_config_falls_back_to_defaults(self):
        with open(os.path.join(self.conf, "config.json"), "w") as f:
            f.write("{ not json")
        self.assertEqual(self.heavy("true").returncode, 0)

    def test_override_beats_the_schedule_then_expires(self):
        self.write_config({"slots": 6, "schedule": [{"from": "00:00", "to": "24:00", "slots": 2}]})
        h = self.module()
        now = time.time()
        self.assertEqual(h.capacity(h.config(), now)[0], 2)
        h.write_json(h.OVERRIDE, {"slots": 4, "until": now + 60})
        self.assertEqual(h.capacity(h.config(), now)[0], 4)
        self.assertEqual(h.capacity(h.config(), now + 61)[0], 2)

    def test_override_commands(self):
        self.write_config({"slots": 6, "quietSlots": 2})
        self.heavy("quiet", "90m")
        self.assertIn("0 of 2 slots busy", self.heavy("status").stdout)
        self.heavy("cap", "9", "2h")
        self.assertIn("0 of 9 slots busy", self.heavy("status").stdout)
        self.assertIn("0 of 6 slots busy", self.heavy("normal").stdout)

    def test_how_long_accepts_lengths_and_clock_times(self):
        h = self.module()
        noon = at(2026, 11, 18, 12, 0)
        self.assertEqual(h.parse_until("90m", noon), noon + 5400)
        self.assertEqual(h.parse_until("2h30m", noon), noon + 9000)
        self.assertEqual(h.parse_until("19:00", noon), noon + 7 * 3600)
        self.assertEqual(h.parse_until("09:00", noon), noon + 21 * 3600)  # tomorrow morning
        with self.assertRaises(SystemExit):
            h.parse_until("soon", noon)

    def test_pressure_caps_the_count(self):
        self.write_config({"slots": 6})
        h = self.module()
        now = time.time()
        os.makedirs(self.state)
        h.write_json(h.PRESSURE, {"level": "tight", "reason": "swap 11.0G", "at": now})
        self.assertEqual(h.capacity(h.config(), now), (1, "under pressure: swap 11.0G"))
        h.write_json(h.PRESSURE, {"level": "critical", "reason": "swap 15.0G", "at": now})
        self.assertEqual(h.capacity(h.config(), now)[0], 0)
        # A reading the watchdog stopped refreshing no longer counts.
        self.assertEqual(h.capacity(h.config(), now + 91)[0], 6)


class Ungated(Sandbox):
    def test_default_patterns_match_runners_and_nothing_looser(self):
        h = self.module()
        cfg = h.defaults()
        cfg["breaker"]["ungated"] = [__import__("re").compile(p) for p in cfg["breaker"]["ungated"]]
        yes = ["node /p/node_modules/.bin/playwright test a.spec.ts",
               "/opt/bin/node /p/node_modules/.bin/vitest run",
               "node /p/node_modules/.bin/vitest",
               "node /p/node_modules/.bin/tsc --noEmit",
               "node /p/node_modules/.bin/jest",
               "node /p/node_modules/.bin/next build",
               "node /p/node_modules/.bin/vite build"]
        no = ["node /p/node_modules/.bin/vite --port 6000",
              "node /p/node_modules/.bin/vite preview",
              "node /p/node_modules/.bin/next dev",
              "node /p/node_modules/.bin/playwright show-report",
              "node /p/server/main.js --port 4000",
              "node /p/bin/ask please run node /p/node_modules/.bin/tsc and playwright test",
              "npm exec vitest run"]
        for cmd in yes:
            self.assertTrue(h.is_ungated(cfg, cmd), cmd)
        for cmd in no:
            self.assertFalse(h.is_ungated(cfg, cmd), cmd)


class Queue(Sandbox):
    def test_exit_code_and_output_pass_through(self):
        out = self.heavy("sh", "-c", "echo hi; exit 7")
        self.assertEqual((out.returncode, out.stdout), (7, "hi\n"))

    def test_missing_command(self):
        out = self.heavy("no-such-command-here")
        self.assertEqual(out.returncode, 127)
        self.assertIn("command not found", out.stderr)

    def test_double_dash_runs_a_command_named_like_a_subcommand(self):
        self.assertEqual(self.heavy("--", "sh", "-c", "exit 3").returncode, 3)

    def test_runs_at_low_priority(self):
        self.write_config({"nice": 7})
        own = os.nice(0)
        self.assertEqual(int(self.heavy("sh", "-c", "ps -o nice= -p $$").stdout), min(own + 7, 20))

    def test_nested_heavy_does_not_take_a_second_slot(self):
        self.write_config({"slots": 1})
        out = self.heavy(HEAVY, "sh", "-c", "echo inner", timeout=10)
        self.assertEqual(out.stdout, "inner\n")

    def test_commands_start_in_the_order_they_asked(self):
        self.write_config({"slots": 1})
        log = os.path.join(self.tmp.name, "order")
        jobs = []
        for name in "abc":
            jobs.append(self.spawn("sh", "-c", "echo %s >> %s; sleep 0.5" % (name, log)))
            time.sleep(0.3)
        for job in jobs:
            self.assertEqual(job.wait(timeout=20), 0)
        with open(log) as f:
            self.assertEqual(f.read().split(), ["a", "b", "c"])
        self.assertIn("ahead in the queue", jobs[2].stderr.read())

    def test_never_more_running_than_slots(self):
        self.write_config({"slots": 2})
        jobs = [self.spawn("sleep", "1.5") for _ in range(4)]
        self.wait_for_status("2 of 2 slots busy, 2 queued")
        for job in jobs:
            self.assertEqual(job.wait(timeout=20), 0)
        self.assertIn("0 of 2 slots busy, 0 queued", self.heavy("status").stdout)

    def test_killed_wrapper_frees_its_slot_and_a_cancelled_wait_leaves_the_queue(self):
        self.write_config({"slots": 1})
        first = self.spawn("sleep", "30")
        self.wait_for_status("1 of 1 slots busy, 0 queued")
        waiting = self.spawn("sleep", "30")
        self.wait_for_status("1 of 1 slots busy, 1 queued")
        waiting.terminate()
        waiting.wait(timeout=5)
        self.wait_for_status("1 of 1 slots busy, 0 queued")
        first.send_signal(signal.SIGKILL)
        first.wait(timeout=5)
        self.wait_for_status("0 of 1 slots busy, 0 queued")
        subprocess.run(["pkill", "-f", "^sleep 30$"])

    def test_sigterm_reaches_the_command(self):
        ready = os.path.join(self.tmp.name, "ready")
        job = self.spawn("sh", "-c", "trap 'exit 42' TERM; touch %s; sleep 30 & wait" % ready)
        deadline = time.time() + 10
        while not os.path.exists(ready) and time.time() < deadline:
            time.sleep(0.1)
        job.terminate()
        self.assertEqual(job.wait(timeout=5), 42)
        subprocess.run(["pkill", "-f", "^sleep 30$"])

    def test_sigterm_during_startup_never_orphans_the_command(self):
        for _ in range(15):
            job = self.spawn("sleep", "32")
            self.wait_for_status("1 of ")
            job.terminate()
            job.wait(timeout=5)
            time.sleep(0.2)
            self.assertEqual(subprocess.run(["pgrep", "-f", "^sleep 32$"], capture_output=True).stdout, b"")


class Breaker(Sandbox):
    def test_once_reports_and_touches_nothing_when_ok(self):
        self.write_config({"breaker": {"criticalLoad": 1e9, "criticalSwapGb": 1e9,
                                       "tightLoad": 1e9, "tightSwapGb": 1e9}})
        job = self.spawn("sleep", "3")
        self.wait_for_status("1 of ")
        out = self.heavy("watchdog", "--once")
        self.assertTrue(out.stdout.startswith("ok (swap "), out.stdout)
        self.assertEqual(job.wait(timeout=10), 0)

    def test_critical_stops_the_newest_job_and_tells_it_why(self):
        self.write_config({"slots": 2, "breaker": {"criticalLoad": 0}})
        older = self.spawn("sleep", "30")
        self.wait_for_status("1 of 2 slots busy")
        newer = self.spawn("sh", "-c", "sleep 31 & sleep 31")
        self.wait_for_status("2 of 2 slots busy")

        listed = self.heavy("watchdog", "--once", "--dry-run").stdout
        self.assertIn("would stop", listed)
        self.assertEqual(newer.poll(), None)

        out = self.heavy("watchdog", "--once")
        self.assertIn("stopping the newest heavy job", out.stdout)
        self.assertEqual(newer.wait(timeout=10), 75)
        self.assertIn("stopped by the pressure breaker", newer.stderr.read())
        self.assertEqual(subprocess.run(["pgrep", "-f", "^sleep 31$"], capture_output=True).stdout, b"")

        self.assertEqual(older.poll(), None)
        # The reading it just wrote holds new work back until the watchdog reports ok again.
        self.assertIn("1 of 0 slots busy", self.heavy("status").stdout)
        older.terminate()


if __name__ == "__main__":
    unittest.main()
