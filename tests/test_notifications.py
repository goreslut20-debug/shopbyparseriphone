import unittest
from decimal import Decimal

from shopby_monitor.config import PRODUCT_NAME, PRODUCT_URL
from shopby_monitor.notifications import notification_text, should_notify


class NotificationTests(unittest.TestCase):
    def decisions(self, prices):
        previous_price = None
        decisions = []
        for price in prices:
            current_price = Decimal(str(price))
            decisions.append(should_notify(current_price, previous_price, Decimal("3000")))
            previous_price = current_price
        return decisions

    def test_requirement_example(self):
        self.assertEqual(self.decisions([2990, 2990, 2950, 2970, 2940]),
                         [True, False, True, False, True])

    def test_strict_threshold_and_first_observation(self):
        for current_price, expected in (("3001", False), ("3000", False), ("2999.99", True)):
            with self.subTest(current_price=current_price):
                self.assertEqual(self.decisions([current_price]), [expected])

    def test_rise_to_threshold_rearms_notification(self):
        self.assertEqual(self.decisions([2990, 3000, 2995, 3100, 2999]),
                         [True, False, True, False, True])

    def test_decrease_is_relative_to_previous_observation(self):
        self.assertEqual(self.decisions([2950, 2970, 2960]), [True, False, True])

    def test_no_notification_for_decrease_above_threshold(self):
        self.assertEqual(self.decisions([3500, 3200, 3000]), [False, False, False])

    def test_message_contains_required_product_price_and_link(self):
        message = notification_text(Decimal("2990.50"))
        for expected in (PRODUCT_NAME, "2990.50 BYN", PRODUCT_URL):
            self.assertIn(expected, message)
