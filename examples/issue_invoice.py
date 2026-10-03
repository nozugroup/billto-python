"""Install with `python -m pip install .`, then run this file with BILLTO_TOKEN set.

Creates an invoice in the sandbox and downloads its PDF.
"""
import os
from datetime import date
from billto import BillTo, InvoiceType, PaymentMethod, VatRate, ValidationError


def main() -> None:
    client = BillTo.sandbox(os.environ['BILLTO_TOKEN'])
    series = client.invoice_series.default(InvoiceType.VAT)
    if series is None:
        raise RuntimeError('Configure a default VAT series in the sandbox first.')
    try:
        invoice = client.invoices.create({
            'type': InvoiceType.VAT,
            'series_id': series.id,
            'issue': True,
            'issue_date': date.today().isoformat(),
            'payment_method': PaymentMethod.TRANSFER,
            'currency': 'PLN',
            'amount_entry_mode': 'net',
            'buyer': {'name': 'ACME sp. z o.o.', 'tax_type': 'local', 'tax_number': '5261040828'},
            'items': [{'name': 'Usługa konsultingowa', 'quantity': 10, 'unit_price': '150.00', 'vat_type': VatRate.RATE23}],
        })
        print(invoice.invoice_number, invoice.get('totals.gross'))
        client.invoices.pdf(invoice.id).save_to('invoice.pdf')
    except ValidationError as error:
        print(error.errors)
        raise


if __name__ == '__main__':
    main()
