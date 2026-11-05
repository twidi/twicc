#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo 'Usage: ./android/build.sh webview|gecko 64|32|emulator|universal'
    echo '64: ARM 64-bit phones/tablets; 32: ARM 32-bit phones/tablets.'
    echo 'emulator: Intel/AMD 64-bit; universal: all supported architectures.'
    echo 'Builds one tested, linted APK in android/dist/<engine>/<profile>/.'
}
if [[ $# == 1 && ( $1 == -h || $1 == --help ) ]]; then usage; exit 0; fi
if [[ $# != 2 ]]; then usage >&2; exit 2; fi
case "$1" in
    webview) variant=Webview ;;
    gecko) variant=Gecko ;;
    *) usage >&2; exit 2 ;;
esac
engine=$1
case "$2" in
    64|32|emulator|universal) profile=$2 ;;
    *) usage >&2; exit 2 ;;
esac
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd -- "$project_dir"
if ! command -v java >/dev/null 2>&1 && [[ ! -x "${JAVA_HOME:-}/bin/java" ]]; then
    echo 'Set JAVA_HOME to a JDK 17 installation.' >&2
    exit 1
fi
if [[ -z "${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}" && ! -f local.properties ]]; then
    echo 'Set ANDROID_HOME to an Android SDK with platform 37.2 and build-tools 36.0.0.' >&2
    exit 1
fi
# Both flavors use the same persistent debug signing key, independent of the caller's cwd.
export ANDROID_USER_HOME="$project_dir/.signing"
mkdir -p -- "$ANDROID_USER_HOME"
chmod 700 "$ANDROID_USER_HOME"
./gradlew --no-daemon "-PtargetProfile=$profile" :policy:test ":app:test${variant}DebugUnitTest" ":app:lint${variant}Debug" ":app:assemble${variant}Debug"
output="$project_dir/dist/$engine/$profile"
apk_name="app-$engine-$profile-debug.apk"
mkdir -p -- "$output"
cp -- "$project_dir/app/build/outputs/apk/$engine/debug/app-$engine-debug.apk" "$output/$apk_name"
(cd -- "$output" && sha256sum -- "$apk_name" > SHA256SUMS)
if [[ -f "$ANDROID_USER_HOME/debug.keystore" ]]; then chmod 600 "$ANDROID_USER_HOME/debug.keystore"; fi
printf '\nAPK: %s/%s\nChecksums: %s/SHA256SUMS\n' "$output" "$apk_name" "$output"
