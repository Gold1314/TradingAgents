import { Platform } from 'react-native';
import * as Haptics from 'expo-haptics';

/**
 * Thin, fire-and-forget wrapper around `expo-haptics`. We deliberately keep
 * this OUT of `TouchTarget` so the design system stays neutral and each call
 * site picks the intensity that matches the action's consequence — over-
 * buzzing every tap makes the app feel cheap. Each function is silent on web
 * (`expo-haptics` is already a no-op there; the Platform guard is defensive in
 * case that changes) and swallows any rejection so a flaky haptics engine
 * never blocks the UI thread.
 */

function fireAndForget(promise: Promise<void>): void {
  promise.catch(() => {
    /* haptics are best-effort; never surface engine errors to the UI */
  });
}

/**
 * Light "selection changed" tick. Use for toggling state where the user is
 * choosing between equivalent options — analyst chips, asset-type segment,
 * Advanced disclosure, Buy/Sell when switching back to the non-destructive
 * side.
 */
export function selectionFeedback(): void {
  if (Platform.OS === 'web') return;
  fireAndForget(Haptics.selectionAsync());
}

/**
 * General button-press impact. Use for primary CTAs that commit a non-
 * destructive action — "Run analysis", the resume card, the buy-side review
 * button. Heavier than {@link selectionFeedback} so the user feels a deliberate
 * "press" rather than a passive tick.
 */
export function lightFeedback(): void {
  if (Platform.OS === 'web') return;
  fireAndForget(Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light));
}

/**
 * Warning notification pattern. Use for consequential or destructive
 * interactions — opening the LIVE sell confirmation, flipping the segment to
 * Sell. The triple-pulse pattern is the iOS HIG cue for "are you sure?" and
 * sets it apart from routine selection ticks.
 */
export function warningFeedback(): void {
  if (Platform.OS === 'web') return;
  fireAndForget(Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning));
}
