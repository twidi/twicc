"""Exercise the build entry point without downloading or invoking Gradle."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


BUILD_SCRIPT = Path(__file__).resolve().parents[1] / "build.sh"
GRADLE_STUB = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$@" > gradle-arguments
if [[ ${FAIL_BUILD:-0} == 1 ]]; then exit 17; fi
case "$*" in
    *:app:assembleGeckoRelease*) engine=gecko; mode=release ;;
    *:app:assembleWebviewRelease*) engine=webview; mode=release ;;
    *:app:assembleGeckoDebug*) engine=gecko; mode=debug ;;
    *:app:assembleWebviewDebug*) engine=webview; mode=debug ;;
    *) exit 18 ;;
esac
profile=
for argument in "$@"; do
    case "$argument" in -PtargetProfile=*) profile=${argument#*=} ;; esac
done
[[ -n "$profile" ]] || exit 19
if [[ ${SKIP_APK:-0} == 1 ]]; then exit 0; fi
mkdir -p "app/build/outputs/apk/$engine/$mode"
printf '%s/%s/%s' "$engine" "$profile" "$mode" > "app/build/outputs/apk/$engine/$mode/app-$engine-$mode.apk"
"""


class BuildScriptTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="twicc build test ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "android project"
        self.project.mkdir()
        (self.project / "app").mkdir()
        (self.project / "app/build.gradle.kts").write_text(
            'versionCode = 30\nversionName = "0.5.19"\n')
        shutil.copyfile(BUILD_SCRIPT, self.project / "build.sh")
        (self.project / "gradlew").write_text(GRADLE_STUB)
        (self.project / "gradlew").chmod(0o755)
        self.environment = {
            **os.environ,
            "ANDROID_HOME": str(self.root / "sdk"),
            "JAVA_HOME": str(self.root / "jdk"),
        }
        java = self.root / "jdk/bin/java"
        java.parent.mkdir(parents=True)
        java.write_text("#!/usr/bin/env bash\nexit 0\n")
        java.chmod(0o755)

    def run_build(self, *arguments):
        return subprocess.run(
            ["bash", str(self.project / "build.sh"), *arguments],
            cwd=self.root,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_selected_profile_moves_only_current_apk_and_hashes_it(self):
        for engine in ("webview", "gecko"):
            for profile in ("64", "32", "emulator", "universal"):
                for mode, flags in (("release", ()), ("debug", ("--debug",))):
                    with self.subTest(engine=engine, profile=profile, mode=mode):
                        stale = self.project / f"app/build/outputs/apk/{engine}/{mode}/stale-universal.apk"
                        stale.parent.mkdir(parents=True, exist_ok=True)
                        stale.write_bytes(b"stale APK from another target")
                        result = self.run_build(engine, profile, *flags)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        output = self.project / f"dist/{engine}/{profile}/{mode}"
                        apk = output / f"twicc-{engine}-{profile}-v0.5.19-{mode}.apk"
                        self.assertEqual(apk.read_text(), f"{engine}/{profile}/{mode}")
                        self.assertFalse((stale.parent / f"app-{engine}-{mode}.apk").exists())
                        self.assertEqual(list(output.glob("*.apk")), [apk])
                        digest = hashlib.sha256(apk.read_bytes()).hexdigest()
                        self.assertEqual((output / "SHA256SUMS").read_text(), f"{digest}  {apk.name}\n")
                        arguments = (self.project / "gradle-arguments").read_text().splitlines()
                        variant = f"{engine.title()}{mode.title()}"
                        self.assertIn(f"-PtargetProfile={profile}", arguments)
                        self.assertIn(":policy:test", arguments)
                        self.assertIn(f":app:test{variant}UnitTest", arguments)
                        self.assertIn(f":app:lint{variant}", arguments)
                        self.assertIn(f":app:assemble{variant}", arguments)
        apks = list((self.project / "dist").rglob("*.apk"))
        self.assertEqual(len(apks), 9)
        for apk in apks:
            digest = hashlib.sha256(apk.read_bytes()).hexdigest()
            self.assertEqual((apk.parent / "SHA256SUMS").read_text(), f"{digest}  {apk.name}\n")

    def test_success_removes_old_apks_only_in_selected_output(self):
        output = self.project / "dist/webview/64/release"
        output.mkdir(parents=True)
        for name in ("app-webview-64-release.apk", "app-webview-64-v0.5.18-29-release.apk"):
            (output / name).write_bytes(b"old APK")
        note = output / "notes.txt"
        note.write_text("keep")
        other = self.project / "dist/webview/32/release/old.apk"
        other.parent.mkdir(parents=True)
        other.write_bytes(b"other profile")
        result = self.run_build("webview", "64")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([p.name for p in output.glob("*.apk")],
                         ["twicc-webview-64-v0.5.19-release.apk"])
        self.assertEqual(note.read_text(), "keep")
        self.assertEqual(other.read_bytes(), b"other profile")

    def test_gecko_success_keeps_only_one_apk_across_profiles_and_modes(self):
        old_paths = (
            "dist/gecko/64/release/old.apk",
            "dist/gecko/32/debug/old.apk",
            "app/build/outputs/apk/gecko/release/old.apk",
            "app/build/outputs/apk/androidTest/gecko/debug/test.apk",
        )
        for path in old_paths:
            apk = self.project / path
            apk.parent.mkdir(parents=True, exist_ok=True)
            apk.write_bytes(b"old Gecko APK")
        old_checksum = self.project / "dist/gecko/64/release/SHA256SUMS"
        old_checksum.write_text("old checksum")
        preserved = (
            "dist/gecko/64/release/notes.txt",
            "dist/webview/64/release/old.apk",
            "app/build/outputs/apk/webview/debug/app.apk",
            "app/build/outputs/apk/androidTest/webview/debug/test.apk",
            ".signing/debug.keystore",
        )
        for path in preserved:
            file = self.project / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b"keep")

        result = self.run_build("gecko", "universal", "--debug")

        self.assertEqual(result.returncode, 0, result.stderr)
        published = self.project / "dist/gecko/universal/debug/twicc-gecko-universal-v0.5.19-debug.apk"
        self.assertEqual(published.read_text(), "gecko/universal/debug")
        for path in old_paths:
            self.assertFalse((self.project / path).exists(), path)
        self.assertFalse(old_checksum.exists())
        self.assertEqual(list((self.project / "app/build/outputs/apk/gecko").rglob("*.apk")), [])
        for path in preserved:
            self.assertEqual((self.project / path).read_bytes(), b"keep", path)

    def test_failed_build_keeps_previously_published_apk(self):
        output = self.project / "dist/gecko/64/release"
        output.mkdir(parents=True)
        old = output / "app-gecko-64-v0.5.18-29-release.apk"
        old.write_bytes(b"previous release")
        checksum = output / "SHA256SUMS"
        checksum.write_text("previous checksum")
        self.environment["FAIL_BUILD"] = "1"
        result = self.run_build("gecko", "64")
        self.assertEqual(result.returncode, 17)
        self.assertEqual(old.read_bytes(), b"previous release")
        self.assertEqual(checksum.read_text(), "previous checksum")

    def test_missing_gradle_apk_keeps_previous_publication(self):
        output = self.project / "dist/gecko/32/debug"
        output.mkdir(parents=True)
        old = output / "previous.apk"
        old.write_bytes(b"previous release")
        checksum = output / "SHA256SUMS"
        checksum.write_text("previous checksum")
        self.environment["SKIP_APK"] = "1"

        result = self.run_build("gecko", "64")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(old.read_bytes(), b"previous release")
        self.assertEqual(checksum.read_text(), "previous checksum")

    def test_missing_invalid_or_removed_arguments_fail_before_gradle(self):
        for arguments in [(), ("gecko",), ("other", "64"), ("gecko", "arm64-v8a"),
                          ("gecko", "--native-tts"), ("gecko", "64", "--native-tts"),
                          ("gecko", "64", "--release"), ("gecko", "64", "--debug", "--debug")]:
            with self.subTest(arguments=arguments):
                result = self.run_build(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertFalse((self.project / "gradle-arguments").exists())
                self.assertIn("64|32|emulator|universal", result.stderr)

    def test_help_does_not_start_gradle(self):
        result = self.run_build("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("64|32|emulator|universal", result.stdout)
        self.assertIn("--debug", result.stdout)
        self.assertFalse((self.project / "gradle-arguments").exists())

    def test_build_entry_runs_i18n_verification(self):
        result = self.run_build("webview", "emulator")
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = (self.project / "gradle-arguments").read_text().splitlines()
        self.assertLess(arguments.index(":app:verifyAndroidI18n"), arguments.index(":policy:test"))

    def test_prebuild_verifies_fingerprints_without_node(self):
        gradle = (BUILD_SCRIPT.parent / "app/build.gradle.kts").read_text()
        self.assertIn('tasks.register("verifyAndroidI18n")', gradle)
        self.assertIn('MessageDigest.getInstance("SHA-256")', gradle)
        self.assertIn('dependsOn(verifyAndroidI18n)', gradle)
        self.assertNotIn('commandLine("npm"', gradle)
        self.assertNotIn('commandLine("node"', gradle)

    def test_failed_build_does_not_publish_a_stale_apk(self):
        self.environment["FAIL_BUILD"] = "1"
        for mode, flags in (("release", ()), ("debug", ("--debug",))):
            with self.subTest(mode=mode):
                stale = self.project / f"app/build/outputs/apk/gecko/{mode}/app-gecko-{mode}.apk"
                stale.parent.mkdir(parents=True)
                stale.write_bytes(b"previous build")
                result = self.run_build("gecko", "64", *flags)
                self.assertEqual(result.returncode, 17)
                self.assertFalse((self.project / "dist").exists())


if __name__ == "__main__":
    unittest.main()
