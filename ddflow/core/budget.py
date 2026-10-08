"""One vocabulary for "how much may this take": tokens, characters, and the exchange rate.

Before this module the brief, the markdown view and the recall renderer each wrote their own
`// 4` and `* 4`, and the recall default of 4000 was defined in four places. The brief's
API and the recall defaults now use this vocabulary; `views/markdown.py` (the brief's
renderer) and the recall renderers move onto it with the context pack
(B-uni-context-pack.2-pack). A budget states its unit: the brief counts TOKENS
(`[session] brief_max_tokens`), recall counts CHARACTERS (`max_chars`), and
`CHARS_PER_TOKEN` is the one conversion between them. The estimate is deliberately cheap -- a tokenizer import would make the brief
the slowest command in the tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: The estimate's exchange rate: roughly four characters to a token.
CHARS_PER_TOKEN = 4
#: `ddflow recall`'s answer budget (characters) when the caller names none.
RECALL_MAX_CHARS = 4000

Unit = Literal["tokens", "chars"]


def approx_tokens(text: str) -> int:
    """Cheap token estimate of `text`: at least 1."""
    return max(1, len(text) // CHARS_PER_TOKEN)


def chars_for(tokens: int) -> int:
    """The characters that `tokens` tokens stand for."""
    return tokens * CHARS_PER_TOKEN


@dataclass(frozen=True)
class Budget:
    """A limit and the unit it is stated in."""

    limit: int
    unit: Unit = "chars"

    @property
    def chars(self) -> int:
        """The limit in characters."""
        return chars_for(self.limit) if self.unit == "tokens" else self.limit

    @property
    def tokens(self) -> int:
        """The limit in tokens (at least 1)."""
        return (
            max(1, self.limit) if self.unit == "tokens" else max(1, self.limit // CHARS_PER_TOKEN)
        )

    def cost(self, text: str) -> int:
        """What `text` costs against this budget, in its unit."""
        return approx_tokens(text) if self.unit == "tokens" else len(text)

    def fits(self, text: str) -> bool:
        return self.cost(text) <= self.limit
