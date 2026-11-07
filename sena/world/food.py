"""Products: composition and nutrition by barcode or name from Open Food Facts (an open database of millions of packaged products, no key needed).
The per-100g numbers are ready for Luma's product library: Sena finds the product here and puts it into Luma via luma food_add.
The data is entered by volunteers: inaccuracies are possible, treat it as a guide. Names, composition, brands are someone else's text."""
import re

from sena.core.safety import external
from sena.core.tools import string, tool
from sena.world import finance, net

PRODUCT = "https://world.openfoodfacts.org/api/v2/product/{}.json"
SEARCH = "https://world.openfoodfacts.org/cgi/search.pl"
HEADERS = {"User-Agent": "Sena/1.0 (personal assistant; https://github.com/AStorozh/Sena)"}   # Open Food Facts asks clients to identify themselves
FIELDS = "code,product_name,generic_name,brands,quantity,serving_size,serving_quantity,nutriments,nutriscore_grade,nova_group,ingredients_text,allergens_tags"
CACHE_SECONDS = 3600
FOUND = 5


class FoodError(net.WorldError):
    pass


def _explain(reply):
    if reply.status_code == 404:
        return "Такого продукта нет в Open Food Facts"
    if reply.status_code in (429, 503):
        return "Open Food Facts просит подождать: слишком много запросов"
    return None


def _num(nutriments, key):
    value = nutriments.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _line(p):
    n = p.get("nutriments") or {}
    name = (p.get("product_name") or p.get("generic_name") or "(без названия)").strip()
    head = f"{name}" + (f" ({p['brands']})" if p.get("brands") else "") + (f", {p['quantity']}" if p.get("quantity") else "") + (f", штрихкод {p['code']}" if p.get("code") else "")
    parts = [(label, _num(n, key)) for label, key in (("ккал", "energy-kcal_100g"), ("белки", "proteins_100g"), ("жиры", "fat_100g"), ("углеводы", "carbohydrates_100g"),
                                                      ("из них сахар", "sugars_100g"), ("клетчатка", "fiber_100g"), ("соль", "salt_100g"))]
    shown = [f"{label} {finance.fmt(v, 0 if label == 'ккал' else 1)}" for label, v in parts if v is not None]
    lines = [head, "На 100 г: " + (", ".join(shown) if shown else "питательность в базе не указана")]
    if p.get("serving_size"):
        lines.append(f"Порция: {p['serving_size']}")
    marks = [m for m in (f"Nutri-Score {str(p['nutriscore_grade']).upper()}" if p.get("nutriscore_grade") in list("abcde") else "",
                         f"NOVA {p['nova_group']} (степень переработки, 1 — минимальная, 4 — максимальная)" if p.get("nova_group") else "") if m]
    if marks:
        lines.append(", ".join(marks))
    if p.get("ingredients_text"):
        text = " ".join(p["ingredients_text"].split())
        lines.append("Состав: " + text[:300] + ("…" if len(text) > 300 else ""))
    allergens = [a.split(":", 1)[-1] for a in p.get("allergens_tags") or []]
    if allergens:
        lines.append("Аллергены: " + ", ".join(allergens))
    return "\n".join(lines)


def product_lookup(barcode="", query=""):
    """Composition and nutrition of a packaged product: by barcode (exact) or by name (up to five matches)."""
    barcode, query = re.sub(r"\D", "", str(barcode or "")), str(query or "").strip()
    if barcode:
        if not 8 <= len(barcode) <= 14:
            raise FoodError("Штрихкод — от 8 до 14 цифр (EAN или UPC)")
        data = net.as_json(net.get(PRODUCT.format(barcode), {"fields": FIELDS}, HEADERS, timeout=25, ttl=CACHE_SECONDS, who="Open Food Facts", error=FoodError, explain=_explain),
                           "Open Food Facts", FoodError)
        if data.get("status") != 1 or not data.get("product"):
            return f"Продукта со штрихкодом {barcode} нет в Open Food Facts."
        products = [{**data["product"], "code": barcode}]
    elif query:
        data = net.as_json(net.get(SEARCH, {"search_terms": query, "search_simple": 1, "action": "process", "json": 1, "page_size": FOUND, "fields": FIELDS},
                                   HEADERS, timeout=30, ttl=CACHE_SECONDS, who="Open Food Facts", error=FoodError, explain=_explain), "Open Food Facts", FoodError)
        products = data.get("products") or []
        if not products:
            return f"По «{query}» в Open Food Facts ничего нет. Попробуй название на английском или штрихкод с упаковки."
    else:
        raise FoodError("Нужен штрихкод или название продукта")
    body = "\n\n".join(f"{i}. {_line(p)}" if len(products) > 1 else _line(p) for i, p in enumerate(products, 1))
    return external(body + "\n(Данные Open Food Facts вносят пользователи: числа на 100 г ориентировочные. Для библиотеки Luma: luma food_add.)", "Open Food Facts")


TOOLS = [tool("product_lookup", "Состав и питательность упакованного продукта из Open Food Facts: калории, белки, жиры, углеводы, сахар на 100 г, порция, Nutri-Score, состав, аллергены. "
                                "По штрихкоду (точно) или по названию. Нужно, чтобы узнать калорийность продукта, которого нет в Luma, и добавить его туда через luma food_add.",
              {"barcode": string("Штрихкод с упаковки, 8-14 цифр"), "query": string("Название продукта, лучше с брендом: Nutella, Snickers")})]
FUNCTIONS = {"product_lookup": product_lookup}
