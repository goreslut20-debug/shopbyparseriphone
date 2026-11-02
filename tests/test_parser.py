import unittest
from decimal import Decimal
from pathlib import Path

from shopby_monitor.config import MonitorError, PRODUCT_URL, parse_money
from shopby_monitor.parser import extract_minimum_price


FIXTURE = Path(__file__).parent / "fixtures" / "shopby_product_excerpt.html"


class PriceParserTests(unittest.TestCase):
    def setUp(self):
        self.page_html = FIXTURE.read_text(encoding="utf-8")

    def test_real_page_excerpt_ignores_cheaper_diagonals(self):
        self.assertEqual(extract_minimum_price(self.page_html), Decimal("3400.00"))

    def test_ignores_global_price_elements_outside_main_block(self):
        page_html = self.page_html.replace(
            "</body>",
            '<aside><span class="PriceBlock__PriceValue">от 1,00 Ђ</span></aside></body>',
        )
        self.assertEqual(extract_minimum_price(page_html), Decimal("3400.00"))

    def test_parses_below_threshold_with_nonbreaking_spaces(self):
        page_html = (
            self.page_html.replace("3 400,00", "2\u202f990,50")
            .replace("3400.00", "2990.50").replace('"lowPrice":3400', '"lowPrice":2990.50')
        )
        self.assertEqual(extract_minimum_price(page_html), Decimal("2990.50"))

    def test_rejects_missing_ambiguous_or_inconsistent_data(self):
        changes = {
            "wrong diagonal": ("L75MB-SRU", "L65MB-SRU"),
            "wrong id": ('data-model-id="1014472"', 'data-model-id="999"'),
            "wrong canonical": ('href="' + PRODUCT_URL, 'href="https://shop.by/other/'),
            "missing main block": ('class="ModelInfo__BlockPrice"', 'class="ChangedPriceBlock"'),
            "missing visible price": ('class="PriceBlock__PriceValue"', 'class="ChangedValue"'),
            "missing from": ("от&nbsp;</span>", "цена&nbsp;</span>"),
            "missing attribute": ('data-low-price="3400.00"', 'data-price="3400.00"'),
            "attribute mismatch": ('data-low-price="3400.00"', 'data-low-price="1999.00"'),
            "json mismatch": ('"lowPrice":3400', '"lowPrice":2990'),
            "wrong currency": ('"priceCurrency":"BYN"', '"priceCurrency":"RUB"'),
            "missing json product": ('"@type":"Product"', '"@type":"Other"'),
            "broken json": ('"lowPrice":3400', '"lowPrice":broken'),
            "sold out": ("3 400,00", "Нет в продаже"),
            "zero price": ("3 400,00", "0,00"),
        }
        for description, (original, replacement) in changes.items():
            with self.subTest(description=description):
                with self.assertRaises(MonitorError):
                    extract_minimum_price(self.page_html.replace(original, replacement))

    def test_duplicate_main_block_is_an_error(self):
        page_html = self.page_html.replace(
            "</body>", '<div class="ModelInfo__BlockPrice" data-low-price="1940.00"></div></body>'
        )
        with self.assertRaises(MonitorError):
            extract_minimum_price(page_html)

    def test_missing_price_never_falls_back_to_other_diagonal_or_json(self):
        page_html = self.page_html.replace("PriceBlock__PriceValue", "UnavailablePrice")
        with self.assertRaises(MonitorError):
            extract_minimum_price(page_html)


class MoneyTests(unittest.TestCase):
    def test_supported_formats(self):
        for value in ("2990.50", "2 990,50", "2\u00a0990,50", "2\u202f990.50", "2990.5"):
            with self.subTest(value=value):
                self.assertEqual(parse_money(value), Decimal("2990.50"))

    def test_invalid_amounts(self):
        for value in ("NaN", "Infinity", "-1", "0", "1e3", "2 99,00", "1.001", "", None, 2990):
            with self.subTest(value=value):
                with self.assertRaises(MonitorError):
                    parse_money(value)
