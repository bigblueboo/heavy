#!/bin/sh
# Install or update heavy and its pressure watchdog for the current user. No sudo.
#   ./install.sh               install, or update after a git pull
#   ./install.sh --uninstall   remove the command and the watchdog (the config stays)
# HEAVY_BIN_DIR picks where the `heavy` command goes (default ~/.local/bin).
set -eu

here=$(cd "$(dirname "$0")" && pwd)
bin_dir=${HEAVY_BIN_DIR:-$HOME/.local/bin}
conf=$HOME/.config/heavy/config.json
label=com.github.bigblueboo.heavy
agent=$HOME/Library/LaunchAgents/$label.plist
domain=gui/$(id -u)

if [ "$(uname)" != Darwin ]; then
	echo "heavy runs on macOS only: the watchdog reads macOS memory counters and runs under launchd." >&2
	exit 1
fi
if [ ! -x /usr/bin/python3 ]; then
	echo "heavy needs /usr/bin/python3. Install the Xcode Command Line Tools: xcode-select --install" >&2
	exit 1
fi

launchctl bootout "$domain/$label" 2>/dev/null || true

if [ "${1:-}" = --uninstall ]; then
	rm -f "$agent" "$bin_dir/heavy"
	echo "Removed the heavy command and its watchdog. Your config is still in $(dirname "$conf")."
	exit 0
fi

mkdir -p "$bin_dir" "$(dirname "$conf")" "$(dirname "$agent")" "$HOME/Library/Logs"
ln -sf "$here/bin/heavy" "$bin_dir/heavy"

if [ ! -e "$conf" ]; then
	slots=$(( $(sysctl -n hw.ncpu) / 2 ))
	[ "$slots" -ge 2 ] || slots=2
	cat >"$conf" <<JSON
{
  "slots": $slots,
  "schedule": []
}
JSON
	echo "Wrote $conf"
fi

cat >"$agent" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$label</string>
	<key>ProgramArguments</key>
	<array>
		<string>/usr/bin/python3</string>
		<string>$here/bin/heavy</string>
		<string>watchdog</string>
	</array>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<true/>
	<!-- Not Background: launchd throttles those exactly when the watchdog is needed. -->
	<key>ProcessType</key>
	<string>Interactive</string>
	<key>StandardOutPath</key>
	<string>$HOME/Library/Logs/heavy.log</string>
	<key>StandardErrorPath</key>
	<string>$HOME/Library/Logs/heavy.log</string>
</dict>
</plist>
PLIST
launchctl bootstrap "$domain" "$agent"

case ":$PATH:" in
*":$bin_dir:"*) ;;
*) echo "Note: $bin_dir is not on your PATH. Add it, or rerun with HEAVY_BIN_DIR set to a directory that is." ;;
esac

sleep 2
"$here/bin/heavy" status
