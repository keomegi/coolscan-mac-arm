#!/bin/bash
# Builds Coolscan.app: the Coolscan page in its own window, with its own icon in the Dock.
#
#   mac/build_app.sh                 puts it in ~/Applications (Launchpad and Spotlight find it there)
#   mac/build_app.sh /Applications   or anywhere else you like
#
# The app runs this folder's coolscan.py with this folder's .venv, so build it again if you move
# the folder. Changes to the Python files or the page need no new build, only a restart of the app.
# Needs the Xcode Command Line Tools (for swiftc):  xcode-select --install
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
dest="${1:-$HOME/Applications}"
app="$dest/Coolscan.app"

if ! command -v swiftc >/dev/null 2>&1; then
    echo "swiftc is missing. Install the Xcode Command Line Tools first:  xcode-select --install" >&2
    exit 1
fi
if [ ! -x "$here/.venv/bin/python" ]; then
    echo "Note: $here/.venv is missing. The app needs it: python3 -m venv .venv; source .venv/bin/activate; pip install -r requirements.txt" >&2
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

echo "Compiling…"
swiftc -O -swift-version 5 -o "$work/Coolscan" "$here/mac/Coolscan.swift" -framework Cocoa -framework WebKit

esc() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }
version="$(cd "$here" && git rev-parse --short HEAD 2>/dev/null || echo dev)"

mkdir -p "$work/Coolscan.app/Contents/MacOS" "$work/Coolscan.app/Contents/Resources"
cp "$work/Coolscan" "$work/Coolscan.app/Contents/MacOS/Coolscan"
cp "$here/mac/AppIcon.icns" "$work/Coolscan.app/Contents/Resources/AppIcon.icns"
cat > "$work/Coolscan.app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Coolscan</string>
    <key>CFBundleDisplayName</key><string>Coolscan</string>
    <key>CFBundleIdentifier</key><string>local.coolscan.app</string>
    <key>CFBundleExecutable</key><string>Coolscan</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleVersion</key><string>$(esc "$version")</string>
    <key>LSMinimumSystemVersion</key><string>12.0</string>
    <key>LSApplicationCategoryType</key><string>public.app-category.photography</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSAppTransportSecurity</key>
    <dict><key>NSAllowsLocalNetworking</key><true/></dict>
    <key>CoolscanFolder</key><string>$(esc "$here")</string>
</dict>
</plist>
PLIST

# Signed for this Mac only (no Apple ID needed); built here, so macOS does not quarantine it.
codesign --force --sign - "$work/Coolscan.app" >/dev/null

mkdir -p "$dest"
rm -rf "$app"
mv "$work/Coolscan.app" "$app"
# Tell the Finder and the Dock about the (new) icon.
touch "$app"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$app" 2>/dev/null || true

echo "Built $app"
echo "Open it from Launchpad or Spotlight (Coolscan). To keep it in the Dock: while it runs, right-click its icon > Options > Keep in Dock."
