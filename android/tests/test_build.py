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

    def test_selected_profile_copies_only_current_apk_and_hashes_it(self):
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
                        apk = output / f"app-{engine}-{profile}-{mode}.apk"
                        self.assertEqual(apk.read_text(), f"{engine}/{profile}/{mode}")
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
        self.assertEqual(len(apks), 16)
        for apk in apks:
            digest = hashlib.sha256(apk.read_bytes()).hexdigest()
            self.assertEqual((apk.parent / "SHA256SUMS").read_text(), f"{digest}  {apk.name}\n")

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
