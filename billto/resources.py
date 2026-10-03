from __future__ import annotations

import math
import re
import time
from copy import deepcopy
from decimal import Decimal
from datetime import date, datetime
from typing import Any, Callable, Generic, TypeVar

from . import entities as E
from .core import ApiResponse, BinaryFile, Transport, UnexpectedResponseError, compact, date_string, iso_string, segment
from .pagination import Page, Paginator

T = TypeVar("T", bound=E.Entity)
Payload = dict[str, Any]


class Resource:
    def __init__(self, transport: Transport):
        self.transport = transport

    def entity(self, response: ApiResponse, cls: type[T]) -> T:
        return cls(response.data_object())

    def entities(self, response: ApiResponse, cls: type[T]) -> list[T]:
        return [cls(row) for row in response.data_list()]

    def page(self, path: str, filters: Payload | None, cls: type[T], page: int = 1, per_page: int = 15) -> Page[T]:
        if type(page) is not int or page < 1 or type(per_page) is not int or per_page < 1:
            raise ValueError("Page and per_page must be positive integers.")
        return Page.from_response(self.transport.get(path, {**(filters or {}), "page": page, "per_page": per_page}), cls)

    def paginate(self, path: str, filters: Payload | None, cls: type[T], per_page: int = 100) -> Paginator[T]:
        snapshot = deepcopy(filters)
        return Paginator(lambda page: self.page(path, snapshot, cls, page, per_page))


class ReadableResource(Resource, Generic[T]):
    path: str
    entity_type: type[T]

    def list(self, filters: Payload | None = None, page: int = 1, per_page: int = 15) -> Page[T]:
        return self.page(self.path, filters, self.entity_type, page, per_page)

    def all(self, filters: Payload | None = None, per_page: int = 100) -> Paginator[T]:
        return self.paginate(self.path, filters, self.entity_type, per_page)

    def get(self, resource_id: str) -> T:
        return self.entity(self.transport.get(f"{self.path}/{segment(resource_id)}"), self.entity_type)


class CrudResource(ReadableResource[T]):
    def create(self, data: Payload, idempotency_key: str | None = None) -> T:
        return self.entity(self.transport.post(self.path, data, idempotency_key), self.entity_type)

    def update(self, resource_id: str, data: Payload, idempotency_key: str | None = None) -> T:
        return self.entity(self.transport.put(f"{self.path}/{segment(resource_id)}", data, idempotency_key), self.entity_type)

    def delete(self, resource_id: str) -> None:
        self.transport.delete(f"{self.path}/{segment(resource_id)}")


