"""Per-product and per-upload API budget guardrails for lookup/enrichment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


_RUN_MODE_LIMITS = {
    "fast": {
        "target_budget_usd": 0.10,
        "hard_budget_usd": 0.25,
        "max_ai_calls": 3,
        "max_external_lookups": 25,
        "max_image_searches": 5,
        "max_retries": 5,
    },
    "standard": {
        "target_budget_usd": 0.25,
        "hard_budget_usd": 0.50,
        "max_ai_calls": 6,
        "max_external_lookups": 50,
        "max_image_searches": 10,
        "max_retries": 10,
    },
    "balanced": {
        "target_budget_usd": 0.25,
        "hard_budget_usd": 0.50,
        "max_ai_calls": 6,
        "max_external_lookups": 50,
        "max_image_searches": 10,
        "max_retries": 10,
    },
    "deep": {
        "target_budget_usd": 1.00,
        "hard_budget_usd": 2.00,
        "max_ai_calls": 20,
        "max_external_lookups": 200,
        "max_image_searches": 50,
        "max_retries": 30,
    },
    "manual_retry": {
        "target_budget_usd": 1.00,
        "hard_budget_usd": 2.00,
        "max_ai_calls": 20,
        "max_external_lookups": 200,
        "max_image_searches": 50,
        "max_retries": 30,
    },
}


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _mode_float_env(mode: str, suffix: str, default: float) -> float:
    mode_key = f"ENRICHMENT_{mode.upper()}_{suffix}"
    if mode_key in os.environ:
        return _float_env(mode_key, default)
    generic_key = f"ENRICHMENT_{suffix}"
    return _float_env(generic_key, default) if mode == "fast" else default


def _mode_int_env(mode: str, suffix: str, default: int) -> int:
    mode_key = f"ENRICHMENT_{mode.upper()}_{suffix}"
    if mode_key in os.environ:
        return _int_env(mode_key, default)
    generic_key = f"ENRICHMENT_{suffix}"
    return _int_env(generic_key, default) if mode == "fast" else default


def _normalise_mode(mode: str) -> str:
    return mode if mode in _RUN_MODE_LIMITS else "fast"


@dataclass
class EnrichmentRunBudget:
    mode: str = "fast"
    target_budget_usd: float = 0.10
    hard_budget_usd: float = 0.25
    max_ai_calls: int = 3
    max_external_lookups: int = 25
    max_image_searches: int = 5
    max_retries: int = 5
    search_cost_usd: float = 0.003
    fetch_cost_usd: float = 0.0005
    ai_call_cost_usd: float = 0.03
    image_search_cost_usd: float = 0.003
    retry_cost_usd: float = 0.0
    estimated_cost_usd: float = 0.0
    external_lookups_used: int = 0
    image_searches_used: int = 0
    retries_used: int = 0
    ai_calls_used: int = 0
    skipped_calls: list[dict] = field(default_factory=list)
    skipped_fields: list[dict] = field(default_factory=list)
    paid_call_reasons: list[dict] = field(default_factory=list)
    stage_costs: dict[str, float] = field(default_factory=dict)
    item_costs: dict[str, float] = field(default_factory=dict)

    def remaining_budget_usd(self) -> float:
        return max(0.0, self.hard_budget_usd - self.estimated_cost_usd)

    def _limit_reached(self, kind: str) -> str:
        if kind in {"search", "fetch", "external_lookup"} and self.external_lookups_used >= self.max_external_lookups:
            return "external lookup limit reached"
        if kind == "ai" and self.ai_calls_used >= self.max_ai_calls:
            return "AI call limit reached"
        if kind == "image_search" and self.image_searches_used >= self.max_image_searches:
            return "image search limit reached"
        if kind == "retry" and self.retries_used >= self.max_retries:
            return "retry limit reached"
        return ""

    def can_spend(
        self,
        kind: str,
        cost_usd: float,
        *,
        item_key: str = "",
        field: str = "",
        reason: str = "",
        record_skip: bool = True,
    ) -> bool:
        limit_reason = self._limit_reached(kind)
        if limit_reason:
            if record_skip:
                self.record_skip(kind, cost_usd, item_key=item_key, field=field, reason=limit_reason)
            return False
        if self.estimated_cost_usd + cost_usd > self.hard_budget_usd:
            if record_skip:
                self.record_skip(kind, cost_usd, item_key=item_key, field=field, reason="hard budget exceeded")
            return False
        return True

    def record_skip(
        self,
        kind: str,
        cost_usd: float = 0.0,
        *,
        item_key: str = "",
        field: str = "",
        reason: str = "",
    ) -> None:
        record = {
            "kind": kind,
            "estimated_cost_usd": round(float(cost_usd), 6),
            "item_key": item_key,
            "field": field,
            "reason": reason or "budget limit",
        }
        self.skipped_calls.append(record)
        if field:
            self.skipped_fields.append(record)

    def consume(
        self,
        kind: str,
        cost_usd: float,
        *,
        item_key: str = "",
        field: str = "",
        reason: str = "",
        stage: str = "",
    ) -> bool:
        if not self.can_spend(kind, cost_usd, item_key=item_key, field=field, reason=reason):
            return False
        self.estimated_cost_usd += cost_usd
        if kind in {"search", "fetch", "external_lookup"}:
            self.external_lookups_used += 1
        elif kind == "ai":
            self.ai_calls_used += 1
        elif kind == "image_search":
            self.image_searches_used += 1
        elif kind == "retry":
            self.retries_used += 1
        stage_key = stage or kind
        self.stage_costs[stage_key] = round(self.stage_costs.get(stage_key, 0.0) + cost_usd, 6)
        if item_key:
            self.item_costs[item_key] = round(self.item_costs.get(item_key, 0.0) + cost_usd, 6)
        self.paid_call_reasons.append({
            "kind": kind,
            "stage": stage_key,
            "item_key": item_key,
            "field": field,
            "reason": reason,
            "estimated_cost_usd": round(cost_usd, 6),
        })
        return True

    def diagnostics(self) -> dict:
        most_expensive_item = ""
        most_expensive_item_cost = 0.0
        if self.item_costs:
            most_expensive_item, most_expensive_item_cost = max(self.item_costs.items(), key=lambda item: item[1])
        return {
            "mode": self.mode,
            "target_budget_usd": round(self.target_budget_usd, 4),
            "hard_budget_usd": round(self.hard_budget_usd, 4),
            "estimated_cost_usd": round(self.estimated_cost_usd, 4),
            "remaining_budget_usd": round(self.remaining_budget_usd(), 4),
            "external_lookups": self.external_lookups_used,
            "external_lookups_limit": self.max_external_lookups,
            "image_searches": self.image_searches_used,
            "image_searches_limit": self.max_image_searches,
            "ai_calls": self.ai_calls_used,
            "ai_calls_limit": self.max_ai_calls,
            "retries": self.retries_used,
            "retries_limit": self.max_retries,
            "skipped_calls_due_budget": len(self.skipped_calls),
            "skipped_calls": self.skipped_calls[-25:],
            "skipped_fields_due_budget": self.skipped_fields[-25:],
            "cost_by_stage": self.stage_costs,
            "cost_by_item": self.item_costs,
            "most_expensive_item": most_expensive_item,
            "most_expensive_item_cost_usd": round(most_expensive_item_cost, 4),
            "paid_call_reasons": self.paid_call_reasons[-25:],
        }


def run_budget_for_mode(mode: str) -> EnrichmentRunBudget:
    mode = _normalise_mode(mode)
    limits = dict(_RUN_MODE_LIMITS[mode])
    return EnrichmentRunBudget(
        mode=mode,
        target_budget_usd=_mode_float_env(mode, "TARGET_BUDGET_USD", limits["target_budget_usd"]),
        hard_budget_usd=_mode_float_env(mode, "HARD_BUDGET_USD", limits["hard_budget_usd"]),
        max_ai_calls=_mode_int_env(mode, "MAX_AI_CALLS_PER_UPLOAD", limits["max_ai_calls"]),
        max_external_lookups=_mode_int_env(mode, "MAX_EXTERNAL_LOOKUPS_PER_UPLOAD", limits["max_external_lookups"]),
        max_image_searches=_mode_int_env(mode, "MAX_IMAGE_SEARCHES_PER_UPLOAD", limits["max_image_searches"]),
        max_retries=_mode_int_env(mode, "MAX_RETRIES_PER_UPLOAD", limits["max_retries"]),
        search_cost_usd=_float_env("ENRICHMENT_SEARCH_COST_USD", 0.003),
        fetch_cost_usd=_float_env("ENRICHMENT_FETCH_COST_USD", 0.0005),
        ai_call_cost_usd=_float_env("ENRICHMENT_AI_CALL_COST_USD", 0.03),
        image_search_cost_usd=_float_env("ENRICHMENT_IMAGE_SEARCH_COST_USD", 0.003),
    )


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
    run_budget: EnrichmentRunBudget | None
    item_key: str

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
        run_budget: EnrichmentRunBudget | None = None,
        item_key: str = "",
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
        self.run_budget = run_budget
        self.item_key = item_key

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
        if allowed and self.run_budget is not None:
            allowed = self.run_budget.can_spend(
                "search",
                self.run_budget.search_cost_usd,
                item_key=self.item_key,
                field="Product URL",
                reason="product search",
            )
            if not allowed and not self.stopped_reason:
                self.stopped_reason = "run budget exhausted"
        return allowed

    def can_fetch(self) -> bool:
        allowed = self.page_fetches_used < self.page_fetches_limit
        if not allowed and not self.stopped_reason:
            self.stopped_reason = "page fetch budget exhausted"
        if allowed and self.run_budget is not None:
            allowed = self.run_budget.can_spend(
                "fetch",
                self.run_budget.fetch_cost_usd,
                item_key=self.item_key,
                field="Product URL",
                reason="candidate page fetch",
            )
            if not allowed and not self.stopped_reason:
                self.stopped_reason = "run budget exhausted"
        return allowed

    def can_ai_call(self) -> bool:
        allowed = self.ai_calls_used < self.ai_calls_limit
        if not allowed and not self.stopped_reason:
            self.stopped_reason = "AI budget exhausted"
        if allowed and self.run_budget is not None:
            allowed = self.run_budget.can_spend(
                "ai",
                self.run_budget.ai_call_cost_usd,
                item_key=self.item_key,
                field="missing verified-page fields",
                reason="verified page deterministic extraction incomplete",
            )
            if not allowed and not self.stopped_reason:
                self.stopped_reason = "run budget exhausted"
        return allowed

    def consume_search(self) -> None:
        if not self.can_search():
            return
        self.brave_searches_used += 1
        if self.run_budget is not None:
            self.run_budget.consume(
                "search",
                self.run_budget.search_cost_usd,
                item_key=self.item_key,
                field="Product URL",
                reason="product search",
                stage="search",
            )

    def consume_fetch(self) -> None:
        if not self.can_fetch():
            return
        self.page_fetches_used += 1
        if self.run_budget is not None:
            self.run_budget.consume(
                "fetch",
                self.run_budget.fetch_cost_usd,
                item_key=self.item_key,
                field="Product URL",
                reason="candidate page fetch",
                stage="page_fetch",
            )

    def consume_ai_call(self) -> None:
        if not self.can_ai_call():
            return
        self.ai_calls_used += 1
        if self.run_budget is not None:
            self.run_budget.consume(
                "ai",
                self.run_budget.ai_call_cost_usd,
                item_key=self.item_key,
                field="missing verified-page fields",
                reason="verified page deterministic extraction incomplete",
                stage="ai",
            )

    def stop(self, reason: str) -> None:
        if reason:
            self.stopped_reason = reason

    def diagnostics(self) -> dict:
        data = {
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
        if self.run_budget is not None:
            data.update({
                "run_estimated_cost_usd": round(self.run_budget.estimated_cost_usd, 4),
                "run_target_budget_usd": round(self.run_budget.target_budget_usd, 4),
                "run_hard_budget_usd": round(self.run_budget.hard_budget_usd, 4),
                "run_external_lookups": self.run_budget.external_lookups_used,
                "run_ai_calls": self.run_budget.ai_calls_used,
            })
        return data
