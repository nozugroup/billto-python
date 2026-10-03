import hashlib
import json
import re
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import billto as sdk


def snake(value):
    return re.sub(r'(?<=[a-z0-9])([A-Z])', r'_\1', value).lower()


def response(data, status=200, headers=None):
    return sdk.ApiResponse(status, json.dumps(data, ensure_ascii=False).encode('utf-8'), headers or {})


def fake(queue, config=None):
    requests, delays = [], []

    def send(request, timeout):
        requests.append(request)
        if not queue:
            raise AssertionError('Unexpected extra HTTP request')
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client = sdk.BillTo(config or sdk.Config('test-token'), sender=send, sleeper=delays.append, key_generator=lambda: 'generated-key')
    return client, requests, delays


class ContractTests(unittest.TestCase):
    pass


def make_contract(scenario):
    def test(self):
        client, requests, _ = fake([response(scenario['response'], 204 if scenario['verb'] == 'DELETE' else 200)])
        method = getattr(getattr(client, snake(scenario['resource'])), snake(scenario['method']))
        result = method(*scenario['args'], **{snake(key): value for key, value in scenario.get('options', {}).items()})
        if scenario['collect']:
            result = result.collect()
        self.assertEqual(len(requests), 1)
        request = requests[0]
        url = urlsplit(request.full_url)
        self.assertEqual(request.method, scenario['verb'])
        self.assertEqual(url.path, '/api/v1/' + scenario['path'])
        self.assertEqual(dict(parse_qsl(url.query)), scenario['query'])
        self.assertEqual(json.loads(request.data) if request.data else None, scenario['body'])
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-token')
        if scenario['result']:
            self.assertIsInstance(result, getattr(sdk, scenario['result']))
        if 'event' in scenario['args'] or scenario.get('options', {}).get('idempotencyKey'):
            self.assertEqual(request.get_header('Idempotency-key'), 'event')
    return test


for contract in json.loads(Path(__file__).with_name('contracts.json').read_text(encoding='utf-8')):
    setattr(ContractTests, 'test_' + contract['name'].replace('.', '_'), make_contract(contract))