class Invoices(CrudResource[E.Invoice]):
    path, entity_type = "invoices", E.Invoice

    def issue(self, invoice_id: str, idempotency_key: str | None = None) -> E.Invoice:
        return self.entity(self.transport.post(f"invoices/{segment(invoice_id)}/issue", key=idempotency_key), E.Invoice)

    def send_to_ksef(self, invoice_id: str, idempotency_key: str | None = None) -> E.KsefStatus:
        return self.entity(self.transport.post(f"invoices/{segment(invoice_id)}/ksef", key=idempotency_key), E.KsefStatus)

    def ksef_status(self, invoice_id: str) -> E.KsefStatus:
        return self.entity(self.transport.get(f"invoices/{segment(invoice_id)}/ksef"), E.KsefStatus)

    def wait_for_ksef(self, invoice_id: str, timeout_seconds: float = 120, interval_seconds: float = 5,
                      *, sleeper: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> E.KsefStatus:
        """Return terminal or last status. An in-flight request uses the HTTP timeout."""
        if not math.isfinite(timeout_seconds) or timeout_seconds < 0 or not math.isfinite(interval_seconds) or interval_seconds <= 0:
            raise ValueError("Invalid polling timeout or interval.")
        deadline = clock() + timeout_seconds
        while True:
            status = self.ksef_status(invoice_id)
            remaining = deadline - clock()
            if status.is_assigned() or status.has_errors() or remaining <= 0:
                return status
            sleeper(min(interval_seconds, remaining))
            if clock() >= deadline:
                return status

    def pdf(self, invoice_id: str) -> BinaryFile:
        return self.transport.download(f"invoices/{segment(invoice_id)}/pdf", fallback=f"{invoice_id}.pdf")

    def xml(self, invoice_id: str, version: int | None = None) -> BinaryFile:
        return self.transport.download(f"invoices/{segment(invoice_id)}/xml", compact({"version": version}), f"{invoice_id}.xml")

    def send_email(self, invoice_id: str, email: str | None = None, idempotency_key: str | None = None) -> E.EmailDelivery:
        return self.entity(self.transport.post(f"invoices/{segment(invoice_id)}/send-email", compact({"email": email}), idempotency_key), E.EmailDelivery)

    def public_link(self, invoice_id: str, idempotency_key: str | None = None) -> E.PublicLink:
        return self.entity(self.transport.post(f"invoices/{segment(invoice_id)}/public-link", key=idempotency_key), E.PublicLink)

    def record_payment(self, invoice_id: str, amount: float | str | Decimal, *, paid_at: date | str | None = None,
                       payment_method: str | None = None, note: str | None = None, idempotency_key: str | None = None) -> E.Invoice:
        payload = compact({"amount": amount, "paid_at": date_string(paid_at), "payment_method": payment_method, "note": note})
        return self.entity(self.transport.post(f"invoices/{segment(invoice_id)}/payments", payload, idempotency_key), E.Invoice)

    def mark_paid(self, invoice_id: str, *, paid_at: date | str | None = None, payment_method: str | None = None,
                  idempotency_key: str | None = None) -> E.Invoice:
        payload = compact({"paid_at": date_string(paid_at), "payment_method": payment_method})
        return self.entity(self.transport.post(f"invoices/{segment(invoice_id)}/mark-paid", payload, idempotency_key), E.Invoice)

    def delete_payment(self, invoice_id: str, payment_id: str) -> None:
        self.transport.delete(f"invoices/{segment(invoice_id)}/payments/{segment(payment_id)}")


class Orders(CrudResource[E.Order]):
    path, entity_type = "orders", E.Order

    def confirm(self, order_id: str, idempotency_key: str | None = None) -> E.Order:
        return self.entity(self.transport.post(f"orders/{segment(order_id)}/confirm", key=idempotency_key), E.Order)

    def _action(self, response: ApiResponse) -> E.OrderActionResult:
        return E.OrderActionResult(E.Order(response.data_object()), response.meta())

    def close(self, order_id: str, idempotency_key: str | None = None) -> E.OrderActionResult:
        return self._action(self.transport.post(f"orders/{segment(order_id)}/close", key=idempotency_key))

    def cancel(self, order_id: str, idempotency_key: str | None = None) -> E.OrderActionResult:
        return self._action(self.transport.post(f"orders/{segment(order_id)}/cancel", key=idempotency_key))

    def send_confirmation(self, order_id: str, email: str | None = None, idempotency_key: str | None = None) -> E.Order:
        return self.entity(self.transport.post(f"orders/{segment(order_id)}/send-confirmation", compact({"email": email}), idempotency_key), E.Order)

    def pdf(self, order_id: str) -> BinaryFile:
        return self.transport.download(f"orders/{segment(order_id)}/pdf", fallback=f"{order_id}.pdf")

    def issue_advance(self, order_id: str, options: Payload | None = None, idempotency_key: str | None = None) -> E.Invoice:
        return self.entity(self.transport.post(f"orders/{segment(order_id)}/issue-advance", options, idempotency_key), E.Invoice)

    def mark_paid(self, order_id: str, *, idempotency_key: str | None = None, send_email: bool = True,
                  series_id: str | None = None, invoice_type: str | None = None, oss_vat_type: float | str | Decimal | None = None, mark_paid: bool = True) -> E.Invoice:
        payload = compact({"send_email": None if send_email else False, "mark_paid": None if mark_paid else False,
                           "series_id": series_id, "invoice_type": invoice_type, "oss_vat_type": oss_vat_type})
        return self.entity(self.transport.post(f"orders/{segment(order_id)}/mark-paid", payload, idempotency_key), E.Invoice)

    def issue_correction(self, order_id: str, lines: list[Payload], reason: str | None = None,
                         idempotency_key: str | None = None) -> E.OrderActionResult:
        return self._action(self.transport.post(f"orders/{segment(order_id)}/issue-correction", compact({"lines": lines, "reason": reason}), idempotency_key))

    def issue_kor(self, order_id: str, *, series_id: str | None = None, idempotency_key: str | None = None, send_email: bool = True) -> E.Invoice:
        payload = compact({"series_id": series_id, "send_email": None if send_email else False})
        return self.entity(self.transport.post(f"orders/{segment(order_id)}/issue-kor", payload, idempotency_key), E.Invoice)


class Contractors(CrudResource[E.Contractor]):
    path, entity_type = "contractors", E.Contractor

    def find_by_tax_number(self, tax_number: str) -> E.Contractor | None:
        normalized = re.sub(r"[\s-]", "", tax_number)
        return next((item for item in self.all({"search": normalized}, 50) if item.tax_number == normalized), None)

    def insights(self, contractor_id: str) -> E.ContractorInsights:
        return self.entity(self.transport.get(f"contractors/{segment(contractor_id)}/insights"), E.ContractorInsights)


class Products(CrudResource[E.Product]):
    path, entity_type = "products", E.Product

    def price(self, product_id: str, contractor_id: str | None = None, price_group_id: str | None = None) -> E.ProductPrice:
        return self.entity(self.transport.get(f"products/{segment(product_id)}/price", compact({"contractor_id": contractor_id, "price_group_id": price_group_id})), E.ProductPrice)


class PriceGroups(Resource):
    def list(self) -> list[E.PriceGroup]:
        return self.entities(self.transport.get("price-groups"), E.PriceGroup)


class IncomingInvoices(ReadableResource[E.IncomingInvoice]):
    path, entity_type = "incoming-invoices", E.IncomingInvoice

    def list(self, filters: Payload | None = None, page: int = 1, per_page: int = 50) -> Page[E.IncomingInvoice]:
        return super().list(filters, page, per_page)

    def all(self, filters: Payload | None = None, per_page: int = 200) -> Paginator[E.IncomingInvoice]:
        return super().all(filters, per_page)

    def updated_since(self, since: datetime | str, status: str | None = None, per_page: int = 200) -> Paginator[E.IncomingInvoice]:
        return self.all(compact({"updated_since": iso_string(since), "status": status}), per_page)

    def xml(self, invoice_id: str) -> BinaryFile:
        return self.transport.download(f"incoming-invoices/{segment(invoice_id)}/xml", fallback=f"{invoice_id}.xml")

    def accept(self, invoice_id: str, idempotency_key: str | None = None) -> E.IncomingInvoice:
        return self.entity(self.transport.post(f"incoming-invoices/{segment(invoice_id)}/accept", key=idempotency_key), E.IncomingInvoice)

    def reject(self, invoice_id: str, reason: str | None = None, idempotency_key: str | None = None) -> E.IncomingInvoice:
        return self.entity(self.transport.post(f"incoming-invoices/{segment(invoice_id)}/reject", compact({"reason": reason}), idempotency_key), E.IncomingInvoice)

    def vote(self, invoice_id: str, decision: str, email: str, *, name: str | None = None, decided_at: datetime | str | None = None,
             ksef_number: str | None = None, idempotency_key: str | None = None) -> E.VoteResult:
        payload = compact({"decision": decision, "email": email, "name": name, "decided_at": iso_string(decided_at), "ksef_number": ksef_number})
        response = self.transport.post(f"incoming-invoices/{segment(invoice_id)}/vote", payload, idempotency_key)
        invoice = E.IncomingInvoice(response.data_object())
        data = response.json() or {}
        return E.VoteResult(bool(data.get("applied")), data.get("reason"), invoice)


class Warehouse(Resource):
    def warehouses(self) -> list[E.WarehouseInfo]:
        return self.entities(self.transport.get("warehouse/warehouses"), E.WarehouseInfo)

    def stocks(self, filters: Payload | None = None, page: int = 1, per_page: int = 15) -> Page[E.StockLevel]:
        return self.page("warehouse/stocks", filters, E.StockLevel, page, per_page)

    def all_stocks(self, filters: Payload | None = None, per_page: int = 100) -> Paginator[E.StockLevel]:
        return self.paginate("warehouse/stocks", filters, E.StockLevel, per_page)

    def movements(self, filters: Payload | None = None, page: int = 1, per_page: int = 15) -> Page[E.StockMovement]:
        return self.page("warehouse/movements", filters, E.StockMovement, page, per_page)

    def all_movements(self, filters: Payload | None = None, per_page: int = 100) -> Paginator[E.StockMovement]:
        return self.paginate("warehouse/movements", filters, E.StockMovement, per_page)

    def move(self, movement_type: str, quantity: float | str | Decimal, options: Payload | None = None, idempotency_key: str | None = None) -> E.StockMovementReceipt:
        return self.entity(self.transport.post("warehouse/movements", {**(options or {}), "type": movement_type, "quantity": quantity}, idempotency_key), E.StockMovementReceipt)

    def receive(self, quantity: float | str | Decimal, options: Payload | None = None, idempotency_key: str | None = None) -> E.StockMovementReceipt:
        return self.move("pz", quantity, options, idempotency_key)

    def issue(self, quantity: float | str | Decimal, options: Payload | None = None, idempotency_key: str | None = None) -> E.StockMovementReceipt:
        return self.move("wz", quantity, options, idempotency_key)


class Reports(Resource):
    def profitability(self, date_from: date | str | None = None, date_to: date | str | None = None, group_by: str | None = None) -> E.ProfitabilityReport:
        response = self.transport.get("reports/profitability", compact({"from": date_string(date_from), "to": date_string(date_to), "group_by": group_by}))
        data = response.json()
        if not data or not isinstance(data.get("summary"), dict):
            raise UnexpectedResponseError("Expected a profitability report.", response)
        return E.ProfitabilityReport(data)


class InvoiceSeries(Resource):
    def list(self, invoice_type: str | None = None) -> list[E.InvoiceSerie]:
        return self.entities(self.transport.get("invoice-series", compact({"type": invoice_type})), E.InvoiceSerie)

    def default(self, invoice_type: str = "VAT") -> E.InvoiceSerie | None:
        return next((item for item in self.list(invoice_type) if item.is_default), None)


class BankAccounts(Resource):
    def list(self, active_only: bool = True) -> list[E.BankAccount]:
        return self.entities(self.transport.get("bank-accounts", {"active_only": active_only}), E.BankAccount)

    def default(self) -> E.BankAccount | None:
        return next((item for item in self.list() if item.is_default), None)


class ExchangeRates(Resource):
    def list(self, currency: str | None = None, date: date | str | None = None,
             date_from: date | str | None = None, date_to: date | str | None = None) -> list[E.ExchangeRate]:
        query = compact({"currency": currency.upper() if currency else currency, "date": date_string(date),
                         "date_from": date_string(date_from), "date_to": date_string(date_to)})
        return self.entities(self.transport.get("exchange-rates", query), E.ExchangeRate)

    def rate(self, currency: str, date: date | str | None = None) -> E.ExchangeRate | None:
        return next(iter(self.list(currency, date)), None)
