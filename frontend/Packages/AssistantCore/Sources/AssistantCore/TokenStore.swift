import Foundation

#if canImport(Security)
    import Security
#endif

/// Where the API client gets the Bearer token from.
public protocol TokenProvider: Sendable {
    func currentToken() throws -> String?
}

/// A writable token item; the mirror can be tested without accessing a device's Keychain.
public protocol MutableTokenStore: TokenProvider {
    func save(_ token: String) throws
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
    /// A device-only generic password; an explicit group can share it with an extension.
    public struct KeychainTokenStore: MutableTokenStore {
        public let service: String
        public let account: String
        public let accessGroup: String?

        public init(service: String = "local.assistant.api", account: String = "bearer-token", accessGroup: String? = nil) {
            self.service = service
            self.account = account
            self.accessGroup = accessGroup
        }

        private var baseQuery: [String: Any] {
            var query: [String: Any] = [
                kSecClass as String: kSecClassGenericPassword,
                kSecAttrService as String: service,
                kSecAttrAccount as String: account,
            ]
            if let accessGroup { query[kSecAttrAccessGroup as String] = accessGroup }
            return query
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

#if canImport(Security)
/// The App keeps the old private item and mirrors it to the shared group. Mac remains private.
public struct AppTokenStore: TokenProvider {
    private let legacy: any MutableTokenStore
    private let shared: (any MutableTokenStore)?
    public init(legacy: any MutableTokenStore, shared: (any MutableTokenStore)?) {
        self.legacy = legacy
        self.shared = shared
    }
    public init(bundle: Bundle = .main) {
        // No group named: the item the App saved before it had any access group is found
        // wherever it is. Naming the App's own group here would lose the user's token on a
        // device where the name in Info.plist came out differently from the group it is in.
        legacy = KeychainTokenStore()
        shared = SharedKeychainToken.store(bundle: bundle)
    }
    public func currentToken() throws -> String? {
        if let token = try legacy.currentToken(), !token.isEmpty { return token }
        return try shared?.currentToken()
    }
    public func save(_ token: String) throws {
        try legacy.save(token)
        // A missing shared entitlement must not break the App's existing connection.
        try? shared?.save(token)
    }
    public func synchronize() {
        if let token = try? currentToken(), !token.isEmpty { try? shared?.save(token) }
    }
}
public enum SharedKeychainToken {
    public static func store(bundle: Bundle = .main) -> KeychainTokenStore? {
        guard let group = bundle.object(forInfoDictionaryKey: "AssistantSharedKeychainGroup") as? String,
              !group.isEmpty, !group.contains("$(") else { return nil }
        return KeychainTokenStore(accessGroup: group)
    }
}
#endif
