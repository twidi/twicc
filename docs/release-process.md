# Release Process

When the user asks to make a new release, follow these steps in order:

1. **Check branch:** Verify you're on `main`. If not, stop and inform the user.

2. **Update version numbers:**
   - `pyproject.toml` → `[project]` → `version`
   - `uv.lock` → `[[package]]` → `version` (for the `twicc` package entry)

3. **Update CHANGELOG.md:**
   - Set the version number on the `[Unreleased]` section (if not already done) and add the release date (`YYYY-MM-DD`).
   - **Ensure the release has a `### Summary` section** as its first category (before `### Added` / `### Changed` / `### Fixed`) — a deliberate deviation from Keep a Changelog. It holds a single bold-led bullet in the form `- **vX.Y.Z: Catchy title** — one-line recap of the highlights.`
   - **Check the version inside the summary's bold lead matches the release** and fix it otherwise — in particular, a summary drafted under `[Unreleased]` reads `**Unreleased: …**` and must become `**vX.Y.Z: …**`.
   - **If no summary has been written, STOP and ask the user before continuing.** Do not invent and commit one silently: propose a draft — a short, punchy bold title plus a one-line description — modelled on the existing summaries (read a few first to match their title style and the terseness of their descriptions), then wait for the user to confirm or amend it.

4. **Build:** Run `./scripts/build-release.sh` (~1-2 min). This produces:
   - `dist/twicc-{version}.tar.gz` (sdist, platform-agnostic — both this and the wheel get published to PyPI in step 11)
   - `dist/twicc-{version}-py3-none-any.whl` (single platform-agnostic wheel)

   The Codex CLI binary is downloaded at first launch from the matching GitHub Release (see `src/twicc/providers/codex/runtime.py`) — OpenAI stopped publishing stable Codex binaries to PyPI after 0.136.0 — so TwiCC itself does not need per-platform wheels. The sdist embeds the pre-built frontend assets so `pip install` from source does not need npm. See `hatch_build.py` and `docs/codex-vendoring.md`.

   **Fresh frontend, always:** `build-release.sh` deletes `src/twicc/static/frontend/` before `uv build`. The `hatch_build.py` hook skips the npm build whenever that directory already holds an `index.html` (needed for the sdist→wheel and pip-install-from-sdist paths). Without the wipe, a stale dev build sitting there would be packaged as-is, silently shipping an outdated UI — exactly how 1.7.1 went out with a frontend missing its latest changes. Never package a release without that clean rebuild.

5. **User testing (mandatory):** Ask the user to test the build before continuing:
   ```
   uvx --from /absolute/path/to/dist/twicc-{version}-py3-none-any.whl twicc
   ```
   Remind them to stop any running TwiCC instance first, then visit `http://localhost:3500` to test. **Do not run `uvx` yourself** — this requires user interaction.

6. **Wait for user confirmation.** Only proceed if they say it's OK.

7. **Commit:** Create a commit with message `release: v{version}`.

8. **Create annotated tag** with changelog content extracted from `CHANGELOG.md`:
   ```bash
   git tag -a v{version} -m "Release v{version}

   {changelog content for this version}"
   ```
   **Image URLs:** If the changelog contains relative image paths (e.g., `frontend/public/whats-new/...`), replace them with absolute URLs in the tag message by prefixing with `https://raw.githubusercontent.com/twidi/twicc/main/`. Do **not** modify `CHANGELOG.md` itself.

9. **Push** commit and tag:
   ```bash
   git push && git push --tags
   ```
   From inside a TwiCC session the SSH agent is unavailable (`git push` over `git@github.com:` fails with *Permission denied (publickey)* / *Could not open a connection to your authentication agent*). Push over HTTPS using the `gh` token as the credential helper instead — `gh` is authenticated even when SSH is not:
   ```bash
   git -c credential.helper='!gh auth git-credential' \
       -c url."https://github.com/".insteadOf="git@github.com:" push
   git -c credential.helper='!gh auth git-credential' \
       -c url."https://github.com/".insteadOf="git@github.com:" push --tags
   ```

10. **Create GitHub Release** using the same changelog content (with the same absolute image URLs as the tag):
    ```bash
    gh release create v{version} --title "v{version}" --notes "{changelog content}"
    ```

11. **Publish to PyPI (user action):** Give the user the command to publish both the wheel and the sdist:
    ```
    uvx uv-publish /home/twidi/dev/twicc-poc/dist/twicc-{version}*
    ```
    The glob picks up both `twicc-{version}-py3-none-any.whl` and `twicc-{version}.tar.gz`. The sdist is now safe to publish — it embeds the pre-built frontend assets, so `pip install` from source needs no npm. (The Codex CLI binary is fetched from the matching GitHub Release at first launch, not bundled — see `docs/codex-vendoring.md`.) **Do not run `uv-publish` yourself** unless the user explicitly asks you to.

12. **Deploy the telemetry collector (user action, only if it changed):** Check whether `telemetry-collector/` changed since the previous tag:
    ```bash
    git diff --stat v{previous_version}..HEAD -- telemetry-collector/
    ```
    If it did, the deployed Worker and its transparency page are stale — a payload field documented in the repo but missing from `twicc-telemetry.twidi.com` breaks the transparency promise the whole default-on telemetry rests on. Give the user the commands, to run from `telemetry-collector/`:
    ```
    npm run db:migrate:remote   # only if migrations/ changed
    npm run deploy
    ```
    **Do not run them yourself** — wrangler is authenticated per machine and this publishes to production.