class BehaviorTests(unittest.TestCase):
    def test_retry_preserves_body_and_key(self):
        client, requests, delays = fake([response({}, 503), OSError('network'), response({'data': {'id': 'i'}})])
        client.invoices.create({'name': 'Zażółć'})
        self.assertEqual(len(requests), 3)
        self.assertEqual(len({item.data for item in requests}), 1)
        self.assertEqual({item.get_header('Idempotency-key') for item in requests}, {'generated-key'})
        self.assertEqual(len(delays), 2)
        self.assertNotEqual(sdk.IdempotencyKey.generate(), sdk.IdempotencyKey.generate())

    def test_unkeyed_mutations_never_retry(self):
        config = sdk.Config('test', auto_idempotency=False)
        client, requests, _ = fake([response({}, 503)], config)
        with self.assertRaises(sdk.ServerError):
            client.invoices.create({})
        self.assertEqual(len(requests), 1)
        client, requests, _ = fake([response({}, 503), response({'data': {}})], config)
        client.invoices.create({}, 'event')
        self.assertEqual(len(requests), 2)

    def test_throttling_and_plan_limit(self):
        client, _, delays = fake([response({}, 429, {'Retry-After': '100'}), response({'data': {}})])
        client.invoices.get('i')
        self.assertEqual(delays, [10])
        client, requests, _ = fake([response({'usage': {'used': 100, 'limit': 100}, 'upgrade_url': 'u'}, 429)])
        with self.assertRaises(sdk.RateLimitError) as caught:
            client.invoices.get('i')
        self.assertTrue(caught.exception.is_plan_limit())
        self.assertEqual(caught.exception.usage['used'], 100)
        self.assertEqual(caught.exception.upgrade_url, 'u')
        self.assertEqual(len(requests), 1)

    def test_retry_after_dates(self):
        future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=60))
        error = sdk.RateLimitError('wait', response({}, 429, {'Retry-After': future}))
        self.assertTrue(0 < error.retry_after <= 60)
        for value in ('bad', '-5'):
            self.assertIsNone(sdk.RateLimitError('wait', response({}, 429, {'Retry-After': value})).retry_after)

    def test_error_mapping_and_raw_body(self):
        for status, cls in [(400,sdk.ApiError),(401,sdk.AuthenticationError),(402,sdk.PaymentRequiredError),(403,sdk.ForbiddenError),
                            (404,sdk.NotFoundError),(409,sdk.ConflictError),(422,sdk.ValidationError),(429,sdk.RateLimitError),
                            (500,sdk.ServerError),(502,sdk.ServerError),(503,sdk.ServerError),(504,sdk.ServerError)]:
            with self.subTest(status=status):
                client, _, _ = fake([sdk.ApiResponse(status, b'not json')], sdk.Config('test', max_retries=0))
                with self.assertRaises(cls) as caught:
                    client.invoices.get('i')
                self.assertEqual(caught.exception.status, status)
                self.assertEqual(caught.exception.response.body, b'not json')

    def test_error_helpers(self):
        error = sdk.ValidationError('invalid', response({'errors': {'items.0.name': ['Required'], 'buyer': 'Missing'}}))
        self.assertTrue(error.has('buyer'))
        self.assertEqual(error.first('items.0.name'), 'Required')
        self.assertEqual(error.messages(), ['Required', 'Missing'])
        raw = response({'code': 'nip_conflict', 'upgrade_url': 'u', 'data': {'id': 'i'}})
        self.assertTrue(sdk.ForbiddenError('scope missing', raw).is_insufficient_scope())
        self.assertTrue(sdk.ForbiddenError('', raw).is_nip_conflict())
        self.assertEqual(sdk.PaymentRequiredError('', raw).upgrade_url(), 'u')
        self.assertEqual(sdk.ConflictError('', raw).data(), {'id': 'i'})

    def test_network_failure_retains_cause(self):
        cause = OSError('offline')
        client, requests, _ = fake([cause, cause, cause])
        with self.assertRaises(sdk.TransportError) as caught:
            client.invoices.get('i')
        self.assertIs(caught.exception.__cause__, cause)
        self.assertEqual(len(requests), 3)

    def test_malformed_success_and_redirect(self):
        for data in ({}, {'data': None}, {'data': []}, {'data': 'bad'}):
            client, _, _ = fake([response(data)])
            with self.assertRaises(sdk.UnexpectedResponseError):
                client.invoices.get('i')
        for data in ({'data': {}}, {'data': [3]}, {'data': [None]}):
            client, _, _ = fake([response(data)])
            with self.assertRaises(sdk.UnexpectedResponseError):
                client.invoices.list()
        client, _, _ = fake([response({}, 302, {'Location': 'https://elsewhere.test'})])
        with self.assertRaises(sdk.UnexpectedResponseError):
            client.invoices.get('i')

    def test_lazy_pagination_and_early_exit(self):
        client, requests, _ = fake([response({'data': [{'id': '1'}], 'meta': {'current_page': 1, 'last_page': 2}}),
                                    response({'data': [{'id': '2'}], 'meta': {'current_page': 2, 'last_page': 2}})])
        paginator = client.invoices.all({'page': 1, 'per_page': 1}, 5)
        self.assertEqual(len(requests), 0)
        self.assertEqual([item.id for item in paginator], ['1', '2'])
        self.assertEqual([dict(parse_qsl(urlsplit(item.full_url).query))['page'] for item in requests], ['1', '2'])
        client, requests, _ = fake([response({'data': [{'id': '1'}], 'meta': {'current_page': 1, 'last_page': 2}})])
        for item in client.invoices.all():
            self.assertEqual(item.id, '1')
            break
        self.assertEqual(len(requests), 1)

    def test_stuck_pagination(self):
        data = {'data': [], 'meta': {'current_page': 1, 'last_page': 3}}
        client, _, _ = fake([response(data), response(data)])
        with self.assertRaisesRegex(RuntimeError, 'did not advance'):
            client.invoices.all().collect()

    def test_queries_dates_enums_and_ids(self):
        client, requests, _ = fake([response({'data': []}), response({'data': {}})])
        client.products.list({'active_only': False, 'missing': None, 'tags': ['a', 'b'], 'since': datetime(2026, 9, 1, tzinfo=timezone.utc), 'status': sdk.InvoiceStatus.DRAFT})
        query = dict(parse_qsl(urlsplit(requests[0].full_url).query))
        self.assertEqual(query['active_only'], '0')
        self.assertEqual(query['tags[1]'], 'b')
        self.assertNotIn('missing', query)
        self.assertEqual(query['since'], '2026-09-01T00:00:00+00:00')
        self.assertEqual(query['status'], 'draft')
        client.invoices.get('a/b?#')
        self.assertEqual(urlsplit(requests[1].full_url).path, '/api/v1/invoices/a%2Fb%3F%23')
        with self.assertRaises(ValueError):
            client.invoices.get('..')

    def test_ksef_polling(self):
        for terminal in ({'ksef_number': 'K1'}, {'has_unresolved_errors': True}):
            client, _, _ = fake([response({'data': {'sent': True}}), response({'data': terminal})])
            now = [0]
            def sleep(delay):
                now[0] += delay
            status = client.invoices.wait_for_ksef('i', 10, 2, sleeper=sleep, clock=lambda: now[0])
            self.assertTrue(status.is_assigned() or status.has_errors())
            self.assertEqual(now[0], 2)
        client, requests, _ = fake([response({'data': {'sent': True}})])
        now = [0]
        status = client.invoices.wait_for_ksef('i', 1, sleeper=sleep, clock=lambda: now[0])
        self.assertTrue(status.is_pending())
        self.assertEqual(now[0], 1)
        self.assertEqual(len(requests), 1)

    def test_binary_downloads(self):
        for resource, method, args, expected in [('invoices','pdf',['i'],'invoices/i/pdf'),('invoices','xml',['i',2],'invoices/i/xml'),
                                                  ('orders','pdf',['o'],'orders/o/pdf'),('incoming_invoices','xml',['i'],'incoming-invoices/i/xml')]:
            raw = bytes([0, 255, 1, 2])
            client, requests, _ = fake([sdk.ApiResponse(200, raw, {'Content-Type': 'application/pdf', 'Content-Disposition': "attachment; filename*=UTF-8''..%2FZa%C5%BC%C3%B3%C5%82%C4%87.pdf"})])
            file = getattr(getattr(client, resource), method)(*args)
            self.assertEqual(file.content, raw)
            self.assertEqual(file.filename, 'Zażółć.pdf')
            self.assertEqual(requests[0].get_header('Accept'), '*/*')
            self.assertEqual(urlsplit(requests[0].full_url).path, '/api/v1/' + expected)
            if len(args) == 2:
                self.assertEqual(dict(parse_qsl(urlsplit(requests[0].full_url).query))['version'], '2')
            with tempfile.TemporaryDirectory(prefix='billto-python-test-') as directory:
                saved = file.save_in(directory)
                self.assertEqual(saved.parent, Path(directory))
                self.assertEqual(saved.read_bytes(), raw)

    def test_entities_and_decimal_amounts(self):
        invoice = sdk.Invoice({'id': 'i', 'status': 'draft', 'paid_at': None, 'totals': {'gross': '123.45'}, 'remaining_amount': '23.45',
                              'items': [{'name': 'X'}], 'payments': [{'amount': '1.00'}], 'buyer': {'name': 'ACME'}, 'ksef': {'number': 'K'}})
        self.assertTrue(invoice.is_draft())
        self.assertEqual(invoice.status_enum(), sdk.InvoiceStatus.DRAFT)
        self.assertIsNone(invoice.type_enum())
        self.assertEqual(sdk.Invoice({'type': 'VAT'}).type_enum(), sdk.InvoiceType.VAT)
        self.assertEqual(sdk.Order({'status': 'confirmed'}).status_enum(), sdk.OrderStatus.CONFIRMED)
        self.assertEqual(sdk.IncomingInvoice({'status': 'pending'}).status_enum(), sdk.IncomingInvoiceStatus.PENDING)
        self.assertFalse(invoice.is_paid())
        self.assertEqual(invoice.gross_total(), Decimal('123.45'))
        self.assertEqual(invoice.remaining_amount(), Decimal('23.45'))
        self.assertEqual(invoice.items()[0].name, 'X')
        self.assertEqual(invoice.payments()[0]['amount'], '1.00')
        self.assertEqual(invoice.buyer().name, 'ACME')
        self.assertEqual(invoice.ksef_number(), 'K')
        self.assertEqual(invoice.get('items.0.name'), 'X')
        self.assertTrue(invoice.has('paid_at'))
        self.assertFalse(invoice.has('missing'))
        with self.assertRaises(AttributeError):
            invoice.id = 'changed'
        invoice.totals['gross'] = '0'
        self.assertEqual(invoice.gross_total(), Decimal('123.45'))
        self.assertIsNone(sdk.Entity({'amount': ''}).amount('amount'))

    def test_config(self):
        config = sdk.Config('secret')
        self.assertEqual(config.with_max_retries(4).max_retries, 4)
        self.assertEqual(config.max_retries, 2)
        self.assertNotIn('secret', repr(config))
        self.assertEqual(config.identify('ERP','2.1','a@example.test').user_agent, 'ERP/2.1 (+a@example.test; compat=2026-09-15) billto-python/1.0.0')
        for options in [{'max_retries': -1}, {'timeout': 0}, {'base_url': 'file:///tmp'}, {'base_url': 'https://user:pass@example.test'}, {'user_agent': 'bad\nheader'}]:
            with self.assertRaises(ValueError):
                sdk.Config('test', **options)
        self.assertEqual(sdk.BillTo.sandbox('test').config.base_url, sdk.SANDBOX_BASE_URL)

    def test_idempotency_and_enums(self):
        self.assertRegex(sdk.IdempotencyKey.generate(), r'^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$')
        self.assertEqual(sdk.IdempotencyKey.from_operation('event','billto-php'), hashlib.sha256(b'billto-php|event').hexdigest())
        self.assertEqual(sdk.PaymentMethod.CARD.value, 'card')
        self.assertTrue(sdk.InvoiceType.KOR.is_correction())
        self.assertEqual(sdk.VatRate.RATE23.percent(), 23)
        self.assertFalse(sdk.InvoiceStatus.DRAFT.is_issued())

    def test_real_http_transport_redirect_and_timeout(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                requests.append((self.path, self.headers.get('Authorization')))
                if self.path.endswith('/slow'):
                    time.sleep(0.3)
                    return
                if self.path.endswith('/redirect'):
                    self.send_response(302)
                    self.send_header('Location', '/leak')
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"data":{"id":"live"}}')

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = sdk.BillTo(sdk.Config('test-token', base_url=f'http://127.0.0.1:{server.server_port}/api/v1', max_retries=0, timeout=0.1))
            self.assertEqual(client.invoices.get('live').id, 'live')
            with self.assertRaises(sdk.UnexpectedResponseError):
                client.invoices.get('redirect')
            with self.assertRaises(sdk.TransportError):
                client.invoices.get('slow')
            self.assertTrue(all(path != '/leak' for path, _ in requests))
            self.assertEqual(requests[0][1], 'Bearer test-token')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
