"""Extract only the visible 'от' price in the current product's main block."""

import html
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from html.parser import HTMLParser
from urllib.parse import urlsplit

from .config import MonitorError, PRODUCT_ID, PRODUCT_NAME, PRODUCT_URL, parse_money


@dataclass
class Element:
    tag: str
    attributes: dict[str, str | None] = field(default_factory=dict)
    content: list = field(default_factory=list)

    def walk(self):
        yield self
        for child in self.content:
            if isinstance(child, Element):
                yield from child.walk()

    def text(self) -> str:
        return "".join(child.text() if isinstance(child, Element) else child for child in self.content)

    def has_class(self, class_name: str) -> bool:
        return class_name in (self.attributes.get("class") or "").split()


class DocumentParser(HTMLParser):
    VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Element("document")
        self.stack = [self.root]

    def handle_starttag(self, tag, attributes):
        element = Element(tag, dict(attributes))
        self.stack[-1].content.append(element)
        if tag not in self.VOID_TAGS:
            self.stack.append(element)

    def handle_startendtag(self, tag, attributes):
        self.handle_starttag(tag, attributes)
        if tag not in self.VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].content.append(data)


def normalized_text(value: str) -> str:
    return " ".join(html.unescape(value).split())


def same_product_url(value: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed_url = urlsplit(value)
        return (
            parsed_url.scheme == "https"
            and parsed_url.netloc == "shop.by"
            and parsed_url.path == urlsplit(PRODUCT_URL).path
            and not parsed_url.query
            and not parsed_url.fragment
        )
    except ValueError:
        return False


def only_element(elements: list[Element], description: str) -> Element:
    if len(elements) != 1:
        raise MonitorError(f"Ожидался один {description}; структура страницы изменилась.")
    return elements[0]


def extract_minimum_price(page_html: str) -> Decimal:
    document_parser = DocumentParser()
    document_parser.feed(page_html)
    document_parser.close()
    elements = list(document_parser.root.walk())

    body = only_element([element for element in elements if element.tag == "body"], "body")
    heading = only_element([element for element in elements if element.tag == "h1"], "заголовок h1")
    if (
        body.attributes.get("data-model-id") != PRODUCT_ID
        or normalized_text(heading.text()) != f"Телевизор {PRODUCT_NAME}"
    ):
        raise MonitorError("Страница не соответствует нужной модели 75″ L75MB-SRU.")

    canonical = only_element(
        [element for element in elements if element.tag == "link"
         and "canonical" in (element.attributes.get("rel") or "").split()],
        "canonical URL",
    )
    if not same_product_url(canonical.attributes.get("href")):
        raise MonitorError("Canonical URL не соответствует нужному товару.")

    main_price_block = only_element(
        [element for element in elements if element.has_class("ModelInfo__BlockPrice")],
        "основной ценовой блок",
    )
    visible_price = only_element(
        [element for element in main_price_block.walk()
         if element.has_class("PriceBlock__PriceValue")],
        "элемент цены внутри основного блока",
    )
    price_match = re.fullmatch(
        r"от\s+([0-9][0-9\s]*(?:[.,][0-9]{1,2})?)\s*(?:Ђ|руб\.?|BYN)",
        normalized_text(visible_price.text()),
    )
    if price_match is None:
        raise MonitorError("Не найдена однозначная цена рядом со словом «от».")
    minimum_price = parse_money(price_match.group(1))
    attribute_price = parse_money(main_price_block.attributes.get("data-low-price"))
    if minimum_price != attribute_price:
        raise MonitorError("Видимая цена не совпадает с data-low-price; уведомление отменено.")

    # JSON-LD is a cross-check, never a fallback for a missing visible price.
    matching_products = []
    for element in elements:
        if element.tag != "script" or element.attributes.get("type") != "application/ld+json":
            continue
        try:
            structured_data = json.loads(element.text(), parse_float=Decimal)
        except (ValueError, TypeError):
            raise MonitorError("Некорректный JSON-LD на странице товара.") from None
        top_level_items = structured_data if isinstance(structured_data, list) else [structured_data]
        candidates = list(top_level_items)
        for candidate in top_level_items:
            if isinstance(candidate, dict) and "@graph" in candidate:
                graph = candidate["@graph"]
                if not isinstance(graph, list):
                    raise MonitorError("Неизвестная структура JSON-LD.")
                candidates.extend(graph)
        # Read the top-level objects and the site's @graph, without other products.
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("@type") == "Product":
                if str(candidate.get("sku")) == PRODUCT_ID and same_product_url(candidate.get("url")):
                    matching_products.append(candidate)
    if len(matching_products) != 1:
        raise MonitorError("Не найдены однозначные структурированные данные нужного товара.")
    product = matching_products[0]
    if not isinstance(product.get("name"), str) or normalized_text(product["name"]) != f"Телевизор {PRODUCT_NAME}":
        raise MonitorError("Название товара в JSON-LD не соответствует нужной модели.")
    offers = product.get("offers")
    if not isinstance(offers, dict) or offers.get("priceCurrency") != "BYN":
        raise MonitorError("Не подтверждена валюта BYN в данных товара.")
    if offers.get("@type") != "AggregateOffer":
        raise MonitorError("Не найдена сводная минимальная цена нужного товара.")
    structured_price = parse_money(str(offers.get("lowPrice")))
    if structured_price != minimum_price:
        raise MonitorError("Видимая цена и JSON-LD расходятся; уведомление отменено.")
    return minimum_price
