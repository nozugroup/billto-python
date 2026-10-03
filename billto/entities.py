"""Read-only API entities. Use get() or [] for fields colliding with methods."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, TypeVar
from .enums import InvoiceType, InvoiceStatus, OrderStatus, IncomingInvoiceStatus

T = TypeVar("T", bound="Entity")


class Entity:
    def __init__(self, attributes: dict[str, Any]):
        object.__setattr__(self, "_attributes", deepcopy(attributes))

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable.")

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return self.get(name)

    def __getitem__(self, name: str) -> Any:
        return deepcopy(self._attributes[name])

    def get(self, path: str, default: Any = None) -> Any:
        value: Any = self._attributes
        for key in path.split("."):
            if isinstance(value, list) and key.isdigit() and int(key) < len(value):
                value = value[int(key)]
            elif isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return deepcopy(value)

    def has(self, path: str) -> bool:
        missing = object()
        return self.get(path, missing) is not missing

    def amount(self, path: str) -> Decimal | None:
        value = self.get(path)
        try:
            number = Decimal(str(value))
            return number if number.is_finite() else None
        except (InvalidOperation, ValueError):
            return None

    def date(self, path: str) -> datetime | None:
        value = self.get(path)
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else None
        except ValueError:
            return None

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self._attributes)

    def many(self, path: str, cls: type[T]) -> list[T]:
        rows = self.get(path)
        return [cls(row) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def one(self, path: str, cls: type[T]) -> T | None:
        row = self.get(path)
        return cls(row) if isinstance(row, dict) else None


class Invoice(Entity):
    effective_target: str
    id: str
    type: str
    status: str
    invoice_number: str | None
    series_id: str | None
    order_id: str | None
    order_installment_index: int | None
    corrected_invoice_id: str | None
    is_correction: bool
    buyer_contractor_id: str | None
    issue_date: str
    sales_date: str | None
    payment_method: str | None
    payment_date: str | None
    currency: str
    amount_entry_mode: str
    fx_rate: str | None
    fx_rate_date: str | None
    split_payment: bool
    cash_method: bool
    bank_number: str | None
    bank_name: str | None
    bank_swift: str | None
    notes: str | None
    additional_description: str | None
    totals: dict[str, Any]
    economic: dict[str, Any] | None
    correction: dict[str, Any] | None
    corrections: list[dict[str, Any]] | None
    ksef: dict[str, Any]
    paid_at: str | None
    payment_status: str
    paid_amount: str
    cancelled_at: str | None
    cancellation_reason: str | None
    public_url: str | None
    email_delivery: dict[str, Any]
    third_parties: list[dict[str, Any]] | None
    margin: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None

    def is_draft(self) -> bool:
        return self.status == "draft"

    def type_enum(self) -> InvoiceType | None:
        try:
            return InvoiceType(self.type)
        except ValueError:
            return None

    def status_enum(self) -> InvoiceStatus | None:
        try:
            return InvoiceStatus(self.status)
        except ValueError:
            return None

    def is_paid(self) -> bool:
        return self.paid_at is not None

    def gross_total(self) -> Decimal | None:
        return self.amount("totals.gross")

    def remaining_amount(self) -> Decimal | None:
        return self.amount("remaining_amount")

    def ksef_number(self) -> str | None:
        return self.get("ksef.number")

    def items(self) -> list[InvoiceItem]:
        return self.many("items", InvoiceItem)

    def payments(self) -> list[InvoicePayment]:
        return self.many("payments", InvoicePayment)

    def seller(self) -> InvoiceParty | None:
        return self.one("seller", InvoiceParty)

    def buyer(self) -> InvoiceParty | None:
        return self.one("buyer", InvoiceParty)


class Order(Entity):
    source: str
    external_id: str | None
    display_number: str
    integration_warnings: list[str]
    buyer: dict[str, Any]
    items: list[dict[str, Any]] | None
    id: str
    order_number: str | None
    status: str
    corrects_order_id: str | None
    correction_invoice_id: str | None
    currency: str
    order_date: str
    notes: str | None
    totals: dict[str, Any]
    buyer: dict[str, Any]
    items: list[dict[str, Any]] | None
    is_fully_invoiced: bool
    payment_schedule: list[dict[str, Any]] | None
    next_installment_index: int | None
    invoices: list[dict[str, Any]] | None
    email_delivery: dict[str, Any]
    created_at: str | None
    updated_at: str | None

    def is_correcting(self) -> bool:
        return self.corrects_order_id is not None

    def status_enum(self) -> OrderStatus | None:
        try:
            return OrderStatus(self.status)
        except ValueError:
            return None

    def gross_total(self) -> Decimal | None:
        return self.amount("totals.gross")


class IncomingInvoice(Entity):
    id: str
    source: str
    duplicate_group_id: str | None
    ksef_number: str | None
    external_id: str | None
    invoice_number: str | None
    seller_nip: str | None
    seller_name: str | None
    issue_date: str | None
    net_amount: str | None
    vat_amount: str | None
    gross_amount: str | None
    currency: str
    status: str
    extraction_status: str | None
    cost_category: str | None
    project: str | None
    notes: str | None
    accepted_at: str | None
    accepted_via: str | None
    rejection_reason: str | None
    in_approval_flow: bool
    approval: dict[str, Any] | None
    clarification_open: dict[str, Any] | None
    duplicate_suspicion: dict[str, Any] | None
    ksef_acquired_at: str | None
    paid_at: str | None
    paid_amount: str | None
    payment_due_date: str | None
    seller_bank_account: str | None
    split_payment: bool
    has_xml: bool
    has_file: bool
    lines: list[dict[str, Any]] | None
    created_at: str | None
    updated_at: str | None

    def is_pending(self) -> bool:
        return self.status == "pending"

    def status_enum(self) -> IncomingInvoiceStatus | None:
        try:
            return IncomingInvoiceStatus(self.status)
        except ValueError:
            return None

    def is_in_approval_flow(self) -> bool:
        value = self.get("approval.in_flow")
        return bool(self.in_approval_flow if value is None else value)


class KsefStatus(Entity):
    is_ksef: bool
    sent: bool
    ksef_number: str | None
    ksef_offline: bool
    ksef_date: str | None
    has_unresolved_errors: bool
    errors: list[dict[str, Any]]
    upo: dict[str, Any] | None

    def is_assigned(self) -> bool:
        return isinstance(self.ksef_number, str) and bool(self.ksef_number)

    def is_pending(self) -> bool:
        return bool(self.sent) and not self.is_assigned() and not self.has_errors()

    def has_errors(self) -> bool:
        return bool(self.has_unresolved_errors)


class Contractor(Entity):
    id: str
    type: str
    name: str
    email: str | None
    phone: str | None
    tax_registration_type: str | None
    tax_number: str | None
    tax_country_code: str
    client_number: str | None
    price_group_id: str | None
    addresses: list[dict[str, Any]] | None
    payment_score: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None

    def registered_address(self) -> dict[str, Any] | None:
        return next((row for row in self.addresses or [] if row.get("type") == "registered"), None)

    def correspondence_address(self) -> dict[str, Any] | None:
        return next((row for row in self.addresses or [] if row.get("type") == "correspondence"), None)


class ContractorInsights(Entity):
    stats: dict[str, Any]
    payment_score: dict[str, Any]

    def score(self) -> int | None:
        score = self.amount("payment_score.score")
        return int(score) if score is not None else None


class Product(Entity):
    id: str
    kind: str
    name: str
    description: str | None
    units: str | None
    sku: str | None
    ean: str | None
    unit_price: str | None
    unit_price_gross: str | None
    vat_type: str | None
    pkwiu: str | None
    track_stock: bool
    low_stock_threshold: str | None
    is_active: bool
    packagings: list[dict[str, Any]] | None
    created_at: str | None
    updated_at: str | None

    def is_service(self) -> bool:
        return self.kind == "service"


class InvoiceItem(Entity):
    line_number: int
    name: str
    quantity: str
    units: str | None
    unit_price: str | None
    unit_price_gross: str | None
    vat_type: str
    product_id: str | None
    net_amount: str
    vat_amount: str
    gross_amount: str
    unit_cost: str | None
    is_before_correction: bool

    pass


class InvoicePayment(Entity):
    id: str
    paid_at: str
    payment_method: str | None
    note: str | None
    created_at: str | None

    pass


class InvoiceParty(Entity):
    address_line_1: str | None
    address_line_2: str | None
    role: str
    name: str | None
    tax_type: str | None
    tax_number: str | None
    tax_country: str | None
    country_code: str | None
    email: str | None
    phone: str | None
    client_number: str | None

    pass


class BankAccount(Entity):
    id: str
    owner_name: str | None
    iban: str
    formatted_iban: str
    bank_name: str | None
    bic: str | None
    currency: str
    label: str | None
    is_default: bool
    active: bool

    pass


class EmailDelivery(Entity):
    sent_to: str | None
    sent_at: str
    public_url: str

    pass


class ExchangeRate(Entity):
    currency: str
    rate: str | float
    rate_date: str
    source: str | None

    pass


class InvoiceSerie(Entity):
    id: str
    type: str
    code: str
    name: str
    pattern: str | None
    is_default: bool
    is_active: bool

    pass


class PriceGroup(Entity):
    id: str
    name: str
    pricing: str
    markup_percent: float | int | None
    margin_percent: float | int | None

    pass


class ProductPrice(Entity):
    product_id: str
    unit_price: float | int
    unit_price_gross: float | int
    vat_type: str
    source: str
    wdt: bool

    pass


class ProfitabilityReport(Entity):
    meta: dict[str, Any]
    summary: dict[str, Any]
    groups: list[dict[str, Any]]
    below_cost: list[dict[str, Any]]

    pass


class PublicLink(Entity):
    public_url: str
    first_viewed_at: str | None

    pass


class StockLevel(Entity):
    id: str
    warehouse_id: str
    warehouse: str | None
    product_id: str
    product: str | None
    sku: str | None
    ean: str | None
    location: str | None
    quantity: float | int
    reserved: float | int
    available: float | int
    value: float | int
    updated_at: str | None

    pass


class StockMovement(Entity):
    id: str
    type: str
    movement_date: str | None
    product_id: str
    product: str | None
    warehouse_id: str | None
    location: str | None
    quantity: float | int
    quantity_after: float | int
    unit_cost: float | int | None
    note: str | None
    created_at: str | None

    pass


class StockMovementReceipt(Entity):
    id: str
    type: str
    quantity_after: float | int | None
    replayed: bool

    pass


class WarehouseInfo(Entity):
    id: str
    name: str
    symbol: str | None
    is_default: bool
    is_active: bool

    pass


@dataclass(frozen=True)
class OrderActionResult:
    order: Order
    meta: dict[str, Any]

    def get_meta(self, key: str, default: Any = None) -> Any:
        return self.meta.get(key, default)

    def overpayment_gross(self) -> Decimal | None:
        return Entity(self.meta).amount("overpayment_gross")


@dataclass(frozen=True)
class VoteResult:
    applied: bool
    reason: str | None
    invoice: IncomingInvoice
