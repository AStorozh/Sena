"""Recipes from TheMealDB (an open recipe database: ingredients with quantities, steps, cuisine, photo). Shared free key "1" for personal, non-commercial use.
Names are in English. No calories in the database: Sena computes them from the ingredients (product_lookup, food_find in Luma)."""
from sena.core.safety import external
from sena.core.tools import string, tool
from sena.world import net
from sena.world.tech import CYRILLIC

BASE = "https://www.themealdb.com/api/json/v1/1/"
HEADERS = {"User-Agent": "Sena/1.0 (personal assistant; https://github.com/AStorozh/Sena)"}
CACHE_SECONDS = 3600
STEPS = 1500


class RecipeError(net.WorldError):
    pass


def _meals(path, params=None):
    return net.as_json(net.get(BASE + path, params, HEADERS, ttl=CACHE_SECONDS, who="TheMealDB", error=RecipeError), "TheMealDB", RecipeError).get("meals") or []


def _full(m):
    ingredients = []
    for i in range(1, 21):
        name = (m.get(f"strIngredient{i}") or "").strip()
        if name:
            ingredients.append(f"{name} — {(m.get(f'strMeasure{i}') or '').strip() or 'по вкусу'}")
    steps = " ".join((m.get("strInstructions") or "").split())
    return (f"{m.get('strMeal')} ({m.get('strCategory')}, кухня: {m.get('strArea')})\nИнгредиенты: " + "; ".join(ingredients)
            + f"\nШаги: {steps[:STEPS]}{'…' if len(steps) > STEPS else ''}" + (f"\nФото: {m['strMealThumb']}" if m.get("strMealThumb") else "")
            + (f"\nВидео: {m['strYoutube']}" if m.get("strYoutube") else ""))


def recipe(name="", ingredient=""):
    """Recipe by dish name (English), by main ingredient (a list of dishes), or a random one if nothing is given."""
    name, ingredient = str(name or "").strip(), str(ingredient or "").strip()
    if CYRILLIC.search(name + ingredient):
        raise RecipeError("База рецептов на английском: переведи название блюда или ингредиент и повтори")
    if name:
        meals = _meals("search.php", {"s": name})
        if not meals:
            return f"Блюда «{name}» в базе нет. Попробуй другое название или ингредиент (ingredient=chicken)."
        more = [m["strMeal"] for m in meals[1:6]]
        return external(_full(meals[0]) + (f"\nПохожие блюда: {', '.join(more)}" if more else "") + "\n(Калорий в базе нет: посчитай по ингредиентам.)", "TheMealDB")
    if ingredient:
        meals = _meals("filter.php", {"i": ingredient.replace(" ", "_")})
        if not meals:
            return f"С ингредиентом «{ingredient}» блюд нет. Попробуй главный продукт одним словом: chicken, salmon, potato."
        return external(f"Блюда с «{ingredient}»: " + ", ".join(m["strMeal"] for m in meals[:15]) + ". Рецепт любого — recipe(name).", "TheMealDB")
    meals = _meals("random.php")
    if not meals:
        raise RecipeError("База рецептов не вернула блюдо")
    return external(_full(meals[0]) + "\n(Калорий в базе нет: посчитай по ингредиентам.)", "TheMealDB")


TOOLS = [tool("recipe", "Рецепты (TheMealDB, названия по-английски): по блюду (name: Arrabiata), по главному ингредиенту (ingredient: chicken, список блюд) или случайное, если ничего не задано. "
                        "Даёт ингредиенты с количеством и шаги. Калорий нет: чтобы узнать их, посчитай ингредиенты через product_lookup; блюдо в Luma — luma food_add.",
              {"name": string("Название блюда по-английски"), "ingredient": string("Главный ингредиент по-английски")})]
FUNCTIONS = {"recipe": recipe}
