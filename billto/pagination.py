from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, Iterator, TypeVar

from .core import ApiResponse, UnexpectedResponseError
from .entities import Entity

T = TypeVar("T", bound=Entity)


@dataclass
class Page(Generic[T]):
    items: list[T]
    current_page: int = 1
    last_page: int = 1
    total: int | None = None
    per_page: int | None = None

    @classmethod
    def from_response(cls, response: ApiResponse, entity: type[T]) -> Page[T]:
        meta = response.meta()
        current, last = meta.get("current_page", 1), meta.get("last_page", 1)
        if type(current) is not int or current < 1 or type(last) is not int or last < 1:
            raise UnexpectedResponseError("Invalid pagination metadata.", response)
        return cls([entity(row) for row in response.data_list()], current, last, meta.get("total"), meta.get("per_page"))

    def has_more_pages(self) -> bool:
        return self.current_page < self.last_page

    def next_page(self) -> int | None:
        return self.current_page + 1 if self.has_more_pages() else None

    def is_empty(self) -> bool:
        return not self.items

    def first(self) -> T | None:
        return self.items[0] if self.items else None

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[T]:
        return iter(self.items)

    def to_list(self) -> list[dict]:
        return [item.to_dict() for item in self.items]


class Paginator(Generic[T]):
    def __init__(self, fetch_page: Callable[[int], Page[T]], start_page: int = 1):
        self.fetch_page = fetch_page
        self.start_page = start_page

    def pages(self) -> Iterator[Page[T]]:
        number: int | None = self.start_page
        while number is not None:
            page = self.fetch_page(number)
            next_number = page.next_page()
            if next_number is not None and next_number <= number:
                raise RuntimeError("API pagination did not advance.")
            yield page
            number = next_number

    def __iter__(self) -> Iterator[T]:
        for page in self.pages():
            yield from page.items

    def collect(self) -> list[T]:
        return list(self)
