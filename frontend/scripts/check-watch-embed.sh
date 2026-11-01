#!/bin/bash
# Checks that a built iPhone App carries the watch app, so installing the iPhone App also
# installs the watch app. Usage: scripts/check-watch-embed.sh path/to/Assistant-iOS.app
# Runs in CI after the iOS build; works on any build (simulator or device) on a Mac.
set -euo pipefail

app="${1:?usage: $0 path/to/Assistant-iOS.app}"
plist() { /usr/libexec/PlistBuddy -c "Print :$2" "$1/Info.plist" 2>/dev/null || true; }
fail() {
    echo "error: $*" >&2
    exit 1
}

[ -d "$app" ] || fail "$app not found"
phone_id="$(plist "$app" CFBundleIdentifier)"
[ -n "$phone_id" ] || fail "$app/Info.plist has no CFBundleIdentifier"

shopt -s nullglob
watch_apps=("$app"/Watch/*.app)
[ "${#watch_apps[@]}" -eq 1 ] ||
    fail "expected exactly one watch app in $app/Watch, found ${#watch_apps[@]}"
watch="${watch_apps[0]}"

watch_id="$(plist "$watch" CFBundleIdentifier)"
companion="$(plist "$watch" WKCompanionAppBundleIdentifier)"
platform="$(plist "$watch" CFBundleSupportedPlatforms:0)"
independent="$(plist "$watch" WKRunsIndependentlyOfCompanionApp)"

[ "$companion" = "$phone_id" ] ||
    fail "watch WKCompanionAppBundleIdentifier is '$companion', the iPhone App is '$phone_id'"
case "$watch_id" in
"$phone_id".*) ;;
*) fail "watch bundle ID '$watch_id' does not start with '$phone_id.'" ;;
esac
case "$platform" in
Watch*) ;;
*) fail "watch app is built for '$platform', not watchOS" ;;
esac
[ "$independent" != "true" ] || fail "watch app is marked as running without the iPhone App"

widgets=("$watch"/PlugIns/*.appex)
[ "${#widgets[@]}" -ge 1 ] || fail "no complication extension in $watch/PlugIns"

echo "ok: $(basename "$watch") ($watch_id, $platform) is inside $(basename "$app") ($phone_id)"
for widget in "${widgets[@]}"; do
    echo "ok: complication $(basename "$widget") ($(plist "$widget" CFBundleIdentifier))"
done
