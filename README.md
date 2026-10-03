# BillTo Python SDK

[![CI](https://github.com/nozugroup/billto-python/actions/workflows/ci.yml/badge.svg)](https://github.com/nozugroup/billto-python/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Python client for the [BillTo](https://billto.pl) invoicing API. Manage invoices,
KSeF submissions, orders, contractors, products, incoming invoices and warehouse operations.

- Python **3.10+**, with no runtime dependencies.
- Synchronous client, type annotations and a `py.typed` marker.
- Lazy pagination, typed exceptions and binary downloads.
- `Decimal` support for money, automatic idempotency keys, retries and response diagnostics.

[API reference](https://billto.pl/api.json/#/) · [PHP SDK](https://github.com/nozugroup/billto-php) · [Node.js SDK](https://github.com/nozugroup/billto-node)

## Installation

Install from GitHub (requires Git):

```bash
python -m pip install "billto-python @ git+https://github.com/nozugroup/billto-python.git@main"
```

For reproducible deployments, replace `main` with a commit SHA. To install a local checkout:

```bash
python -m pip install ../billto-python
```

The distribution name is `billto-python`; the import name is `billto`.
Installing from GitHub does not require a PyPI release.

## Quick start

Create a token in **BillTo → Settings → API tokens**, with the scopes your integration needs.
Set `BILLTO_TOKEN` in the process environment. Use a sandbox token for this example.
Save it as `example.py`, then run `python example.py`.

```python
import os
from billto import BillTo

client = BillTo.sandbox(os.environ["BILLTO_TOKEN"])
page = client.invoices.list({"status": "issued"}, page=1, per_page=50)

for invoice in page:
    print(invoice.id, invoice.invoice_number, invoice.get("totals.gross"))
```

Use `BillTo(token)` for production. Calls are synchronous and perform blocking I/O.
The following snippets use `client` from the quick start.

## Create an invoice

This creates a draft. Replace the sample dates and buyer details with your own.
Set `"issue": True` and supply an appropriate `series_id` to issue it immediately.

```python
from decimal import Decimal
from billto import InvoiceType, PaymentMethod, VatRate

invoice = client.invoices.create({
    "type": InvoiceType.VAT,
    "issue_date": "2026-10-03",
    "payment_method": PaymentMethod.TRANSFER,
    "currency": "PLN",
    "amount_entry_mode": "net",
    "buyer": {"name": "ACME sp. z o.o.", "tax_type": "local", "tax_number": "5261040828"},
    "items": [
        {"name": "Consulting", "quantity": 1, "unit_price": Decimal("150.00"), "vat_type": VatRate.RATE23},
    ],
})

print(invoice.id, invoice.gross_total())
```

Payloads are dictionaries using the API's original field names. SDK methods and options use
snake_case, for example `record_payment`, `paid_at` and `idempotency_key`.
SDK enums are string enums and may be used directly in payloads.

## Available resources

| Resource | Operations |
| --- | --- |
| `client.invoices` | list, all, get, create, update, delete, issue, send_to_ksef, ksef_status, wait_for_ksef, pdf, xml, send_email, public_link, record_payment, mark_paid, delete_payment |
| `client.orders` | list, all, get, create, update, delete, confirm, close, cancel, send_confirmation, pdf, issue_advance, mark_paid, issue_correction, issue_kor |
| `client.contractors` | list, all, get, create, update, delete, find_by_tax_number, insights |
| `client.products` | list, all, get, create, update, delete, price |
| `client.price_groups` | list |
| `client.incoming_invoices` | list, all, get, updated_since, xml, accept, reject, vote |
| `client.warehouse` | warehouses, stocks, all_stocks, movements, all_movements, move, receive, issue |
| `client.reports` | profitability |
| `client.invoice_series` | list, default |
| `client.bank_accounts` | list, default |
| `client.exchange_rates` | list, rate |

Token scopes, plan requirements and payload fields are defined by the API reference.
See [resource signatures](billto/resources.py) and [entity annotations](billto/entities.py).

## Pagination and entities

`list(filters, page, per_page)` fetches one `Page`. `all(filters, per_page)` returns a lazy
iterator starting at page 1. Explicit pagination arguments override `page` and `per_page`
entries in filters.

```python
for row in client.invoices.all({"status": "issued"}, per_page=100):
    print(row.invoice_number)

for page in client.contractors.all().pages():
    print(page.current_page, page.last_page, len(page))
```

Use `paginator.collect()` only when you want all matching records in memory.
Entities expose attributes, dictionary access (`invoice["id"]`), dot paths
(`invoice.get("totals.gross")`) and `to_dict()`. Fields whose names collide with methods,
such as a payment's `amount`, remain accessible through `get()` or dictionary access.

Missing optional attributes and nullable values return `None` through attribute access or
`get()`; dictionary access raises `KeyError` for missing keys. `invoice.items()` returns
related entities or `[]`, and `invoice.buyer()` returns an entity or `None`.

## Dates and money

Use `datetime.date` or `YYYY-MM-DD` strings for payment dates, report ranges and exchange rates.
Calendar helpers preserve the date component of a supplied `date` or `datetime` without
converting it to UTC. Timestamp inputs such as `updated_since` and vote `decided_at` accept
ISO 8601 strings or `datetime`; use timezone-aware values for unambiguous instants.

```python
from datetime import date

client.invoices.record_payment(
    invoice.id,
    Decimal("184.50"),
    paid_at=date(2026, 10, 3),
    payment_method="transfer",
)
```

`amount(path)`, `gross_total()` and `remaining_amount()` return `Decimal` or `None`.
Finite `Decimal` values are serialized as decimal strings, including inside nested payloads,
so precision is preserved. NaN and infinity are rejected before an HTTP request is sent.
Prefer `Decimal("150.00")` over `Decimal(150.1)`, which starts with a binary floating-point value.
Raw response fields retain their API representation.

## Idempotency and retries

POST, PUT and PATCH requests receive an automatic `Idempotency-Key` by default.
The same key and body are reused during retries. Separate method calls get separate keys;
pass a stable key for an external event that may be delivered more than once.

```python
from billto import IdempotencyKey

# Call after verifying the payment provider's webhook signature and paid state.
order_id = "replace-with-order-id"
event_id = "payment-event-123"
client.orders.mark_paid(
    order_id,
    idempotency_key=IdempotencyKey.from_operation(event_id, "my-shop"),
    send_email=False,
)
```

By default, the SDK retries twice after network failures, HTTP 502/503/504, or HTTP 429
with a usable `Retry-After` header. Retry delays are capped by `max_retry_delay`.
A 429 without a usable retry header is not retried. POST/PUT/PATCH requests without an
idempotency key are not retried. Disable automatic keys with `with_auto_idempotency(False)`.
The API controls the retention period and semantics of replayed responses.

## Configuration and diagnostics

```python
from billto import ApiResponse, Config, SANDBOX_BASE_URL

config = Config(
    os.environ["BILLTO_TOKEN"],
    base_url=SANDBOX_BASE_URL,
    max_retries=2,
    timeout=30,
    retry_base_delay=0.5,
    max_retry_delay=10,
).identify("MyShop", "1.0.0", "integrations@example.com").with_correlation_id("checkout-123")

def log_response(response: ApiResponse) -> None:
    print(response.status, response.header("X-Trace-Id"))

observed_client = BillTo(config, on_response=log_response)
```

Timeouts and delays are in seconds. Configuration is immutable; `with_*` methods return
a new configuration. The production base URL is `https://billto.pl/api/v1`.
The urllib transport timeout applies to blocking socket operations, not a total operation deadline.
Identify distributed integrations with your product name, version and contact details.

`on_response` receives every HTTP response, including failed attempts and binary downloads,
before normal response handling. It must be a synchronous callable. If it raises, the operation
aborts without retrying; handle logging failures inside your callback if they should not abort it.
Network failures have no response to observe. `with_correlation_id(None)` removes the header.
Transport options also accept custom `sender`, `sleeper` and `key_generator` callables.

## Errors

```python
from billto import ApiError, ValidationError, RateLimitError, TransportError

try:
    client.invoices.get("replace-with-invoice-id")
except ValidationError as error:
    print(error.errors, error.first("series_id"))
except RateLimitError as error:
    print(error.retry_after, error.is_plan_limit())
except ApiError as error:
    print(error.status, error.response.header("X-Trace-Id"))
except TransportError as error:
    print(error.__cause__)
```

All SDK exceptions extend `BillToError`. Status-specific classes also include
`AuthenticationError` (401), `PaymentRequiredError` (402), `ForbiddenError` (403),
`NotFoundError` (404), `ConflictError` (409) and `ServerError` (5xx).
`UnexpectedResponseError` reports an unexpected response shape or redirect.

## KSeF and downloads

For an issued invoice eligible for KSeF, with the required team configuration and token scope:

```python
client.invoices.send_to_ksef(invoice.id)
status = client.invoices.wait_for_ksef(invoice.id, timeout_seconds=120)

if status.is_assigned():
    client.invoices.xml(invoice.id).save_to("invoice.xml")
elif status.has_errors():
    print(status.errors)
else:
    print("Still pending; poll again later.")

client.invoices.pdf(invoice.id).save_to("invoice.pdf")
```

Polling returns the last status when its time budget elapses; an in-flight request uses the
HTTP timeout. Binary responses expose `content` (`bytes`), `content_type`, `filename`,
`size()`, `save_to(path)` and `save_in(directory)`.

## Development

```bash
git clone https://github.com/nozugroup/billto-python.git
cd billto-python
python -m pip install -e .
python -m unittest discover -s tests -v
python -m pip wheel --no-deps --wheel-dir dist .
```

Tests use fake HTTP responses and a local server; no BillTo token is required.
CI runs on Python 3.10, 3.12 and 3.13, on Linux and Windows.
See [examples](examples) for invoice creation, payment webhooks and incremental synchronization.
Report issues at [GitHub Issues](https://github.com/nozugroup/billto-python/issues).

## License

MIT. See [LICENSE](LICENSE).
