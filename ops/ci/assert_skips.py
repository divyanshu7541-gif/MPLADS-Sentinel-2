"""
CI guard: the full pytest run may skip exactly the expected tests, nothing else.

    python ops/ci/assert_skips.py ci-artifacts/junit.xml

Reads pytest's JUnit XML and fails (exit 1) unless the skipped tests are exactly
EXPECTED_SKIPS. It also fails on any error or failure, and if no test ran at all.
A suite that "passes" by skipping (for example the browser e2e tests when the stack
is not up) is caught here.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET

# classname::name -> why it may skip
EXPECTED_SKIPS = {
    "tests.test_phase11_chat::test_grounded_reply_never_shows_a_number_for_not_evaluated_work": (
        "run 1 has no NOT_EVALUATED work in the published config to ask about"
    ),
}


def main(path: str) -> int:
    ran, skipped, broken = 0, set(), []
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError) as e:
        print(f"SKIP CHECK: no readable JUnit report at {path}: {e}")
        return 1
    for tc in tree.iter("testcase"):
        tid = f"{tc.get('classname')}::{tc.get('name')}"
        ran += 1
        if tc.find("skipped") is not None:
            skipped.add(tid)
        if tc.find("failure") is not None or tc.find("error") is not None:
            broken.append(tid)
    problems = []
    if ran == 0:
        problems.append("no tests in the report")
    for tid in sorted(skipped - EXPECTED_SKIPS.keys()):
        problems.append(f"unexpected skip: {tid}")
    for tid in sorted(EXPECTED_SKIPS.keys() - skipped):
        problems.append(f"expected skip did not happen (update EXPECTED_SKIPS): {tid}")
    problems += [f"failed or errored: {tid}" for tid in broken]
    print(f"{ran} tests, {len(skipped)} skipped (expected exactly {len(EXPECTED_SKIPS)})")
    for p in problems:
        print("SKIP CHECK:", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
