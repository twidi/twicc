"""Hatch build hook: builds the Vue.js frontend before packaging."""

import os
import shutil
import subprocess
import sys

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

FRONTEND_DIR = "frontend"
STATIC_DIR = os.path.join("src", "twicc", "static", "frontend")


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        frontend_dir = os.path.join(self.root, FRONTEND_DIR)

        if not os.path.isfile(os.path.join(frontend_dir, "package.json")):
            # No frontend source (shouldn't happen but fail safe).
            return

        if self.target_name == "wheel":
            # When uv build creates sdist then wheel, the wheel is built from
            # the extracted sdist which already contains fresh assets.
            static_dir = os.path.join(self.root, STATIC_DIR)
            if os.path.isfile(os.path.join(static_dir, "index.html")):
                return

        # shell=True is required on Windows to resolve npm.cmd wrapper
        use_shell = sys.platform == "win32"

        subprocess.run(
            ["npm", "ci"],
            cwd=frontend_dir,
            check=True,
            shell=use_shell,
        )

        subprocess.run(
            ["npm", "run", "build"],
            cwd=frontend_dir,
            check=True,
            shell=use_shell,
        )

    def clean(self, versions):
        static_dir = os.path.join(self.root, STATIC_DIR)
        if os.path.exists(static_dir):
            shutil.rmtree(static_dir)
