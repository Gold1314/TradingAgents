import React, { useState } from 'react';
import {
  View,
  Text,
  TextInput,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
} from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import type { RootStackParamList } from '../navigation/RootNavigator';
import { authService } from '../services/authService';
import { describeError } from '../services/apiError';
import { Banner, TouchTarget } from '../components/primitives';
import { colors, fontSize, radius, spacing } from '../theme/theme';

type Props = NativeStackScreenProps<RootStackParamList, 'ForgotPassword'>;

/**
 * Request-side of the password reset flow. Calls
 * `authService.resetPassword(email)` which hits Supabase GoTrue's
 * `POST /auth/v1/recover`. We always show a generic confirmation on success so
 * we don't leak whether the email belongs to a registered account; the
 * deep-link landing screen that completes the reset (link → set new password)
 * is intentionally deferred to a follow-up PR.
 */
export function ForgotPasswordScreen({ navigation }: Props) {
  const [email, setEmail] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);

  const isConfigured = authService.isConfigured;
  const trimmedEmail = email.trim();
  const canSubmit = isConfigured && trimmedEmail.length > 0 && !isLoading;

  const submit = async () => {
    if (!canSubmit) return;
    setIsLoading(true);
    setError(null);
    try {
      await authService.resetPassword(trimmedEmail);
      setSubmitted(true);
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
          <Text style={styles.title}>Reset password</Text>
          <Text style={styles.subtitle}>
            We&apos;ll email you a link to reset your password.
          </Text>
        </View>

        <View style={styles.form}>
          {submitted ? (
            <Banner
              tone="info"
              icon
              text="If an account exists for that email, you'll receive a reset link shortly."
            />
          ) : (
            <>
              <TextInput
                value={email}
                onChangeText={setEmail}
                placeholder="Email"
                placeholderTextColor={colors.textMuted}
                keyboardType="email-address"
                textContentType="emailAddress"
                autoCapitalize="none"
                autoCorrect={false}
                returnKeyType="go"
                onSubmitEditing={submit}
                editable={!isLoading}
                style={styles.input}
              />

              {error && <Banner tone="danger" icon text={error} />}

              {!isConfigured && (
                <Text style={styles.warning}>
                  Supabase isn&apos;t configured. Set EXPO_PUBLIC_SUPABASE_URL and
                  EXPO_PUBLIC_SUPABASE_ANON_KEY (see the mobile-rn README).
                </Text>
              )}

              <TouchTarget
                onPress={submit}
                disabled={!canSubmit}
                feedback="scale"
                style={[
                  styles.cta,
                  { backgroundColor: canSubmit ? colors.accent : colors.border },
                ]}
              >
                {isLoading ? (
                  <ActivityIndicator color={colors.background} />
                ) : (
                  <Text style={styles.ctaText}>Send reset link</Text>
                )}
              </TouchTarget>
            </>
          )}

          <TouchTarget
            onPress={() => navigation.navigate('Login')}
            disabled={isLoading}
            style={styles.linkRow}
          >
            <Text style={styles.link}>Back to sign in</Text>
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
  title: { color: colors.textPrimary, fontSize: 28, fontWeight: '800' },
  subtitle: {
    color: colors.textMuted,
    fontSize: fontSize.md,
    textAlign: 'center',
  },
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
  warning: { color: colors.warning, fontSize: fontSize.sm },
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
  link: { color: colors.accent, fontSize: fontSize.md, fontWeight: '600' },
});
