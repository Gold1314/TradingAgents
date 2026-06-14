import React, { useRef, useState } from 'react';
import {
  View,
  Text,
  TextInput,
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
import { colors, fontSize, radius, spacing, withAlpha } from '../theme/theme';

type Props = NativeStackScreenProps<RootStackParamList, 'Login'>;

/**
 * Email/password sign-in against Supabase Auth (iOS plan §3.4.1), ported from
 * the SwiftUI `LoginView` + `LoginViewModel`. Rendered by the auth gate in
 * `RootNavigator` whenever the session is signed out; on success `authService`
 * notifies its subscribers and the gate swaps in the main app automatically —
 * so this screen does not navigate itself.
 */
export function LoginScreen({ navigation }: Props) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const passwordRef = useRef<RNTextInput>(null);

  const isConfigured = authService.isConfigured;
  const canSubmit =
    isConfigured && email.trim().length > 0 && password.length > 0 && !isLoading;

  const submit = async () => {
    if (!canSubmit) return;
    setIsLoading(true);
    setError(null);
    try {
      await authService.signIn(email.trim(), password);
      // No navigation here: the auth gate observes authService and swaps the UI.
    } catch (err) {
      setError(describeError(err));
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <KeyboardAvoidingView
      style={styles.screen}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <View style={styles.container}>
        <View style={styles.header}>
          <View style={styles.logoMark} accessibilityRole="image" accessibilityLabel="StockAgents">
            <Text style={styles.logoMarkText}>SA</Text>
          </View>
          <Text style={styles.title}>StockAgents</Text>
          <Text style={styles.subtitle}>Sign in to run the trading desk.</Text>
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
              textContentType="password"
              secureTextEntry={!passwordVisible}
              autoCapitalize="none"
              autoCorrect={false}
              returnKeyType="go"
              onSubmitEditing={submit}
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

          {error && <Text style={styles.error}>{error}</Text>}

          {!isConfigured && (
            <Text style={styles.warning}>
              Supabase isn&apos;t configured. Set EXPO_PUBLIC_SUPABASE_URL and
              EXPO_PUBLIC_SUPABASE_ANON_KEY (see the mobile-rn README).
            </Text>
          )}

          <TouchTarget
            onPress={() => navigation.navigate('ForgotPassword')}
            disabled={isLoading}
            style={styles.forgotRow}
          >
            <Text style={styles.link}>Forgot password?</Text>
          </TouchTarget>

          <TouchTarget
            onPress={submit}
            disabled={!canSubmit}
            feedback="scale"
            style={[styles.cta, { backgroundColor: canSubmit ? colors.accent : colors.border }]}
          >
            {isLoading ? (
              <ActivityIndicator color={colors.background} />
            ) : (
              <Text style={styles.ctaText}>Sign In</Text>
            )}
          </TouchTarget>

          <TouchTarget
            onPress={() => navigation.navigate('SignUp')}
            disabled={isLoading}
            style={styles.linkRow}
          >
            <Text style={styles.linkMuted}>Don&apos;t have an account? </Text>
            <Text style={styles.link}>Create account</Text>
          </TouchTarget>
        </View>
      </View>
    </KeyboardAvoidingView>
  );
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
  logoMark: {
    width: 64,
    height: 64,
    borderRadius: 16,
    backgroundColor: withAlpha(colors.accent, 0.18),
    alignItems: 'center',
    justifyContent: 'center',
  },
  logoMarkText: {
    color: colors.accent,
    fontSize: 28,
    fontWeight: '800',
    letterSpacing: 0.5,
  },
  title: { color: colors.textPrimary, fontSize: 34, fontWeight: '800' },
  subtitle: { color: colors.textMuted, fontSize: fontSize.md },
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
  forgotRow: {
    alignSelf: 'flex-end',
  },
  cta: {
    borderRadius: radius.md,
    paddingVertical: spacing.md,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: spacing.xs,
  },
  ctaText: { color: colors.background, fontSize: fontSize.lg, fontWeight: '700' },
  linkRow: {
    flexDirection: 'row',
    justifyContent: 'center',
    alignItems: 'center',
    marginTop: spacing.xs,
  },
  linkMuted: { color: colors.textMuted, fontSize: fontSize.md },
  link: { color: colors.accent, fontSize: fontSize.md, fontWeight: '600' },
});
