"""HTTP transport and shared primitives for the BillTo API."""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from enum import Enum
from http.client import HTTPException
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

VERSION = "1.0.0"
API_COMPAT = "2026-09-15"
DEFAULT_BASE_URL = "https://billto.pl/api/v1"
SANDBOX_BASE_URL = "https://sandbox.billto.pl/api/v1"
DEFAULT_USER_AGENT = f"billto-python/{VERSION} (+https://github.com/nozugroup/billto-php; compat={API_COMPAT})"


def compact(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}


def date_string(value: date | str | None) -> str | None:
    return value.isoformat()[:10] if isinstance(value, date) else value


def iso_string(value: datetime | str) -> str:
    return value.isoformat() if isinstance(value, datetime) else value


def segment(value: str) -> str:
    if not isinstance(value, str) or not value or value in (".", ".."):
        raise ValueError("Resource ID must be a nonempty string.")
    return quote(value, safe="")


@dataclass(frozen=True)
class Config:
    token: str = field(repr=False)
    base_url: str = DEFAULT_BASE_URL
    max_retries: int = 2
    auto_idempotency: bool = True
    retry_base_delay: float = 0.5
    max_retry_delay: float = 10.0
    timeout: float = 30.0
    user_agent: str = DEFAULT_USER_AGENT
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.token, str) or not self.token.strip() or re.search(r"[\r\n]", self.token):
            raise ValueError("API token must be nonempty and contain no newlines.")
        url = urlsplit(self.base_url)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Invalid API base URL.")
        if type(self.max_retries) is not int or self.max_retries < 0:
            raise ValueError("max_retries must be a nonnegative integer.")
        for name in ("retry_base_delay", "max_retry_delay", "timeout"):
            value = getattr(self, name)
            if not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0 or (name == "timeout" and value == 0):
                raise ValueError(f"Invalid {name}.")
        if not isinstance(self.auto_idempotency, bool):
            raise ValueError("auto_idempotency must be a boolean.")
        if not isinstance(self.user_agent, str) or not self.user_agent.strip() or re.search(r"[^\x20-\x7e]", self.user_agent):
            raise ValueError("Invalid User-Agent.")
        if self.correlation_id is not None and (not isinstance(self.correlation_id, str) or not self.correlation_id.strip() or re.search(r"[^ -~]", self.correlation_id)):
            raise ValueError("Invalid X-Correlation-Id.")

    def with_correlation_id(self, correlation_id: str | None) -> Config:
        return replace(self, correlation_id=correlation_id)

    def with_base_url(self, base_url: str) -> Config:
        return replace(self, base_url=base_url)

    def with_max_retries(self, max_retries: int) -> Config:
        return replace(self, max_retries=max_retries)

    def with_auto_idempotency(self, enabled: bool) -> Config:
        return replace(self, auto_idempotency=enabled)

    def with_user_agent(self, user_agent: str) -> Config:
        return replace(self, user_agent=user_agent)

    def identify(self, product: str, version: str, contact: str, compat: str | None = API_COMPAT) -> Config:
        def clean(value: str, limit: int) -> str:
            return re.sub(r"[^A-Za-z0-9._\-/:@+?=&%]", "", value)[:limit]
        parts = (clean(product, 60), clean(version, 40), clean(contact, 180))
        if not all(parts):
            raise ValueError("Product, version and contact are required.")
        metadata = f"; compat={clean(compat, 40)}" if compat else ""
        return self.with_user_agent(f"{parts[0]}/{parts[1]} (+{parts[2]}{metadata}) billto-python/{VERSION}")

    def normalized_base_url(self) -> str:
        return self.base_url.rstrip("/")


class BillToError(Exception):
    pass


class TransportError(BillToError):
    pass


class UnexpectedResponseError(BillToError):
    def __init__(self, message: str, response: ApiResponse):
        super().__init__(message)
        self.response = response


class ApiError(BillToError):
    def __init__(self, message: str, response: ApiResponse):
        super().__init__(message)
        self.status = response.status
        self.response = response

    def body(self) -> dict[str, Any] | None:
        return self.response.json()

    def get(self, key: str) -> Any:
        return (self.body() or {}).get(key)

    def is_client_error(self) -> bool:
        return 400 <= self.status < 500

    def is_server_error(self) -> bool:
        return self.status >= 500


class AuthenticationError(ApiError):
    pass


class PaymentRequiredError(ApiError):
    def upgrade_url(self) -> str | None:
        return self.get("upgrade_url")


