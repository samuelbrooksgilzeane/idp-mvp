"""Shared admission check; both stages use one configured combined inference budget."""


def validate_capacity(
    parse_concurrency: int, extraction_concurrency: int, combined_budget: int
) -> None:
    if min(parse_concurrency, extraction_concurrency, combined_budget) < 1:
        raise ValueError("Processing concurrency budgets must be positive")
    if parse_concurrency + extraction_concurrency > combined_budget:
        raise ValueError(
            "Parser and extractor concurrency exceed the combined inference budget"
        )
