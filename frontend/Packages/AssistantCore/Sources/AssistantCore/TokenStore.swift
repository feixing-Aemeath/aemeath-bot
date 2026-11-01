import Foundation

#if canImport(Security)
    import Security
#endif

/// Where the API client gets the Bearer token from.
public protocol TokenProvider: Sendable {
    func currentToken() throws -> String?
}

/// A fixed token, for tests and previews.
public struct StaticTokenProvider: TokenProvider {
    public let token: String?

    public init(_ token: String?) {
        self.token = token
    }

    public func currentToken() throws -> String? { token }
}

public enum KeychainError: Error, Equatable, Sendable {
    case unexpectedStatus(Int32)
    case invalidData
}

#if canImport(Security)
    /// The token in this device's Keychain (a generic password item). Each App target has its
    /// own Keychain; sharing one item between the iPhone and Mac Apps would need a Keychain
    /// access group, which this skeleton does not set up.
    public struct KeychainTokenStore: TokenProvider {
        public let service: String
        public let account: String

        public init(service: String = "local.assistant.api", account: String = "bearer-token") {
            self.service = service
            self.account = account
        }

        private var baseQuery: [String: Any] {
            [
                kSecClass as String: kSecClassGenericPassword,
                kSecAttrService as String: service,
                kSecAttrAccount as String: account,
            ]
        }

        public func currentToken() throws -> String? {
            var query = baseQuery
            query[kSecReturnData as String] = true
            query[kSecMatchLimit as String] = kSecMatchLimitOne
            var item: CFTypeRef?
            let status = SecItemCopyMatching(query as CFDictionary, &item)
            if status == errSecItemNotFound { return nil }
            guard status == errSecSuccess else { throw KeychainError.unexpectedStatus(status) }
            guard let data = item as? Data, let token = String(data: data, encoding: .utf8) else {
                throw KeychainError.invalidData
            }
            return token
        }

        public func save(_ token: String) throws {
            let data = Data(token.utf8)
            let update: [String: Any] = [kSecValueData as String: data]
            let status = SecItemUpdate(baseQuery as CFDictionary, update as CFDictionary)
            if status == errSecSuccess { return }
            guard status == errSecItemNotFound else { throw KeychainError.unexpectedStatus(status) }
            var add = baseQuery
            add[kSecValueData as String] = data
            // Readable by background refresh after the first unlock; never synced to iCloud.
            add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            let added = SecItemAdd(add as CFDictionary, nil)
            guard added == errSecSuccess else { throw KeychainError.unexpectedStatus(added) }
        }

        public func delete() throws {
            let status = SecItemDelete(baseQuery as CFDictionary)
            guard status == errSecSuccess || status == errSecItemNotFound else {
                throw KeychainError.unexpectedStatus(status)
            }
        }
    }
#endif
