import React, { useEffect, useRef } from 'react';
import {
  Animated,
  Pressable,
  StyleSheet,
  View,
  Text,
  type PressableProps,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import { colors, radius, spacing, withAlpha } from '../theme/theme';
import { Icon, type IconName } from './Icon';

/** Surface card matching the SwiftUI `Theme.surface.opacity(0.5)` rounded boxes. */
export function Card({
  children,
  style,
}: {
  children: React.ReactNode;
  style?: StyleProp<ViewStyle>;
}) {
  return <View style={[styles.card, style]}>{children}</View>;
}

export function StatusDot({ color }: { color: string }) {
  return <View style={[styles.dot, { backgroundColor: color }]} />;
}

/**
 * Like {@link StatusDot} but pulses opacity 1 ↔ 0.35 on a 1.4s loop. Use it for
 * "live" states (running agents, active SSE stream) where motion conveys that
 * the system is doing something — the static dot reads as frozen.
 */
export function PulseDot({ color, size = 10 }: { color: string; size?: number }) {
  const opacity = useRef(new Animated.Value(1)).current;

  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, {
          toValue: 0.35,
          duration: 700,
          useNativeDriver: true,
        }),
        Animated.timing(opacity, {
          toValue: 1,
          duration: 700,
          useNativeDriver: true,
        }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [opacity]);

  return (
    <Animated.View
      style={{
        width: size,
        height: size,
        borderRadius: size / 2,
        backgroundColor: color,
        opacity,
      }}
    />
  );
}

type Feedback = 'opacity' | 'scale' | 'none';

const PRESSED_STYLES: Record<Feedback, StyleProp<ViewStyle>> = {
  opacity: { opacity: 0.7 },
  scale: { transform: [{ scale: 0.97 }] },
  none: undefined,
};

export type TouchTargetProps = Omit<PressableProps, 'style' | 'children'> & {
  style?: StyleProp<ViewStyle>;
  /** Override the default press-state style. Composed on top of `style` when pressed. */
  pressedStyle?: StyleProp<ViewStyle>;
  /** Default `'opacity'` (drops to 0.7 on press). Set `'scale'` for chip-like buttons or `'none'` to disable. */
  feedback?: Feedback;
  children?: React.ReactNode;
};

/**
 * `Pressable` with the defaults every interactive element in this app should
 * have: a tactile press state, an Android ripple, an 8px hitSlop bump (so 36pt
 * controls still meet the 44pt target), and `accessibilityRole="button"`
 * unless the caller overrides it. Migrate bare `Pressable` usages to this for
 * consistent feedback across the app.
 */
export function TouchTarget({
  style,
  pressedStyle,
  feedback = 'opacity',
  hitSlop,
  accessibilityRole,
  disabled,
  children,
  ...rest
}: TouchTargetProps) {
  const pressed = pressedStyle ?? PRESSED_STYLES[feedback];
  return (
    <Pressable
      hitSlop={hitSlop ?? 8}
      android_ripple={{ color: withAlpha(colors.textPrimary, 0.08) }}
      accessibilityRole={accessibilityRole ?? 'button'}
      accessibilityState={{ disabled: !!disabled }}
      disabled={disabled}
      {...rest}
      style={({ pressed: isPressed }) => [
        style,
        isPressed && !disabled && pressed,
        disabled && styles.disabled,
      ]}
    >
      {children}
    </Pressable>
  );
}

type BannerTone = 'warning' | 'danger' | 'info';

function tintFor(tone: BannerTone): string {
  switch (tone) {
    case 'danger':
      return colors.danger;
    case 'warning':
      return colors.warning;
    case 'info':
      return colors.accent;
    default: {
      const _exhaustive: never = tone;
      return _exhaustive;
    }
  }
}

function defaultIconFor(tone: BannerTone): IconName {
  switch (tone) {
    case 'danger':
    case 'warning':
      return 'alert';
    case 'info':
      return 'info';
    default: {
      const _exhaustive: never = tone;
      return _exhaustive;
    }
  }
}

/**
 * Tinted banner (cache notice / error), mirroring the SwiftUI banners. The
 * `icon` prop is opt-in so existing call sites render unchanged; new call
 * sites should pass `icon` for stronger semantics. `accessibilityRole="alert"`
 * is set on the danger tone so VoiceOver/TalkBack announce errors.
 */
export function Banner({
  text,
  tone,
  icon,
}: {
  text: string;
  tone: BannerTone;
  /** Show a leading icon. Pass `true` for the tone's default, or a name for a custom one. */
  icon?: boolean | IconName;
}) {
  const tint = tintFor(tone);
  const iconName: IconName | null =
    icon === true ? defaultIconFor(tone) : typeof icon === 'string' ? icon : null;
  return (
    <View
      accessibilityRole={tone === 'danger' ? 'alert' : undefined}
      accessibilityLiveRegion={tone === 'danger' ? 'assertive' : 'polite'}
      style={[styles.banner, { backgroundColor: withAlpha(tint, 0.12) }]}
    >
      {iconName && <Icon name={iconName} size={16} color={tint} />}
      <Text style={[styles.bannerText, { color: tint }]}>{text}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: withAlpha(colors.surface, 0.5),
    borderRadius: radius.lg,
    padding: spacing.lg,
  },
  dot: {
    width: 10,
    height: 10,
    borderRadius: 5,
  },
  disabled: {
    opacity: 0.5,
  },
  banner: {
    borderRadius: radius.sm,
    padding: spacing.md,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  bannerText: {
    fontSize: 13,
    flex: 1,
  },
});
