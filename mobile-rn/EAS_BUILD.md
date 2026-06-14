# EAS Build runbook (cloud builds, no local Xcode)

This app is an **Expo SDK 56 managed** project that uses native config-plugin
modules (`expo-local-authentication`, `expo-web-browser`, `expo-secure-store`),
so it requires a **development build / dev client** — it cannot run in Expo Go.

All builds run in the **cloud** via EAS (Expo Application Services). You do not
need Xcode installed. The commands below are the ones **you** run interactively
(they require an Expo login and, for iOS, an Apple Developer account).

> Already scaffolded for you (no login needed): `eas.json` (build profiles) and
> the `extra.eas.projectId` slot in `app.config.ts`.

---

## 0. One-time prerequisites

```bash
cd mobile-rn

# Install the CLI (global) — or prefix every command with `npx`:
npm i -g eas-cli
# (alternatively: use `npx eas-cli@latest <command>` everywhere)

# Log in to your Expo account (create one free at https://expo.dev):
eas login

# Create the EAS project and link it. This prints a projectId.
eas init
```

### Wire up the projectId (required, because this is a *dynamic* config)

`app.config.ts` is a dynamic config, so `eas init` cannot write the projectId
back into it automatically. After `eas init` prints the id, expose it via env:

```bash
# add to mobile-rn/.env (Expo loads .env into process.env at config time)
echo 'EAS_PROJECT_ID=<the-id-eas-init-printed>' >> .env
```

(Alternatively, replace `process.env.EAS_PROJECT_ID` in `app.config.ts` with the
literal id string. The id is not a secret.)

### .env values the app needs at runtime

Copy `.env.example` to `.env` and fill in:

```bash
EXPO_PUBLIC_API_BASE_URL=http://localhost:8000   # FastAPI backend (web/server.py)
EXPO_PUBLIC_SUPABASE_URL=...                      # REQUIRED for sign-in (Supabase Auth)
EXPO_PUBLIC_SUPABASE_ANON_KEY=...                 # REQUIRED for sign-in (anon/publishable key)
```

- iOS simulator can reach `http://localhost:8000`.
- Android emulator reaches the host machine at `http://10.0.2.2:8000`.
- A **physical device** must use your machine's LAN IP (e.g. `http://192.168.x.x:8000`)
  or a tunnel — `localhost` on the phone is the phone itself.

The dev client connects to a **locally-running Metro bundler**, so after
installing the build you must run `npx expo start --dev-client` on your machine
and open the app while Metro is running.

---

## (a) FREE Android APK path — no Apple account, no Google account needed

```bash
cd mobile-rn

# Cloud-build an installable APK (uses the `preview` profile -> buildType: apk).
eas build --profile preview --platform android
```

1. When the build finishes, EAS prints a download URL (and a QR code). Download
   the `.apk` to an Android device/emulator and install it (allow "install from
   unknown sources" if prompted).
2. Start Metro locally and connect the dev client:

   ```bash
   npx expo start --dev-client
   ```

3. Open the installed **StockAgents** app; it attaches to your Metro bundler.

> The `development` profile also produces an installable Android APK
> (`eas build --profile preview` is the leanest path; use
> `eas build --profile development --platform android` if you want the dev menu
> defaults). Both yield an APK because of `android.buildType: "apk"`.

---

## (b) iOS device path — requires a paid Apple Developer Program membership

Prerequisite: an **Apple Developer Program** account ($99/yr). EAS handles
signing in the cloud, but Apple requires the target device's UDID to be
registered in the provisioning profile.

```bash
cd mobile-rn

# 1. Register your iPhone's UDID (opens a URL / QR to enroll the device).
eas device:create

# 2. Cloud-build a dev-client build for a physical device.
#    You'll be prompted for your Apple credentials so EAS can manage signing.
eas build --profile development --platform ios
```

1. Install the resulting build on the registered device (scan the QR / open the
   link; iOS installs the `.ipa` over the air for internal distribution).
2. Start Metro and connect:

   ```bash
   npx expo start --dev-client
   ```

### iOS Simulator build (no Apple account, but needs a Mac with the Simulator)

The `development` profile is configured for a **device** build
(`ios.simulator: false`). To build for the iOS **Simulator** instead, either:

- run with the flag: `eas build --profile development --platform ios --simulator`, or
- add a dedicated profile to `eas.json`:

```jsonc
"development-simulator": {
  "developmentClient": true,
  "distribution": "internal",
  "ios": { "simulator": true }
}
```

then `eas build --profile development-simulator --platform ios`. The output is a
`.app` you drag onto a running Simulator. (Requires a Mac; you said you have no
local Xcode, so the Android APK path is the simplest credential-free option.)

---

## Profiles in `eas.json`

| Profile        | Platform | Output                              | Notes                                   |
| -------------- | -------- | ----------------------------------- | --------------------------------------- |
| `development`  | iOS      | dev-client, device `.ipa`           | `simulator: false`; flip with `--simulator` |
| `development`  | Android  | dev-client `.apk`                   | internal distribution                   |
| `preview`      | Android  | plain installable `.apk`            | leanest free path                       |
| `preview`      | iOS      | internal `.ipa`                     | needs Apple account                     |
| `production`   | both     | store-ready build, `autoIncrement`  | for App Store / Play submission         |

`cli.appVersionSource: "remote"` means EAS owns the build/version numbers.
