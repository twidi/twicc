#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo 'Usage: ./android/build.sh webview|gecko 64|32|emulator|universal [--debug]'
    echo '64: ARM 64-bit phones/tablets; 32: ARM 32-bit phones/tablets.'
    echo 'emulator: Intel/AMD 64-bit; universal: all supported architectures.'
    echo 'Builds release by default; --debug enables Android and browser debugging.'
    echo 'Builds one tested, linted APK in android/dist/<engine>/<profile>/<mode>/.'
}
if [[ $# == 1 && ( $1 == -h || $1 == --help ) ]]; then usage; exit 0; fi
if [[ $# != 2 && $# != 3 ]]; then usage >&2; exit 2; fi
mode=release
build_type=Release
if [[ $# == 3 ]]; then
    if [[ $3 != --debug ]]; then usage >&2; exit 2; fi
    mode=debug
    build_type=Debug
fi
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
# All variants reuse the same persistent local signing key for installation updates.
export ANDROID_USER_HOME="$project_dir/.signing"
mkdir -p -- "$ANDROID_USER_HOME"
chmod 700 "$ANDROID_USER_HOME"
./gradlew --no-daemon "-PtargetProfile=$profile" :app:verifyAndroidI18n :policy:test ":app:test${variant}${build_type}UnitTest" ":app:lint${variant}${build_type}" ":app:assemble${variant}${build_type}"
output="$project_dir/dist/$engine/$profile/$mode"
version_name=$(sed -nE 's/^[[:space:]]*versionName = "([^"]+)"[[:space:]]*$/\1/p' app/build.gradle.kts)
if [[ ! $version_name =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
    echo 'Cannot read a safe versionName from app/build.gradle.kts.' >&2
    exit 1
fi
apk_name="twicc-$engine-$profile-v$version_name-$mode.apk"
mkdir -p -- "$output"
cp -- "$project_dir/app/build/outputs/apk/$engine/$mode/app-$engine-$mode.apk" "$output/$apk_name"
(cd -- "$output" && sha256sum -- "$apk_name" > SHA256SUMS)
# Remove older publications only after the new APK and checksum exist.
find "$output" -maxdepth 1 -type f -name '*.apk' ! -name "$apk_name" -delete
if [[ -f "$ANDROID_USER_HOME/debug.keystore" ]]; then chmod 600 "$ANDROID_USER_HOME/debug.keystore"; fi
printf '\nAPK: %s/%s\nChecksums: %s/SHA256SUMS\n' "$output" "$apk_name" "$output"
