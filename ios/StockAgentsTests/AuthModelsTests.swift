import XCTest
@testable import StockAgents

/// Supabase GoTrue session/error decoding + SupabaseConfig resolution.
final class AuthModelsTests: XCTestCase {
    private let decoder = JSONDecoder()

    func testSessionDecodingWithExpiresAt() throws {
        let json = """
        {"access_token":"acc","token_type":"bearer","expires_in":3600,"expires_at":1999999999,"refresh_token":"ref","user":{"id":"uid-1","email":"a@b.com"}}
        """
        let session = try decoder.decode(SupabaseSession.self, from: Data(json.utf8))
        XCTAssertEqual(session.accessToken, "acc")
        XCTAssertEqual(session.refreshToken, "ref")
        XCTAssertEqual(session.expiryEpoch, 1999999999)
        XCTAssertEqual(session.authUser, AuthUser(id: "uid-1", email: "a@b.com"))
    }

    func testSessionDerivesExpiryFromExpiresIn() throws {
        let json = #"{"access_token":"acc","refresh_token":"ref","expires_in":60}"#
        let session = try decoder.decode(SupabaseSession.self, from: Data(json.utf8))
        let now = Int(Date().timeIntervalSince1970)
        // Derived expiry should sit ~60s in the future (allow scheduling slack).
        XCTAssertGreaterThan(session.expiryEpoch, now + 50)
        XCTAssertLessThan(session.expiryEpoch, now + 120)
        XCTAssertEqual(session.authUser.id, "")
    }

    func testErrorBodyLegacyShape() throws {
        let json = #"{"error":"invalid_grant","error_description":"Invalid login credentials"}"#
        let body = try decoder.decode(SupabaseAuthErrorBody.self, from: Data(json.utf8))
        XCTAssertEqual(body.displayMessage, "Invalid login credentials")
    }

    func testErrorBodyNewerShape() throws {
        let json = #"{"code":400,"error_code":"invalid_credentials","msg":"Invalid login credentials"}"#
        let body = try decoder.decode(SupabaseAuthErrorBody.self, from: Data(json.utf8))
        XCTAssertEqual(body.displayMessage, "Invalid login credentials")
    }

    func testConfigResolvesFromUserDefaultsOverride() throws {
        let defaults = UserDefaults(suiteName: "AuthModelsTests.override")!
        defaults.removePersistentDomain(forName: "AuthModelsTests.override")
        defaults.set("https://proj.supabase.co", forKey: SupabaseConfig.urlDefaultsKey)
        defaults.set("anon-key-123", forKey: SupabaseConfig.anonKeyDefaultsKey)

        let config = SupabaseConfig.resolve(defaults: defaults, bundle: Bundle(for: AuthModelsTests.self))
        XCTAssertEqual(config?.url.absoluteString, "https://proj.supabase.co")
        XCTAssertEqual(config?.anonKey, "anon-key-123")
        XCTAssertEqual(config?.authBaseURL.absoluteString, "https://proj.supabase.co/auth/v1")
    }

    func testConfigNilWhenUnset() {
        let defaults = UserDefaults(suiteName: "AuthModelsTests.empty")!
        defaults.removePersistentDomain(forName: "AuthModelsTests.empty")
        // A test bundle has no SUPABASE_* Info keys, so resolution returns nil.
        XCTAssertNil(SupabaseConfig.resolve(defaults: defaults, bundle: Bundle(for: AuthModelsTests.self)))
    }
}
