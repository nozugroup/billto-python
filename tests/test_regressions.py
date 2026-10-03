import json
import unittest
from decimal import Decimal
from typing import get_type_hints, get_args
import billto as sdk


def response(data, status=200, trace=None):
    return sdk.ApiResponse(status, json.dumps(data).encode(), {'X-Trace-Id': trace} if trace else {})


def fake(queue, config=None, **options):
    requests = []
    def sender(request, timeout):
        requests.append(request)
        return queue.pop(0)
    return sdk.BillTo(config or sdk.Config('test'), sender=sender, sleeper=lambda _: None, **options), requests


class RegressionTests(unittest.TestCase):
    def test_decimal_roundtrip_preserves_precision_and_nested_payloads(self):
        amount = '12345678901234567890.12345678901234567890'
        client, requests = fake([response({'data': {}}) for _ in range(3)])
        invoice = sdk.Invoice({'totals': {'gross': amount}})
        client.invoices.record_payment('i', invoice.gross_total())
        client.invoices.create({'items': [{'unit_price': Decimal(amount)}], 'empty': []})
        client.warehouse.receive(Decimal('0.12345678901234567890'))
        self.assertEqual(json.loads(requests[0].data)['amount'], amount)
        self.assertEqual(json.loads(requests[1].data)['items'][0]['unit_price'], amount)
        self.assertEqual(json.loads(requests[1].data)['empty'], [])
        self.assertEqual(json.loads(requests[2].data)['quantity'], '0.12345678901234567890')

    def test_nonfinite_decimal_is_rejected_before_http(self):
        client, requests = fake([])
        for value in ('NaN', 'sNaN', 'Infinity', '-Infinity'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'finite'):
                client.invoices.create({'items': [{'unit_price': Decimal(value)}]})
        self.assertEqual(requests, [])

    def test_response_observer_sees_retries_success_download_and_error(self):
        seen = []
        config = sdk.Config('t').with_correlation_id('job-123').with_max_retries(1).identify('app', '1', 'a@example.com')
        client, requests = fake([
            response({}, 503, 'retry'), response({'data': {'id': 'i'}}, trace='ok'),
            sdk.ApiResponse(200, b'%PDF', {'X-Trace-Id': 'pdf'}), response({}, 422, 'error'),
        ], config, on_response=lambda r: seen.append(r.header('X-Trace-Id')))
        self.assertEqual(client.invoices.get('i').id, 'i')
        self.assertEqual(client.invoices.pdf('i').content, b'%PDF')
        with self.assertRaises(sdk.ValidationError):
            client.invoices.create({})
        self.assertEqual(seen, ['retry', 'ok', 'pdf', 'error'])
        self.assertTrue(all(r.get_header('X-correlation-id') == 'job-123' for r in requests))
        self.assertIsNone(config.with_correlation_id(None).correlation_id)

    def test_observer_exception_never_retries_successful_mutation(self):
        def fail(response):
            raise OSError('logger failed')
        client, requests = fake([response({'data': {}})], on_response=fail)
        with self.assertRaisesRegex(OSError, 'logger failed'):
            client.invoices.create({})
        self.assertEqual(len(requests), 1)

    def test_correlation_validation_and_default(self):
        for value in ('', ' ', 'a\r\nb', 'ą', 42):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'X-Correlation-Id'):
                sdk.Config('t', correlation_id=value)
        client, requests = fake([response({'data': {}})])
        client.invoices.get('i')
        self.assertIsNone(requests[0].get_header('X-correlation-id'))

    def test_entity_attributes_and_nullable_annotations(self):
        order = sdk.Order({'source': 'shop', 'external_id': None, 'display_number': '#1', 'integration_warnings': ['missing_email'], 'buyer': {}})
        self.assertEqual(order.source, 'shop')
        self.assertEqual(order.integration_warnings, ['missing_email'])
        self.assertIsNone(order.items)
        invoice = sdk.Invoice({'effective_target': 'ksef', 'created_at': None})
        self.assertEqual(invoice.effective_target, 'ksef')
        self.assertEqual(invoice.items(), [])
        self.assertIsNone(invoice.buyer())
        party = sdk.InvoiceParty({'address_line_1': 'Łódź', 'address_line_2': None})
        self.assertEqual(party.address_line_1, 'Łódź')
        for cls, fields in [(sdk.Order, ['items', 'invoices', 'created_at']), (sdk.Invoice, ['corrections', 'updated_at']), (sdk.Product, ['packagings']), (sdk.InvoiceSerie, ['pattern']), (sdk.EmailDelivery, ['sent_to'])]:
            for field in fields:
                self.assertIn(type(None), get_args(get_type_hints(cls)[field]))

    def test_numeric_and_decimal_oss_rates(self):
        client, requests = fake([response({'data': {}}), response({'data': {}})])
        client.orders.mark_paid('o', oss_vat_type=19.5)
        client.orders.mark_paid('o', oss_vat_type=Decimal('19.5'))
        self.assertEqual(json.loads(requests[0].data)['oss_vat_type'], 19.5)
        self.assertEqual(json.loads(requests[1].data)['oss_vat_type'], '19.5')
