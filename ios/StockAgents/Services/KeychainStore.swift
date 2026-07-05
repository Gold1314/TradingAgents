import Foundation
import Security

/// Thin wrapper over the Security framework for storing small secrets (the app
/// auth token). Items are `kSecAttrAccessibleWhenUnlockedThisDeviceOnly` so they
/// never sync off-device (plan §5.2). Brokerage tokens are NEVER stored here —
/// they stay server-side, per the plan.
struct KeychainStore {
    let service: String

    /// Store (or replace) a value. Returns `true` on success so callers can
    /// react to a Security-framework failure rather than silently losing a
    /// token. `@discardableResult` keeps existing call sites unchanged.
    @discardableResult
    func set(_ value: String, for key: String) -> Bool {
        guard let data = value.data(using: .utf8) else { return false }
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
        ]
        // Delete any existing item first; `errSecItemNotFound` is expected and
        // fine — we only care whether the subsequent add succeeds.
        SecItemDelete(query as CFDictionary)

        var attributes = query
        attributes[kSecValueData as String] = data
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        let status = SecItemAdd(attributes as CFDictionary, nil)
        return status == errSecSuccess
    }

    func get(_ key: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess, let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    /// Remove a value. Returns `true` when the item is gone afterwards (either
    /// deleted, or it wasn't there to begin with).
    @discardableResult
    func delete(_ key: String) -> Bool {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
        ]
        let status = SecItemDelete(query as CFDictionary)
        return status == errSecSuccess || status == errSecItemNotFound
    }
}
