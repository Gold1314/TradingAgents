import * as SecureStore from 'expo-secure-store';

/**
 * Thin wrapper over `expo-secure-store` — the RN equivalent of the Swift
 * `KeychainStore` (Keychain on iOS, Keystore on Android). Used only for the
 * app's own auth tokens; brokerage tokens never leave the server.
 *
 * `WHEN_UNLOCKED_THIS_DEVICE_ONLY` matches the Swift
 * `kSecAttrAccessibleWhenUnlockedThisDeviceOnly`: readable only while the device
 * is unlocked and never migrated to a new device via backup.
 */
const OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
};

export const secureStore = {
  async get(key: string): Promise<string | null> {
    try {
      return await SecureStore.getItemAsync(key, OPTIONS);
    } catch {
      return null;
    }
  },
  async set(key: string, value: string): Promise<void> {
    try {
      await SecureStore.setItemAsync(key, value, OPTIONS);
    } catch {
      // best-effort; a failed secure write should not crash the app
    }
  },
  async delete(key: string): Promise<void> {
    try {
      await SecureStore.deleteItemAsync(key, OPTIONS);
    } catch {
      // ignore
    }
  },
};
