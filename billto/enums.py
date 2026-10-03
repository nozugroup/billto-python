from enum import Enum


class ContractorType(str, Enum):
    OWN = 'own'
    EXTERNAL = 'external'

class IncomingInvoiceStatus(str, Enum):
    PENDING = 'pending'
    ACCEPTED = 'accepted'
    REJECTED = 'rejected'

class InvoiceStatus(str, Enum):
    DRAFT = 'draft'
    ISSUED = 'issued'
    KSEF_ISSUED_OFFLINE = 'ksef_issued_offline'
    KSEF_ISSUED = 'ksef_issued'
    CANCELLED = 'cancelled'

    def is_issued(self) -> bool:
        return self not in (self.DRAFT, self.CANCELLED)

class InvoiceType(str, Enum):
    VAT = 'VAT'
    KOR = 'KOR'
    ZAL = 'ZAL'
    ROZ = 'ROZ'
    KOR_ZAL = 'KOR_ZAL'
    KOR_ROZ = 'KOR_ROZ'
    OSS = 'OSS'
    KOR_OSS = 'KOR_OSS'

    def is_correction(self) -> bool:
        return self.value.startswith("KOR")

class OrderStatus(str, Enum):
    DRAFT = 'draft'
    CONFIRMED = 'confirmed'
    CLOSED = 'closed'
    CANCELLED = 'cancelled'

class PaymentMethod(str, Enum):
    TRANSFER = 'transfer'
    CASH = 'cash'
    CARD = 'card'
    COMPENSATION = 'compensation'
    OTHER = 'other'

class Scope(str, Enum):
    INVOICES_READ = 'invoices:read'
    INVOICES_WRITE = 'invoices:write'
    ORDERS_READ = 'orders:read'
    ORDERS_WRITE = 'orders:write'
    KSEF_SEND = 'ksef:send'
    CONTRACTORS_READ = 'contractors:read'
    CONTRACTORS_WRITE = 'contractors:write'
    PRODUCTS_READ = 'products:read'
    PRODUCTS_WRITE = 'products:write'
    INCOMING_READ = 'incoming:read'
    INCOMING_WRITE = 'incoming:write'
    INCOMING_VOTE = 'incoming:vote'
    WAREHOUSE_READ = 'warehouse:read'
    WAREHOUSE_WRITE = 'warehouse:write'

class StockMovementType(str, Enum):
    PZ = 'pz'
    WZ = 'wz'
    PW = 'pw'
    RW = 'rw'

class TaxType(str, Enum):
    LOCAL = 'local'
    EU = 'eu'
    NON_EU = 'noneu'
    NONE = 'none'

class VatRate(str, Enum):
    RATE23 = '23'
    RATE22 = '22'
    RATE8 = '8'
    RATE7 = '7'
    RATE5 = '5'
    RATE4 = '4'
    RATE3 = '3'
    ZERO_DOMESTIC = '0 KR'
    ZERO_WDT = '0 WDT'
    ZERO_EXPORT = '0 EX'
    EXEMPT = 'zw'
    REVERSE_CHARGE = 'oo'
    NOT_SUBJECT_I = 'np I'
    NOT_SUBJECT_II = 'np II'

    def percent(self) -> float | None:
        return float(self.value) if self.value.isdigit() else 0.0 if self.value.startswith("0 ") else None
