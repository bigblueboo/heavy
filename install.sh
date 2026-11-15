#!/bin/sh
# Install or update heavy and its pressure watchdog for the current user. No sudo.
#   ./install.sh               install, or update after a git pull
#   ./install.sh --uninstall   remove the command, the watchdog and heavy's section in your
#                              agents' instructions (the config stays)
#   --agent-notes / --no-agent-notes   add heavy's section to your agents' global
#                              instructions without asking, or skip it (default: ask)
# HEAVY_BIN_DIR picks where the `heavy` command goes (default ~/.local/bin).
set -eu

uninstall=0 notes=ask
for arg in "$@"; do
	case $arg in
	--uninstall) uninstall=1 ;;
	--agent-notes) notes=yes ;;
	--no-agent-notes) notes=no ;;
	*) echo "install.sh: unknown option $arg" >&2; exit 2 ;;
	esac
done

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

if [ "$uninstall" = 1 ]; then
	"$here/bin/heavy" agent-notes remove
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

case $notes in
yes) "$here/bin/heavy" agent-notes install --yes ;;
ask) "$here/bin/heavy" agent-notes install ;;
esac

sleep 2
echo
"$here/bin/heavy" status
