"""Spend control for an evaluation run.

Prices are supplied by whoever runs the evaluation and recorded in the report. They are
not baked in here: a table in the repository goes stale silently, and an estimate built
on a stale price is worse than none because it looks authoritative.

Before each request the runner *reserves* the most that request could cost and stops
when the ceiling cannot cover it. The reservation is deliberately pessimistic:

* input: one token per UTF-8 byte of the system prompt and user message, plus a fixed
  allowance for message framing. A byte-level tokenizer never produces more tokens than
  bytes, so this bounds the real count from above.
* output: the configured output-token limit, which thinking counts against too.
* one attempt: the provider client is configured with no automatic retries, so a single
  dispatch is at most one billable call.

Afterwards the reservation is replaced by the cost computed from reported usage. A
request with no usable usage keeps its whole reservation as its cost.
"""

from __future__ import annotations

from dataclasses import dataclass, field

FRAMING_TOKEN_ALLOWANCE = 256
# Relative to the input price. Recorded in the report as assumptions; the harness does
# not enable prompt caching, so both are normally multiplied by zero tokens.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10


@dataclass(frozen=True)
class Pricing:
    input_usd_per_mtok: float
    output_usd_per_mtok: float
    source: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "inputUsdPerMTok": self.input_usd_per_mtok,
            "outputUsdPerMTok": self.output_usd_per_mtok,
            "cacheWriteMultiplier": CACHE_WRITE_MULTIPLIER,
            "cacheReadMultiplier": CACHE_READ_MULTIPLIER,
            "source": self.source,
            "reservationMethod": (
                "input: one token per UTF-8 byte of system prompt and user message plus "
                f"{FRAMING_TOKEN_ALLOWANCE}; output: the configured output-token limit; "
                "one attempt per request (provider retries disabled)"
            ),
        }

    def reservation(self, prompt_bytes: int, max_output_tokens: int) -> float:
        input_tokens = prompt_bytes + FRAMING_TOKEN_ALLOWANCE
        return self._usd(input_tokens, self.input_usd_per_mtok) + self._usd(
            max_output_tokens, self.output_usd_per_mtok
        )

    def cost(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cache_creation_tokens: int = 0,
        cache_read_tokens: int = 0,
    ) -> float:
        return (
            self._usd(input_tokens, self.input_usd_per_mtok)
            + self._usd(output_tokens, self.output_usd_per_mtok)
            + self._usd(cache_creation_tokens, self.input_usd_per_mtok * CACHE_WRITE_MULTIPLIER)
            + self._usd(cache_read_tokens, self.input_usd_per_mtok * CACHE_READ_MULTIPLIER)
        )

    @staticmethod
    def _usd(tokens: int, per_mtok: float) -> float:
        return tokens * per_mtok / 1_000_000


@dataclass
class Budget:
    ceiling_usd: float
    committed_usd: float = 0.0
    stopped: bool = False
    charges: list[tuple[str, float, str]] = field(default_factory=list)

    @property
    def remaining_usd(self) -> float:
        return self.ceiling_usd - self.committed_usd

    def can_reserve(self, amount: float) -> bool:
        return amount <= self.remaining_usd + 1e-12

    def charge(self, case_id: str, amount: float, basis: str) -> None:
        """Record what one request is taken to have cost."""

        self.committed_usd += amount
        self.charges.append((case_id, amount, basis))

    def as_dict(self) -> dict[str, object]:
        return {
            "ceilingUsd": self.ceiling_usd,
            "estimatedSpendUsd": round(self.committed_usd, 6),
            "remainingUsd": round(self.remaining_usd, 6),
            "stoppedForBudget": self.stopped,
        }
