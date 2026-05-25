"""Per-product API budget guardrails for lookup/enrichment."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(init=False)
class ProductLookupBudget:
    brave_searches_limit: int
    page_fetches_limit: int
    ai_calls_limit: int
    brave_searches_used: int
    page_fetches_used: int
    ai_calls_used: int
    stopped_reason: str
    mode: str
    allows_retailer: bool
    allows_general_fallback: bool

    def __init__(
        self,
        brave_searches_limit: int | None = None,
        page_fetches_limit: int | None = None,
        ai_calls_limit: int | None = None,
        *,
        max_searches: int | None = None,
        max_urls: int | None = None,
        max_ai_calls: int | None = None,
        brave_searches_used: int = 0,
        page_fetches_used: int = 0,
        ai_calls_used: int = 0,
        stopped_reason: str = "",
        mode: str = "standard",
        allows_retailer: bool = False,
        allows_general_fallback: bool = True,
        searches_used: int | None = None,
        urls_used: int | None = None,
    ) -> None:
        self.brave_searches_limit = int(brave_searches_limit if brave_searches_limit is not None else (max_searches or 0))
        self.page_fetches_limit = int(page_fetches_limit if page_fetches_limit is not None else (max_urls or 0))
        self.ai_calls_limit = int(ai_calls_limit if ai_calls_limit is not None else (max_ai_calls or 0))
        self.brave_searches_used = int(searches_used if searches_used is not None else brave_searches_used)
        self.page_fetches_used = int(urls_used if urls_used is not None else page_fetches_used)
        self.ai_calls_used = int(ai_calls_used)
        self.stopped_reason = stopped_reason
        self.mode = mode
        self.allows_retailer = allows_retailer
        self.allows_general_fallback = allows_general_fallback

    @property
    def max_searches(self) -> int:
        return self.brave_searches_limit

    @max_searches.setter
    def max_searches(self, value: int) -> None:
        self.brave_searches_limit = int(value)

    @property
    def max_urls(self) -> int:
        return self.page_fetches_limit

    @max_urls.setter
    def max_urls(self, value: int) -> None:
        self.page_fetches_limit = int(value)

    @property
    def max_ai_calls(self) -> int:
        return self.ai_calls_limit

    @max_ai_calls.setter
    def max_ai_calls(self, value: int) -> None:
        self.ai_calls_limit = int(value)

    @property
    def searches_used(self) -> int:
        return self.brave_searches_used

    @searches_used.setter
    def searches_used(self, value: int) -> None:
        self.brave_searches_used = int(value)

    @property
    def urls_used(self) -> int:
        return self.page_fetches_used

    @urls_used.setter
    def urls_used(self, value: int) -> None:
        self.page_fetches_used = int(value)

    def can_search(self) -> bool:
        allowed = self.brave_searches_used < self.brave_searches_limit
        if not allowed and not self.stopped_reason:
            self.stopped_reason = "search budget exhausted"
        return allowed

    def can_fetch(self) -> bool:
        allowed = self.page_fetches_used < self.page_fetches_limit
        if not allowed and not self.stopped_reason:
            self.stopped_reason = "page fetch budget exhausted"
        return allowed

    def can_ai_call(self) -> bool:
        allowed = self.ai_calls_used < self.ai_calls_limit
        if not allowed and not self.stopped_reason:
            self.stopped_reason = "AI budget exhausted"
        return allowed

    def consume_search(self) -> None:
        if not self.can_search():
            return
        self.brave_searches_used += 1

    def consume_fetch(self) -> None:
        if not self.can_fetch():
            return
        self.page_fetches_used += 1

    def consume_ai_call(self) -> None:
        if not self.can_ai_call():
            return
        self.ai_calls_used += 1

    def stop(self, reason: str) -> None:
        if reason:
            self.stopped_reason = reason

    def diagnostics(self) -> dict:
        return {
            "brave_searches_used": self.brave_searches_used,
            "brave_searches_limit": self.brave_searches_limit,
            "page_fetches_used": self.page_fetches_used,
            "page_fetches_limit": self.page_fetches_limit,
            "ai_calls_used": self.ai_calls_used,
            "ai_calls_limit": self.ai_calls_limit,
            "stopped_reason": self.stopped_reason,
            "search_usage": f"Used {self.brave_searches_used}/{self.brave_searches_limit} search calls",
            "fetch_usage": f"Used {self.page_fetches_used}/{self.page_fetches_limit} page fetches",
            "ai_usage": f"Used {self.ai_calls_used}/{self.ai_calls_limit} AI calls",
        }
