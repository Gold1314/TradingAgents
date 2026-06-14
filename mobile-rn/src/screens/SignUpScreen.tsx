import React, { useRef, useState } from 'react';
import {
  View,
  Text,
  TextInput,
  Pressable,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
  type TextInput as RNTextInput,
} from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import type { RootStackParamList } from '../navigation/RootNavigator';
import { authService } from '../services/authService';
import { describeError } from '../services/apiError';
import { TouchTarget } from '../components/primitives';
import { Icon } from '../components/Icon';
import { colors, fontSize, radius, spacing } from '../theme/theme';

type Props = NativeStackScreenProps<RootStackParamList, 'SignUp'>;

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MIN_PASSWORD_LENGTH = 6;

/**
 * Self-service account creation against Supabase GoTrue (`POST /auth/v1/signup`).
 * Mirrors `LoginScreen`'s dark theme + error/loading patterns. With email
 * confirmation enabled on the project, a successful sign-up returns no session,
 * so we show a "check your email" success state rather than navigating; if
 * confirmation is off, `authService` persists the session and the auth gate in
 * `RootNavigator` swaps in the main app automatically.
 */
export function SignUpScreen({ navigation }: Props) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [confirmVisible, setConfirmVisible] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmationEmail, setConfirmationEmail] = useState<string | null>(null);
  const passwordRef = useRef<RNTextInput>(null);
  const confirmRef = useRef<RNTextInput>(null);

  const isConfigured = authService.isConfigured;
  const trimmedEmail = email.trim();
  const canSubmit =
    isConfigured &&
    trimmedEmail.length > 0 &&
    password.length > 0 &&
    confirm.length > 0 &&
    !isLoading;

  const validate = (): string | null => {
    if (!EMAIL_RE.test(trimmedEmail)) return 'Enter a valid email address.';
    if (password.length < MIN_PASSWORD_LENGTH)
      return `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`;
    if (password !== confirm) return 'Passwords do not match.';
    return null;
  };

  const submit = async () => {
    if (!canSubmit) return;
    const validationError = validate();
    if (validationError) {
      setError(validationError);
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      const result = await authService.signUp(trimmedEmail, password);
      if (result.status === 'confirmation_required') {
        setConfirmationEmail(result.email);
      }
      // status === 'session': the auth gate observes authService and swaps the UI.
    } catch (err) {
      setError(friendlySignUpError(err));
    } finally {
      setIsLoading(false);
    }
  };

  if (confirmationEmail) {
    return (
      <View style={styles.screen}>
        <View style={styles.container}>
          <View style={styles.header}>
            <Text style={styles.logo}>✉️</Text>
            <Text style={styles.title}>Account created</Text>
            <Text style={styles.subtitle}>
              Check {confirmationEmail} to confirm your account, then sign in.
            </Text>
          </View>
          <Pressable
            onPress={() => navigation.navigate('Login')}
            style={[styles.cta, { backgroundColor: colors.accent }]}
          >
            <Text style={styles.ctaText}>Back to sign in</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  return (
    <KeyboardAvoidingView
      style={styles.screen}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <View style={styles.container}>
        <View style={styles.header}>
          <Text style={styles.logo}>📈</Text>
          <Text style={styles.title}>Create account</Text>
          <Text style={styles.subtitle}>Sign up to run the trading desk.</Text>
        </View>

        <View style={styles.form}>
          <TextInput
            value={email}
            onChangeText={setEmail}
            placeholder="Email"
            placeholderTextColor={colors.textMuted}
            keyboardType="email-address"
            textContentType="emailAddress"
            autoCapitalize="none"
            autoCorrect={false}
            returnKeyType="next"
            onSubmitEditing={() => passwordRef.current?.focus()}
            editable={!isLoading}
            style={styles.input}
          />
          <View style={styles.passwordRow}>
            <TextInput
              ref={passwordRef}
              value={password}
              onChangeText={setPassword}
              placeholder="Password"
              placeholderTextColor={colors.textMuted}
              // Opt out of iOS Automatic Strong Password AutoFill: its overlay
              // steals focus and freezes typing in this secure field.
              textContentType="oneTimeCode"
              autoComplete="off"
              secureTextEntry={!passwordVisible}
              autoCapitalize="none"
              autoCorrect={false}
              returnKeyType="next"
              onSubmitEditing={() => confirmRef.current?.focus()}
              editable={!isLoading}
              style={[styles.input, styles.passwordInput]}
            />
            <TouchTarget
              onPress={() => setPasswordVisible((v) => !v)}
              disabled={isLoading}
              accessibilityLabel={passwordVisible ? 'Hide password' : 'Show password'}
              style={styles.passwordToggle}
            >
              <Icon
                name={passwordVisible ? 'eye' : 'eye-off'}
                size={20}
                color={colors.textMuted}
              />
            </TouchTarget>
          </View>
          <View style={styles.passwordRow}>
            <TextInput
              ref={confirmRef}
              value={confirm}
              onChangeText={setConfirm}
              placeholder="Confirm password"
              placeholderTextColor={colors.textMuted}
              textContentType="oneTimeCode"
              autoComplete="off"
              secureTextEntry={!confirmVisible}
              autoCapitalize="none"
              autoCorrect={false}
              returnKeyType="go"
              onSubmitEditing={submit}
              editable={!isLoading}
              style={[styles.input, styles.passwordInput]}
            />
            <TouchTarget
              onPress={() => setConfirmVisible((v) => !v)}
              disabled={isLoading}
              accessibilityLabel={confirmVisible ? 'Hide confirm password' : 'Show confirm password'}
              style={styles.passwordToggle}
            >
              <Icon
                name={confirmVisible ? 'eye' : 'eye-off'}
                size={20}
                color={colors.textMuted}
              />
            </TouchTarget>
          </View>

          {error && <Text style={styles.error}>{error}</Text>}

          {!isConfigured && (
            <Text style={styles.warning}>
              Supabase isn&apos;t configured. Set EXPO_PUBLIC_SUPABASE_URL and
              EXPO_PUBLIC_SUPABASE_ANON_KEY (see the mobile-rn README).
            </Text>
          )}

          <Pressable
            onPress={submit}
            disabled={!canSubmit}
            style={[styles.cta, { backgroundColor: canSubmit ? colors.accent : colors.border }]}
          >
            {isLoading ? (
              <ActivityIndicator color={colors.background} />
            ) : (
              <Text style={styles.ctaText}>Create account</Text>
            )}
          </Pressable>

          <Pressable
            onPress={() => navigation.navigate('Login')}
            disabled={isLoading}
            hitSlop={8}
            style={styles.linkRow}
          >
            <Text style={styles.linkMuted}>Already have an account? </Text>
            <Text style={styles.link}>Back to sign in</Text>
          </Pressable>
        </View>
      </View>
    </KeyboardAvoidingView>
  );
}

/** Map common GoTrue sign-up errors to friendly copy. */
function friendlySignUpError(err: unknown): string {
  const raw = describeError(err);
  const lower = raw.toLowerCase();
  if (lower.includes('already registered') || lower.includes('already been registered') || lower.includes('user already')) {
    return 'That email is already registered. Try signing in instead.';
  }
  if (lower.includes('rate limit') || lower.includes('http 429')) {
    return 'Too many attempts. Please wait a moment and try again.';
  }
  if (lower.includes('password') && (lower.includes('weak') || lower.includes('at least') || lower.includes('should be'))) {
    return `Password is too weak. Use at least ${MIN_PASSWORD_LENGTH} characters.`;
  }
  return raw;
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  container: {
    flex: 1,
    justifyContent: 'center',
    paddingHorizontal: spacing.xl,
    gap: spacing.xl,
    maxWidth: 480,
    width: '100%',
    alignSelf: 'center',
  },
  header: { alignItems: 'center', gap: spacing.sm },
  logo: { fontSize: 44 },
  title: { color: colors.textPrimary, fontSize: 34, fontWeight: '800' },
  subtitle: { color: colors.textMuted, fontSize: 14, textAlign: 'center' },
  form: { gap: spacing.md },
  input: {
    color: colors.textPrimary,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: radius.md,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    fontSize: fontSize.lg,
  },
  passwordRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  passwordInput: {
    flex: 1,
    paddingRight: 48,
  },
  passwordToggle: {
    position: 'absolute',
    right: 0,
    top: 0,
    bottom: 0,
    width: 44,
    alignItems: 'center',
    justifyContent: 'center',
  },
  error: { color: colors.danger, fontSize: fontSize.sm },
  warning: { color: colors.warning, fontSize: fontSize.sm },
  cta: {
    borderRadius: radius.md,
    paddingVertical: spacing.md,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: spacing.xs,
  },
  ctaText: { color: colors.background, fontSize: 16, fontWeight: '700' },
  linkRow: {
    flexDirection: 'row',
    justifyContent: 'center',
    alignItems: 'center',
    marginTop: spacing.xs,
  },
  linkMuted: { color: colors.textMuted, fontSize: 14 },
  link: { color: colors.accent, fontSize: 14, fontWeight: '600' },
});
