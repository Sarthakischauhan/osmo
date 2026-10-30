# Signing the macOS desktop app

macOS only keeps Microphone, Screen Recording and Accessibility grants for an app it can identify by its code
signature. An unsigned Osmo build loses them: System Settings still shows **Osmo** switched on, but
that entry belongs to an earlier build, so screenshots and input keep failing with permission errors
no matter how often the toggles are flipped. Sign every local build with the same certificate and the
grants survive rebuilds.

## What needs what

| Goal | Certificate |
| --- | --- |
| Run Osmo on your own Mac with working permissions | Free self-signed `Osmo Dev` (below) |
| Give the app to other people without Gatekeeper warnings | Apple Developer ID ($99/year) plus notarization |

A self-signed certificate lives only in your login keychain. Building on another Mac, or recreating
the certificate, is a new identity: reset and grant the permissions again.

## One-time setup

```sh
bun run --filter @osmo/desktop signing:create
```

`apps/desktop/scripts/create-signing-identity.js` creates a self-signed code signing certificate named
`Osmo Dev` in your login keychain and trusts it for code signing (macOS asks for your login password).
It does nothing if the identity already exists. Check with:

```sh
security find-identity -v -p codesigning   # lists "Osmo Dev"
```

## Build and grant

1. Build: `bun run --filter @osmo/desktop pack` (or `dist` for the DMG). electron-builder finds the
   identity on its own; pass `CSC_NAME="Osmo Dev"` to pin it. `pack` leaves `release/mac/Osmo.app`;
   `dist` also writes `release/Osmo-<version>.dmg`, both under `apps/desktop`. The first build asks
   whether `codesign` may use the key. Choose **Always Allow**, or you get one prompt per signed file.
2. Confirm it is signed:
   ```sh
   codesign -dv apps/desktop/release/mac/Osmo.app   # Authority=Osmo Dev
   ```
3. Clear grants that point at old, unsigned builds, and delete any leftover **Osmo** rows in
   System Settings → Privacy & Security with the − button:
   ```sh
   tccutil reset ScreenCapture dev.sarchauhan.osmo
   tccutil reset Accessibility dev.sarchauhan.osmo
   tccutil reset Microphone dev.sarchauhan.osmo
   ```
4. Copy `Osmo.app` to `/Applications` and launch it from there, not from the DMG or `release/mac`.
5. Start a call from the orb and allow the microphone. Osmo asks macOS itself (Electron never does),
   and a refused microphone fails the call with a pointer to System Settings → Privacy & Security →
   Microphone. Then turn on **Computer support** and allow Accessibility and Screen Recording.
6. **Quit** Osmo from the Osmo menu (Cmd+Q) and reopen it. Screen Recording takes effect only in a
   new process, and closing the window does not quit: it switches to the orb.

The bundled Python that performs clicks and typing is signed with the same certificate. macOS
attributes its input to Osmo, so it needs no grant of its own.

## Hardened runtime

`mac.hardenedRuntime` is `false` in `apps/desktop/package.json`. Under the hardened runtime, the
microphone needs the `com.apple.security.device.audio-input` entitlement, and the bundled Python cannot
load extension modules from a certificate without an Apple team ID. Notarization requires the
hardened runtime, so a Developer ID release must turn it back on with an entitlements file that
grants audio input, JIT and disabled library validation.

## Build speed

The app holds about 8,000 files, nearly all of them data, and signing them one `codesign` run at a
time took 5 to 10 minutes per build. A warm `pack` now takes under a minute:

- **The bundled Python is built and signed once.** `prepare-python.js` skips the rebuild while
  `requirements.txt`, `pyproject.toml`, the vendored `core_server/pyproject.toml` and the script itself
  are unchanged (stamp `build/python/.osmo-inputs`). `sign-python.js` then signs only its Mach-O
  binaries (about 180) in one `codesign` run and records the identity in `build/python/.osmo-signed`.
  A rebuild deletes both stamps. Delete `apps/desktop/build/python` to force both steps.
- **electron-builder skips what needs no signature of its own.** `mac.signIgnore` skips the
  already-signed Python and data files (`.pak`, `.pyc`, fonts, `.nib`, `.dat`, `.bin`, `.icns`,
  `.asar`). They are still sealed by the app's own signature, so `codesign --verify --deep --strict`
  passes, and only the roughly 40 real binaries and bundles are signed each build.
- **No secure timestamps.** `mac.timestamp` is `"none"`, so `codesign` does not call Apple's
  timestamp server for each file. Only notarization needs them, so a Developer ID release removes it.

## Development mode

Day-to-day testing needs no packaged app or signing at all. `bun run dev:desktop` runs the unsigned
Electron from `node_modules` and the server's `.venv` Python, both started from your terminal. macOS
checks the permissions of the app that launched them, so grant Microphone, Screen Recording and
Accessibility to your terminal (Terminal, iTerm, VS Code, …), then quit and reopen that terminal.
Grants given to **Osmo** do not apply in development, and grants given to the terminal do not apply
to a packaged Osmo.
