from __future__ import annotations

import unittest

from z0int.credit import lift_credit, paired_credit


class CreditGateTests(unittest.TestCase):
    def test_lift_credit_requires_two_sigma_lift(self) -> None:
        weak = lift_credit(successes=53, n=100, baseline=0.50)
        strong = lift_credit(successes=70, n=100, baseline=0.50)
        self.assertFalse(weak.passed_2sigma)
        self.assertTrue(strong.passed_2sigma)
        self.assertGreater(strong.z_score or 0.0, 1.96)

    def test_paired_credit_uses_same_tasks_for_noninferiority(self) -> None:
        # Candidate differs from a perfect baseline on one of 100 tasks.  A
        # zero-margin point estimate would look "99% good", but the paired
        # lower confidence bound correctly refuses exact non-inferiority.
        baseline = [True] * 100
        candidate = [False] + [True] * 99
        exact = paired_credit(candidate, baseline, noninferiority_margin=0.0)
        tolerant = paired_credit(candidate, baseline, noninferiority_margin=0.04)
        self.assertFalse(exact.passed_2sigma_noninferiority)
        self.assertTrue(tolerant.passed_2sigma_noninferiority)
        self.assertEqual(exact.baseline_only_wins, 1)
        self.assertEqual(exact.candidate_only_wins, 0)


if __name__ == "__main__":
    unittest.main()
