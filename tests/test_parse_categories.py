"""``parse_categories`` guard: `all` must not mask a typo'd category name
(B-upgrade-parse-categories-all-masks-bogus)."""

from __future__ import annotations

import pytest

from ddflow.services import upgrade_apply as UA
from ddflow.services import upgrade_plan as UP


def test_all_does_not_mask_an_unknown_category() -> None:
    with pytest.raises(ValueError, match="unknown upgrade category"):
        UA.parse_categories("all,bogus")


def test_all_alone_still_means_every_category() -> None:
    assert UA.parse_categories("all") == list(UP.CATEGORIES)
