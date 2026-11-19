import AssistantCore
import SwiftUI
import UIKit
import UniformTypeIdentifiers

/// No AssistantUI dependency: that product also contains UIApplication/AppKit-only entry points.
final class ShareViewController: UIViewController {
    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground
        let items = extensionContext?.inputItems.compactMap { $0 as? NSExtensionItem } ?? []
        Task { @MainActor [weak self] in
            let content = await ShareItemLoader.content(items)
            guard let self else { return }
            let page = ShareComposer(content: content, queue: ShareQueue.appGroup()) { [weak self] in
                self?.extensionContext?.completeRequest(returningItems: nil)
            }
            let host = UIHostingController(rootView: page)
            addChild(host)
            host.view.translatesAutoresizingMaskIntoConstraints = false
            view.addSubview(host.view)
            NSLayoutConstraint.activate([
                host.view.leadingAnchor.constraint(equalTo: view.leadingAnchor),
                host.view.trailingAnchor.constraint(equalTo: view.trailingAnchor),
                host.view.topAnchor.constraint(equalTo: view.topAnchor),
                host.view.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            ])
            host.didMove(toParent: self)
        }
    }
}

private enum ShareItemLoader {
    static func content(_ items: [NSExtensionItem]) async -> ShareContent {
        var fragments: [ShareFragment] = []
        // Reject the whole share when it includes images/files, including mixed selections.
        let providers = items.flatMap { $0.attachments ?? [] }
        guard providers.count <= 20,
              providers.allSatisfy({
                  !$0.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier)
                    && !$0.hasItemConformingToTypeIdentifier(UTType.image.identifier)
                    && ($0.hasItemConformingToTypeIdentifier(UTType.url.identifier)
                        || $0.hasItemConformingToTypeIdentifier(UTType.text.identifier))
              }) else { return ShareContent(fragments: []) }
        let deadline = Date().addingTimeInterval(4)
        for item in items {
            let title = item.attributedTitle?.string ?? item.attributedContentText?.string
            let attachments = item.attachments ?? []
            if attachments.isEmpty, let text = item.attributedContentText?.string {
                fragments.append(ShareFragment(text: text, title: item.attributedTitle?.string))
            }
            for provider in attachments {
                guard Date() < deadline else { return ShareContent(fragments: []) }
                let countBefore = fragments.count
                if provider.hasItemConformingToTypeIdentifier(UTType.url.identifier),
                   let value = await load(provider, type: UTType.url.identifier, deadline: deadline),
                   let url = (value as? URL) ?? (value as? String).flatMap(URL.init(string:)),
                   ["http", "https"].contains(url.scheme?.lowercased() ?? "") {
                    fragments.append(ShareFragment(url: url, title: title))
                } else if provider.hasItemConformingToTypeIdentifier(UTType.text.identifier),
                          let value = await load(provider, type: UTType.text.identifier, deadline: deadline) {
                    let text: String?
                    if let string = value as? String { text = string }
                    else if let rich = value as? NSAttributedString { text = rich.string }
                    else if let data = value as? Data { text = String(data: data, encoding: .utf8) }
                    else { text = nil }
                    if let text { fragments.append(ShareFragment(text: ShareContent.limit(text, to: ShareContent.textLimit + 1), title: item.attributedTitle?.string)) }
                }
                guard fragments.count > countBefore else { return ShareContent(fragments: []) }
            }
        }
        return ShareContent(fragments: fragments)
    }
    private static func load(_ provider: NSItemProvider, type: String, deadline: Date) async -> NSSecureCoding? {
        await withCheckedContinuation { (continuation: CheckedContinuation<NSSecureCoding?, Never>) in
            let completion = ShareLoadCompletion(continuation)
            provider.loadItem(forTypeIdentifier: type, options: nil) { value, _ in
                completion.finish(value)
            }
            DispatchQueue.global().asyncAfter(deadline: .now() + max(0, min(2, deadline.timeIntervalSinceNow))) {
                completion.finish(nil)
            }
        }
    }
}

/// Provider callbacks can arrive after a timeout. Resume the continuation exactly once.
private final class ShareLoadCompletion: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<NSSecureCoding?, Never>?
    init(_ continuation: CheckedContinuation<NSSecureCoding?, Never>) { self.continuation = continuation }
    func finish(_ value: NSSecureCoding?) {
        lock.lock()
        let pending = continuation
        continuation = nil
        lock.unlock()
        pending?.resume(returning: value)
    }
}
