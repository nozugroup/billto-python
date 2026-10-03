"""Run with an ISO timestamp argument and BILLTO_TOKEN in the environment.

Persist the returned cursor only after saving all processed rows successfully.
"""
import json
import os
import sys
from datetime import datetime, timezone
from billto import BillTo


def main() -> None:
    client = BillTo(os.environ['BILLTO_TOKEN'])
    if len(sys.argv) != 2:
        raise SystemExit('Pass the timestamp of the previous successful sync.')
    next_cursor = datetime.now(timezone.utc).isoformat()
    for invoice in client.incoming_invoices.updated_since(sys.argv[1]):
        print(json.dumps(invoice.to_dict(), ensure_ascii=False))  # Upsert by invoice.id.
    print('Next sync cursor:', next_cursor)


if __name__ == '__main__':
    main()
