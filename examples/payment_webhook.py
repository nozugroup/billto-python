"""Call only after verifying the payment provider's webhook signature and paid state."""
from billto import BillTo, Config, IdempotencyKey, Invoice


def handle_verified_payment(token: str, event_id: str, order_id: str) -> Invoice:
    config = Config(token).identify('MyShop', '1.0.0', 'integrations@example.com')
    client = BillTo(config)
    return client.orders.mark_paid(
        order_id,
        idempotency_key=IdempotencyKey.from_operation(event_id, 'my-shop'),
        send_email=False,
    )
