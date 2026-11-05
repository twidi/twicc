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
    *:app:assembleGeckoDebug*) engine=gecko ;;
    *:app:assembleWebviewDebug*) engine=webview ;;
    *) exit 18 ;;
esac
profile=
for argument in "$@"; do
    case "$argument" in -PtargetProfile=*) profile=${argument#*=} ;; esac
done
[[ -n "$profile" ]] || exit 19
mkdir -p "app/build/outputs/apk/$engine/debug"
printf '%s/%s' "$engine" "$profile" > "app/build/outputs/apk/$engine/debug/app-$engine-debug.apk"
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
                with self.subTest(engine=engine, profile=profile):
                    stale = self.project / f"app/build/outputs/apk/{engine}/debug/stale-universal.apk"
                    stale.parent.mkdir(parents=True, exist_ok=True)
                    stale.write_bytes(b"stale APK from another target")
                    result = self.run_build(engine, profile)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    output = self.project / f"dist/{engine}/{profile}"
                    apk = output / f"app-{engine}-{profile}-debug.apk"
                    self.assertEqual(apk.read_text(), f"{engine}/{profile}")
                    self.assertEqual(list(output.glob("*.apk")), [apk])
                    digest = hashlib.sha256(apk.read_bytes()).hexdigest()
                    self.assertEqual((output / "SHA256SUMS").read_text(), f"{digest}  {apk.name}\n")
                    arguments = (self.project / "gradle-arguments").read_text().splitlines()
                    self.assertIn(f"-PtargetProfile={profile}", arguments)
                    self.assertIn(":policy:test", arguments)
                    self.assertIn(f":app:test{engine.title()}DebugUnitTest", arguments)
                    self.assertIn(f":app:lint{engine.title()}Debug", arguments)
        self.assertEqual(len(list((self.project / "dist").rglob("*.apk"))), 8)

    def test_missing_invalid_or_removed_arguments_fail_before_gradle(self):
        for arguments in [(), ("gecko",), ("other", "64"), ("gecko", "arm64-v8a"),
                          ("gecko", "--native-tts"), ("gecko", "64", "--native-tts")]:
            with self.subTest(arguments=arguments):
                result = self.run_build(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertFalse((self.project / "gradle-arguments").exists())
                self.assertIn("64|32|emulator|universal", result.stderr)

    def test_help_does_not_start_gradle(self):
        result = self.run_build("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("64|32|emulator|universal", result.stdout)
        self.assertFalse((self.project / "gradle-arguments").exists())

    def test_failed_build_does_not_publish_a_stale_apk(self):
        stale = self.project / "app/build/outputs/apk/gecko/debug/app-gecko-debug.apk"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"previous build")
        self.environment["FAIL_BUILD"] = "1"
        result = self.run_build("gecko", "64")
        self.assertEqual(result.returncode, 17)
        self.assertFalse((self.project / "dist").exists())


if __name__ == "__main__":
    unittest.main()
