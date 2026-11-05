class Refused(str):
    """An action message that is a REFUSAL: nothing was written, and the operator must
    act. A `str`, so `adopt`'s action list reads exactly as before; a distinct TYPE, so
    `setup` can fail on it without sniffing the wording. It used to be a plain string,
    and `adopt` printed "SKIPPED ..." then "ddflow adopted for: kilo" and exited 0 --
    a server never registered, reported as done (roborev on 7216f5e). The same contract
    `companions add` already keeps (`test_a_refusal_is_not_reported_as_success`)."""
