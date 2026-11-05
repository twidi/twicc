#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo 'Usage: ./android/build.sh webview|gecko [--native-tts]'
    echo 'Builds tested, linted debug APKs in android/dist/<engine>/.'
    echo '--native-tts enables experimental native speech; physical acceptance remains pending.'
}
if [[ $# -lt 1 || $# -gt 2 ]]; then usage >&2; exit 2; fi
case "$1" in
    webview) variant=Webview ;;
    gecko) variant=Gecko ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
esac
engine=$1
gradle_options=()
if [[ ${2:-} == --native-tts ]]; then
    gradle_options+=(-PnativeTts=true)
elif [[ $# == 2 ]]; then
    usage >&2; exit 2
fi
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
./gradlew --no-daemon "${gradle_options[@]}" :policy:test ":app:test${variant}DebugUnitTest" ":app:lint${variant}Debug" ":app:assemble${variant}Debug"
output="$project_dir/dist/$engine"
mkdir -p -- "$output"
for apk in "$project_dir/app/build/outputs/apk/$engine/debug/"*.apk; do
    cp -- "$apk" "$output/"
done
(cd -- "$output" && sha256sum -- ./*.apk > SHA256SUMS)
if [[ -f "$ANDROID_USER_HOME/debug.keystore" ]]; then chmod 600 "$ANDROID_USER_HOME/debug.keystore"; fi
printf '\nAPKs: %s\nChecksums: %s/SHA256SUMS\n' "$output" "$output"
