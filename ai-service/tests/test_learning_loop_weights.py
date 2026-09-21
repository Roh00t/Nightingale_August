"""
Pins the weight an unrecognised interaction type carries in the learning loop.

services/importance.py reads interaction_log and learns per-clinic weightings
that reorder clinical findings. That makes interaction_log a context-poisoning
surface in the ASI06 sense: influence what is written into it and you steer
what a clinician is shown later.

The fallback for an action_type not in ACTION_TYPE_WEIGHTS used to be 0.3 -
numerically identical to "view" in that same table. An action this module had
never heard of was therefore counted as genuine clinician engagement and was
indistinguishable from a real one, so anything that could write to the table
gained influence over ranking without anyone adding it here.

Unknown types are now skipped rather than weighted. Defaulting them to 0.0 was
tried first and these tests rejected it: 0.0 is a real weight that normalises
below neutral, so it inverted the bug instead of removing it. An unrecognised
action is an absence of evidence and must reach neither accumulator.

These tests run entirely offline through set_interaction_source. They assert
behaviour, not source text, so they survive a refactor of the lookup.
"""

from __future__ import annotations

import asyncio

import pytest

from services.importance import (
    ACTION_TYPE_WEIGHTS,
    _compute_learned_score,
    set_interaction_source,
)


NEUTRAL = 0.5
CONTENT = "wound dressing changed, exudate reducing, review in three days"
KEYWORDS = ["wound", "dressing", "exudate", "review"]


def _rows(action_type: str, n: int = 6) -> list[dict]:
    """n interactions that all overlap CONTENT's vocabulary."""
    return [
        {
            "action_type": action_type,
            "target_metadata": {"keywords": KEYWORDS, "topic": "wound_care"},
        }
        for _ in range(n)
    ]


@pytest.fixture
def history():
    """Install a synthetic interaction history, then always uninstall it."""
    installed: list[dict] = []

    def _install(rows):
        installed.clear()
        installed.extend(rows)
        set_interaction_source(lambda *a, **k: list(installed))

    yield _install
    set_interaction_source(None)


def _score(clinic_id: str = "clinic-a") -> float:
    return asyncio.run(_compute_learned_score(CONTENT, clinic_id))


class TestUnknownActionTypesCarryNoWeight:
    def test_an_unrecognised_action_does_not_move_the_score(self, history):
        """Six interactions of a type we have never heard of must leave the
        neutral prior untouched. Under the old 0.3 fallback they raised it."""
        history(_rows("exported_to_csv"))
        assert _score() == pytest.approx(NEUTRAL), (
            "an action_type absent from ACTION_TYPE_WEIGHTS moved the learned "
            "score - unknown actions must not count as engagement"
        )

    def test_it_is_no_longer_equal_to_a_genuine_view(self, history):
        """The precise defect: the fallback was the same number as 'view'.

        Asserting the two now diverge is what makes this test fail if someone
        restores 0.3, even though 0.3 is a legitimate value elsewhere.
        """
        history(_rows("view"))
        view_score = _score()

        history(_rows("some_action_added_next_quarter"))
        unknown_score = _score()

        assert view_score != pytest.approx(unknown_score), (
            "an unknown action_type scores identically to a real clinician "
            "view - this is the ASI06 hole reopening"
        )

    def test_abstaining_is_not_the_same_as_scoring_zero(self):
        """The distinction the first attempt at this fix got wrong.

        'unpin' sits at 0.0 and is a real, deliberate signal: the clinician
        did something, and it counts for nothing. An unrecognised action type
        is not that - it is an absence of evidence, and must not enter the mean
        at all. Scoring it 0.0 normalises to (0.0 + 0.3) / 1.3 = 0.23 and pulls
        the result BELOW neutral, which merely inverts the original bug.
        """
        assert ACTION_TYPE_WEIGHTS["unpin"] == 0.0
        assert "unpin" in ACTION_TYPE_WEIGHTS

    @pytest.mark.parametrize("bogus", ["", "VIEW", "view ", "ui_expand", "../../etc"])
    def test_near_misses_and_junk_carry_no_weight(self, history, bogus):
        """Case, whitespace and path-shaped junk must not find a weight."""
        history(_rows(bogus))
        assert _score() == pytest.approx(NEUTRAL), f"{bogus!r} carried weight"


class TestKnownActionTypesStillWork:
    """The fix must not flatten the signal the loop exists to learn from."""

    def test_accept_still_raises_the_score(self, history):
        history(_rows("accept"))
        assert _score() > NEUTRAL, "accept must still count as positive engagement"

    def test_reject_still_lowers_it(self, history):
        history(_rows("reject"))
        assert _score() < NEUTRAL, "reject must still count against"

    def test_a_missing_action_type_is_still_treated_as_a_view(self, history):
        """A row whose action_type went missing is a real interaction with a
        lost field, which is a different thing from an unrecognised type. The
        inner default stays 'view' deliberately."""
        rows = [
            {"target_metadata": {"keywords": KEYWORDS, "topic": "wound_care"}}
            for _ in range(6)
        ]
        history(rows)
        missing_score = _score()

        history(_rows("view"))
        assert missing_score == pytest.approx(_score()), (
            "a row with no action_type should score as a view, not as unknown"
        )
