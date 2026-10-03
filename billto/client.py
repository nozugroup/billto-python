from __future__ import annotations
from typing import Any
from .core import Config, SANDBOX_BASE_URL, Transport
from . import resources


class BillTo:
    def __init__(self, token_or_config: str | Config, **transport_options: Any):
        self.config = token_or_config if isinstance(token_or_config, Config) else Config(token_or_config)
        self.transport = Transport(self.config, **transport_options)
        self.invoices = resources.Invoices(self.transport)
        self.orders = resources.Orders(self.transport)
        self.contractors = resources.Contractors(self.transport)
        self.products = resources.Products(self.transport)
        self.price_groups = resources.PriceGroups(self.transport)
        self.incoming_invoices = resources.IncomingInvoices(self.transport)
        self.warehouse = resources.Warehouse(self.transport)
        self.reports = resources.Reports(self.transport)
        self.invoice_series = resources.InvoiceSeries(self.transport)
        self.bank_accounts = resources.BankAccounts(self.transport)
        self.exchange_rates = resources.ExchangeRates(self.transport)

    @classmethod
    def create(cls, token: str, **transport_options: Any) -> BillTo:
        return cls(token, **transport_options)

    @classmethod
    def from_config(cls, config: Config, **transport_options: Any) -> BillTo:
        return cls(config, **transport_options)

    @classmethod
    def sandbox(cls, token: str, **transport_options: Any) -> BillTo:
        return cls(Config(token, base_url=SANDBOX_BASE_URL), **transport_options)
