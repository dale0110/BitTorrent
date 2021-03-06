#
# Copyright (c) 2001-2004 Twisted Matrix Laboratories.
# See LICENSE for details.

"""test twisted's doctest support
"""

from twisted.trial import runner, unittest, reporter
from twisted.trial.test import mockdoctest


class TestRunners(unittest.TestCase):
    def test_correctCount(self):
        suite = runner.DocTestSuite(mockdoctest)
        self.assertEqual(7, suite.countTestCases())

    def test_basicTrialIntegration(self):
        loader = runner.TestLoader()
        suite = loader.loadDoctests(mockdoctest)
        self.assertEqual(7, suite.countTestCases())

    def test_expectedResults(self):
        suite = runner.DocTestSuite(mockdoctest)
        result = reporter.TestResult()
        suite.run(result)
        self.assertEqual(5, len(result.successes))
        # doctest reports failures as errors in 2.3
        self.assertEqual(2, len(result.errors) + len(result.failures))