class ForbiddenError(ApiError):
    def is_insufficient_scope(self) -> bool:
        return "scope" in str(self).lower()

    def is_nip_conflict(self) -> bool:
        return self.get("code") == "nip_conflict"


class NotFoundError(ApiError):
    pass


class ConflictError(ApiError):
    def data(self) -> Any:
        return self.get("data")


class ServerError(ApiError):
    pass


class ValidationError(ApiError):
    def __init__(self, message: str, response: ApiResponse):
        super().__init__(message, response)
        raw = self.get("errors")
        self.errors = {
            key: [value if isinstance(value, str) else json.dumps(value, ensure_ascii=False) for value in (values if isinstance(values, list) else [values])]
            for key, values in (raw if isinstance(raw, dict) else {}).items()
        }

    def has(self, field: str) -> bool:
        return field in self.errors

    def first(self, field: str) -> str | None:
        return next(iter(self.errors.get(field, [])), None)

    def messages(self) -> list[str]:
        return [message for values in self.errors.values() for message in values]


class RateLimitError(ApiError):
    def __init__(self, message: str, response: ApiResponse):
        super().__init__(message, response)
        retry = response.header("retry-after")
        self.retry_after: float | None = None
        if retry:
            if re.fullmatch(r"\d+(\.\d+)?", retry.strip()):
                self.retry_after = float(retry)
            else:
                try:
                    self.retry_after = max(0.0, (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass
        self.usage = self.get("usage")
        self.upgrade_url = self.get("upgrade_url")

    def is_plan_limit(self) -> bool:
        return self.retry_after is None


def map_error(response: ApiResponse) -> ApiError:
    cls = {401: AuthenticationError, 402: PaymentRequiredError, 403: ForbiddenError,
           404: NotFoundError, 409: ConflictError, 422: ValidationError, 429: RateLimitError}.get(
               response.status, ServerError if response.status >= 500 else ApiError)
    return cls(response.message() or f"BillTo API error (HTTP {response.status}).", response)


@dataclass
class ApiResponse:
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.headers = {key.lower(): value for key, value in self.headers.items()}

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())

    def is_json(self) -> bool:
        return "json" in (self.header("content-type") or "").lower()

    def json(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.body)
            return data if isinstance(data, dict) else None
        except (ValueError, UnicodeDecodeError):
            return None

    def data_object(self) -> dict[str, Any]:
        data = (self.json() or {}).get("data")
        if not isinstance(data, dict):
            raise UnexpectedResponseError('Expected an object in "data".', self)
        return data

    def data_list(self) -> list[dict[str, Any]]:
        data = (self.json() or {}).get("data")
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise UnexpectedResponseError('Expected a list of objects in "data".', self)
        return data

    def meta(self) -> dict[str, Any]:
        data = (self.json() or {}).get("meta")
        return data if isinstance(data, dict) else {}

    def message(self) -> str | None:
        data = (self.json() or {}).get("message")
        return data if isinstance(data, str) else None


def safe_filename(value: str) -> str:
    name = re.sub(r'[\x00-\x1f<>:"|?*]', "_", value.replace("\\", "/").split("/")[-1]).rstrip(". ")
    return "download.bin" if not name or re.match(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", name, re.I) else name


@dataclass(frozen=True)
class BinaryFile:
    content: bytes
    content_type: str
    filename: str = "download.bin"

    def __post_init__(self) -> None:
        object.__setattr__(self, "filename", safe_filename(self.filename))

    @classmethod
    def from_response(cls, response: ApiResponse, fallback: str) -> BinaryFile:
        disposition = response.header("content-disposition") or ""
        extended = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.I)
        ordinary = re.search(r'filename="?([^";]+)"?', disposition, re.I)
        filename = unquote(extended[1].strip('"')) if extended else ordinary[1].strip() if ordinary else fallback
        return cls(response.body, response.header("content-type") or "application/octet-stream", filename)

    def size(self) -> int:
        return len(self.content)

    def save_to(self, destination: str | Path) -> int:
        return Path(destination).write_bytes(self.content)

    def save_in(self, directory: str | Path) -> Path:
        destination = Path(directory) / self.filename
        self.save_to(destination)
        return destination


class IdempotencyKey:
    @staticmethod
    def generate() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def from_operation(operation_id: str, namespace: str = "billto-python") -> str:
        return hashlib.sha256(f"{namespace}|{operation_id}".encode("utf-8")).hexdigest()


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _send(request: Request, timeout: float) -> ApiResponse:
    try:
        response = build_opener(_NoRedirect()).open(request, timeout=timeout)
    except HTTPError as error:
        response = error
    with response:
        return ApiResponse(response.code, response.read(), dict(response.headers.items()))


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Decimal values must be finite.")
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Not JSON serializable: {type(value).__name__}")


def _query_pairs(query: dict[str, Any], prefix: str = "") -> list[tuple[str, str]]:
    pairs = []
    for key, value in query.items():
        if value is None:
            continue
        name = f"{prefix}[{key}]" if prefix else key
        if isinstance(value, Enum):
            value = value.value
        if isinstance(value, (dict, list, tuple)):
            pairs.extend(_query_pairs(value if isinstance(value, dict) else dict(enumerate(value)), name))
        else:
            pairs.append((name, "1" if value is True else "0" if value is False else value.isoformat() if isinstance(value, date) else str(value)))
    return pairs


class Transport:
    def __init__(self, config: Config, *, sender: Callable[[Request, float], ApiResponse] = _send,
                 sleeper: Callable[[float], None] = time.sleep, key_generator: Callable[[], str] = IdempotencyKey.generate,
                 on_response: Callable[[ApiResponse], None] | None = None):
        self.config = config
        self.sender = sender
        self.sleeper = sleeper
        self.key_generator = key_generator
        if on_response is not None and not callable(on_response):
            raise TypeError("on_response must be callable.")
        self.on_response = on_response

    def get(self, path: str, query: dict[str, Any] | None = None) -> ApiResponse:
        return self.request("GET", path, query)

    def post(self, path: str, body: dict[str, Any] | None = None, key: str | None = None) -> ApiResponse:
        return self.request("POST", path, body=body if body is not None else {}, key=key)

    def put(self, path: str, body: dict[str, Any], key: str | None = None) -> ApiResponse:
        return self.request("PUT", path, body=body, key=key)

    def delete(self, path: str) -> ApiResponse:
        return self.request("DELETE", path)

    def download(self, path: str, query: dict[str, Any] | None = None, fallback: str = "download.bin") -> BinaryFile:
        return BinaryFile.from_response(self.request("GET", path, query, accept="*/*"), fallback)

    def request(self, method: str, path: str, query: dict[str, Any] | None = None,
                body: dict[str, Any] | None = None, key: str | None = None, accept: str = "application/json") -> ApiResponse:
        method = method.upper()
        mutation = method in ("POST", "PUT", "PATCH")
        if mutation and key is None and self.config.auto_idempotency:
            key = self.key_generator()
        if key is not None and (not isinstance(key, str) or not key.strip() or re.search(r"[\r\n]", key)):
            raise ValueError("Invalid idempotency key.")
        retryable = not mutation or key is not None
        url = f"{self.config.normalized_base_url()}/{path.lstrip('/')}"
        encoded_query = urlencode(_query_pairs(query or {}), quote_via=quote)
        if encoded_query:
            url += ("&" if "?" in url else "?") + encoded_query
        headers = {"Authorization": f"Bearer {self.config.token}", "Accept": accept, "User-Agent": self.config.user_agent}
        if self.config.correlation_id is not None:
            headers["X-Correlation-Id"] = self.config.correlation_id
        if key is not None:
            headers["Idempotency-Key"] = key
        encoded = None
        if body is not None:
            encoded = json.dumps(body, ensure_ascii=False, allow_nan=False, default=_json_default).encode("utf-8")
            headers["Content-Type"] = "application/json"
        for attempt in range(self.config.max_retries + 1):
            request = Request(url, data=encoded, headers=headers, method=method)
            try:
                response = self.sender(request, self.config.timeout)
            except (URLError, OSError, HTTPException) as cause:
                if retryable and attempt < self.config.max_retries:
                    self.sleeper(self.backoff(attempt))
                    continue
                raise TransportError("Could not reach the BillTo API.") from cause
            if self.on_response is not None:
                self.on_response(response)
            if 200 <= response.status < 300:
                return response
            if response.status < 400:
                raise UnexpectedResponseError("Unexpected HTTP redirect or informational response.", response)
            error = map_error(response)
            if retryable and attempt < self.config.max_retries and (response.status in (502, 503, 504) or isinstance(error, RateLimitError) and error.retry_after is not None):
                self.sleeper(min(error.retry_after, self.config.max_retry_delay) if isinstance(error, RateLimitError) else self.backoff(attempt))
                continue
            raise error
        raise AssertionError("Unreachable retry state.")

    def backoff(self, attempt: int) -> float:
        return min(self.config.retry_base_delay * 2 ** attempt * (1 + random.random() * 0.25), self.config.max_retry_delay)
