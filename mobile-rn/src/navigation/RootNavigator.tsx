import React from 'react';
import { View, ActivityIndicator, StyleSheet } from 'react-native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import type { RunRequest, CachedRun } from '../models/run';
import type { TradeEvent } from '../models/events';
import { RunSetupScreen } from '../screens/RunSetupScreen';
import { RunSessionScreen } from '../screens/RunSessionScreen';
import { LoginScreen } from '../screens/LoginScreen';
import { SignUpScreen } from '../screens/SignUpScreen';
import { ForgotPasswordScreen } from '../screens/ForgotPasswordScreen';
import { SettingsScreen } from '../screens/SettingsScreen';
import { RobinhoodConnectScreen } from '../screens/RobinhoodConnectScreen';
import { OrderTicketScreen } from '../screens/OrderTicketScreen';
import { TouchTarget } from '../components/primitives';
import { Icon } from '../components/Icon';
import { useAuth } from '../hooks/useAuth';
import { colors, spacing } from '../theme/theme';

/**
 * Route params. The signed-out flow shows only `Login`; the signed-in flow
 * shows the run + settings screens. `RunSession` is initialized from one of
 * three modes. New signed-in screens (e.g. the deferred brokerage/order flow)
 * can be registered alongside `RunSetup`/`RunSession`/`Settings` without
 * touching the gate.
 */
export type RootStackParamList = {
  Login: undefined;
  SignUp: undefined;
  ForgotPassword: undefined;
  RunSetup: undefined;
  RunSession:
    | { mode: 'started'; runId: string; nodes: string[]; request: RunRequest }
    | { mode: 'cached'; run: CachedRun }
    | { mode: 'restore'; runId: string; request: RunRequest; nodes: string[] };
  Settings: undefined;
  RobinhoodConnect: undefined;
  OrderTicket: { runId: string; trade: TradeEvent };
};

const Stack = createNativeStackNavigator<RootStackParamList>();

/**
 * Top-level auth gate (iOS `AuthGateView`): renders the `LoginScreen` when
 * signed out and the main app when signed in, swapping automatically as
 * `authService` notifies `useAuth`. While persisted tokens are still loading it
 * shows a brief splash so we never flash the login screen for a signed-in user.
 */
export function RootNavigator() {
  const { hydrated, isSignedIn } = useAuth();

  if (!hydrated) {
    return (
      <View style={styles.splash}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  return (
    <Stack.Navigator
      screenOptions={{
        headerStyle: { backgroundColor: colors.background },
        headerTitleStyle: { color: colors.textPrimary },
        headerTintColor: colors.accent,
        contentStyle: { backgroundColor: colors.background },
      }}
    >
      {isSignedIn ? (
        <>
          <Stack.Screen
            name="RunSetup"
            component={RunSetupScreen}
            options={({ navigation }) => ({
              title: 'StockAgents',
              headerRight: () => (
                <TouchTarget
                  onPress={() => navigation.navigate('Settings')}
                  hitSlop={16}
                  accessibilityLabel="Settings"
                  style={styles.headerAction}
                >
                  <Icon name="settings" size={22} color={colors.accent} />
                </TouchTarget>
              ),
            })}
          />
          <Stack.Screen name="RunSession" component={RunSessionScreen} options={{ title: 'Run' }} />
          <Stack.Screen name="Settings" component={SettingsScreen} options={{ title: 'Settings' }} />
          <Stack.Screen
            name="RobinhoodConnect"
            component={RobinhoodConnectScreen}
            options={{ title: 'Robinhood', presentation: 'modal' }}
          />
          <Stack.Screen
            name="OrderTicket"
            component={OrderTicketScreen}
            options={{ title: 'Order ticket', presentation: 'modal' }}
          />
        </>
      ) : (
        <>
          <Stack.Screen name="Login" component={LoginScreen} options={{ headerShown: false }} />
          <Stack.Screen name="SignUp" component={SignUpScreen} options={{ headerShown: false }} />
          <Stack.Screen
            name="ForgotPassword"
            component={ForgotPasswordScreen}
            options={{ headerShown: false }}
          />
        </>
      )}
    </Stack.Navigator>
  );
}

const styles = StyleSheet.create({
  splash: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.background,
  },
  headerAction: {
    paddingHorizontal: spacing.xs,
    alignItems: 'center',
    justifyContent: 'center',
  },
});
