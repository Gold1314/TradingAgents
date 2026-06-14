# Integration notes — Brokerage (Robinhood) + biometric-gated order (v1)

This worker added the **Robinhood connect** flow and the **biometric-gated order
ticket** as self-contained NEW files under `mobile-rn/src/`. Per the file-ownership
rules, it did **not** edit any shared files (`App.tsx`, `RootNavigator.tsx`,
`package.json`, `app.config.ts`, `.env.example`, `apiClient`, `authService`, the
theme, the login/run-setup/run-session screens). The parent must wire the items
below.

## New files (this worker)

| File | Purpose |
| --- | --- |
| `src/models/robinhood.ts` | `RobinhoodAuthorize` + `RobinhoodStatus` types and defensive parsers (`/authorize`, `/status`); `placesRealMoney(status)` helper. |
| `src/services/biometrics.ts` | `biometrics` helper wrapping `expo-local-authentication` (`availableKind()`, `evaluate(reason)`); `BiometricGateError`, `BiometryKind`, `biometryLabel`. |
| `src/services/brokerService.ts` | `brokerService` — `authorize()` / `status()` / `submitOrder()` on `/api/v2/*`, plus the `expo-web-browser` `openAuthSession()` + `callbackStatus()` for OAuth. Reuses `apiClient.baseURL` + `authService` (does not modify them). |
| `src/hooks/useBrokerConnect.ts` | Connect-flow state machine (port of `BrokerConnectViewModel`): authorize → auth session → poll `/status`. |
| `src/hooks/useOrderTicket.ts` | Order-ticket state + the single biometric-gated submit path (port of `OrderTicketViewModel`). |
| `src/screens/RobinhoodConnectScreen.tsx` | Connect UI (port of `RobinhoodConnectView`): status card, trade-safety panel, connect button, "tokens stay on the server" footnote. |
| `src/screens/OrderTicketScreen.tsx` | Order ticket UI (port of `OrderTicketView`): editable side/notional/quantity/type, mode banner, confirm alert, result/failure sections. |
| `src/navigation/brokerRoutes.ts` | `BrokerStackParamList` for the two new screens (to merge into the app's root param list). |

## 1. Dependencies to install

Two Expo modules are imported but not yet in `package.json`:

```bash
npx expo install expo-local-authentication expo-web-browser
```

- `expo-local-authentication` — Face ID / Touch ID / device-passcode gate (the
  LIVE-order guard). Needs a config plugin entry (see §3) for the iOS Face ID
  usage string and a custom dev client (these are not Expo-Go compatible).
- `expo-web-browser` — `openAuthSessionAsync` for the server-mediated OAuth
  redirect. `dismissAuthSession` is called optionally on screen exit.

Until these are installed, `npx tsc --noEmit` reports `Cannot find module
'expo-local-authentication' / 'expo-web-browser'` in `biometrics.ts` and
`brokerService.ts` — expected, resolves on install.

## 2. Navigation registration (`src/navigation/RootNavigator.tsx`)

Add the two routes to `RootStackParamList` and register the screens:

```ts
import { RobinhoodConnectScreen } from '../screens/RobinhoodConnectScreen';
import { OrderTicketScreen } from '../screens/OrderTicketScreen';
import type { TradeEvent } from '../models/events';

// In RootStackParamList:
//   RobinhoodConnect: undefined;
//   OrderTicket: { runId: string; trade: TradeEvent };

<Stack.Screen name="RobinhoodConnect" component={RobinhoodConnectScreen} />
<Stack.Screen name="OrderTicket" component={OrderTicketScreen} />
```

The screen names **must** match `BrokerStackParamList` in
`src/navigation/brokerRoutes.ts` (`RobinhoodConnect`, `OrderTicket`). Once merged,
the local `BrokerStackParamList` typing in the screens is satisfied by the shared
`RootStackParamList` (same shape). Optionally present both as a modal group.

## 3. `app.config.ts` — deep-link scheme + plugins

- The OAuth redirect scheme is already correct: `scheme: 'stockagents'` is set,
  matching `brokerService.APP_REDIRECT_URL = 'stockagents://oauth/robinhood'` and
  the backend's `MOBILE_OAUTH_APP_REDIRECT`. **No change needed** unless the
  backend redirect changes.
- Add the new plugin so the iOS Face ID usage string is generated:

```ts
plugins: [
  'expo-secure-store',
  'expo-web-browser',
  ['expo-local-authentication', { faceIDPermission: 'Authorize LIVE trades with Face ID.' }],
],
```

## 4. Entry point into the brokerage flow (run-session screen)

`OrderTicketScreen` expects route params `{ runId, trade }`, where `trade` is the
run's **proposed** `trade` event. The run-session store already captures this as
`lastTrade` (`src/state/runSessionStore.ts`). The parent should add (in the
forbidden `RunSessionScreen.tsx`, which this worker did not touch) a "Trade" /
"Review order" affordance shown when `lastTrade?.intent?.action` is `buy`/`sell`,
that calls:

```ts
navigation.navigate('OrderTicket', { runId, trade: lastTrade });
```

An entry to `RobinhoodConnect` (e.g. from a Settings screen or the run-session
header) is also useful; the order ticket already navigates there itself on a
`409 not connected`.

## 5. Assumed shared exports (already present — no changes required)

`brokerService` / hooks reuse only existing public exports, so nothing new is
needed from shared files:

- `apiClient.baseURL` (public field) — base URL, honors a Settings repoint.
- `authService.getAuthHeader()` / `attemptRefresh()` / `signOut()`.
- `apiError`: `ApiError` type, `isApiError`, `describeError`.
- `models/order.ts`: `OrderIntent`, `PlaceOrderRequest`.
- `models/events.ts`: `TradeEvent`, `parseAgentEvent`.
- `theme/theme.ts`, `components/primitives.tsx`, `utils/format.ts`.

## 6. Optional `.env.example` note

No new client env vars are required (base URL + Supabase already cover auth). The
server side needs `MOBILE_PUBLIC_BASE_URL` (for the OAuth callback) and
optionally `MOBILE_FAKE_BROKER=true` to exercise the trading UI without live
Robinhood — those are backend env, not client `EXPO_PUBLIC_*`.
