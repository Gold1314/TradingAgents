import Foundation

/// Broker-agnostic order description, mirroring `OrderIntent.to_dict()` in
/// `tradingagents/brokers/intents.py`. Used inside the `trade` SSE event and the
/// (deferred) order ticket. `notional` (USD) and `quantity` (shares) are
/// mutually exclusive sizing fields.
struct OrderIntent: Codable, Equatable {
    let ticker: String
    let action: String      // "buy" | "sell" | "none"
    let rating: String
    let notional: Double?
    let quantity: Double?
    let orderType: String
    let reason: String

    enum CodingKeys: String, CodingKey {
        case ticker
        case action
        case rating
        case notional
        case quantity
        case orderType = "order_type"
        case reason
    }

    var isActionable: Bool { action == "buy" || action == "sell" }
}

/// Body of `POST /api/robinhood/orders/{run_id}` (`PlaceOrderRequest`). All
/// fields optional; the ticker is never client-supplied (forced to the run's).
/// Defined here for completeness; placement is a later milestone.
struct PlaceOrderRequest: Codable, Equatable {
    var action: String?
    var quantity: Double?
    var notional: Double?
    var orderType: String?

    enum CodingKeys: String, CodingKey {
        case action
        case quantity
        case notional
        case orderType = "order_type"
    }
}
