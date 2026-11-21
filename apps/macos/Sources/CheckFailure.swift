import Foundation

struct CheckFailure: Error, LocalizedError, Sendable {
    let message: String
    var errorDescription: String? { message }
}
