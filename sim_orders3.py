"""Выбор заказов и расчетов объемов производства по продуктам.

Условия
-------

1. Химическое производство выпускает несколько продуктов, обозначенных A, B, C, D.

2. Мы выбираем временной период планирования в днях, например, 7, 10, 30 или 60 дней.

3. Мы генерируем портфель заказов по продуктам на этот период. Каждый заказ содержит:
  - день поставки продукта
  - объем поставки в тоннах
  - цену приобретения  

4. Объемы производства каждого продукта ограничены максимальным выпуском в день.

Текущая задача
--------------

Определить:
    
  1. какие заказы выбрать
  2. объем производства каждого продукта по дням

Текущие допущения
-----------------
    
  - нет ограничения по срокам хранения продуктов
  - стоимость хранения нулевая
  - емкость хранения не ограничена
  - производство продуктов не связано друг с другом
  - нулевые остатки продуктов в начале и конце периода 
  - все заказы известны в начале периода
  - заказ берется либо отклоняется, не пересматривается
  
"""
import warnings
from dataclasses import dataclass
from enum import Enum
from random import choice, uniform
from typing import Dict, List

import pandas as pd  # type: ignore
import pulp  # type: ignore

warnings.simplefilter("ignore")


class Product(Enum):
    """Виды продуктов."""

    A = "H"
    B = "H10"
    C = "TA-HSA-10"
    D = "TA-240"


# Имитация портфеля заказов


@dataclass
class Order:
    """Параметры заказа."""

    day: int
    volume: float
    price: float


OrderDict = Dict[Product, List[Order]]
CapacityDict = Dict[Product, float]
LpExpression = pulp.pulp.LpAffineExpression


def rounds(x, f=1):
    """Округление, обычно до 5 или 10. Используется для выравнивания объема заказа."""
    return round(x / f, 0) * f


@dataclass
class Price:
    mean: float
    delta: float

    def generate(self):
        p = uniform(self.mean - self.delta, self.mean + self.delta)
        return round(p, 1)


@dataclass
class Volume:
    min_order: float
    max_order: float
    round_to: float = 1.0

    def generate(self) -> float:
        x = uniform(self.min_order, self.max_order)
        return rounds(x, self.round_to)


def generate_volumes(total_volume: float, sizer: Volume) -> List[float]:
    xs = []
    remaining = total_volume
    while remaining >= 0:
        x = sizer.generate()
        remaining = remaining - x
        if remaining == 0:
            break
        if remaining > 0:
            xs.append(x)
        else:
            xs.append(total_volume - sum(xs))
    return xs


def generate_day(n_days: int) -> int:
    return choice(range(n_days))


def generate_orders(n_days: int, total_volume: float, pricer: Price, sizer: Volume):
    days = list(range(n_days))
    sim_volumes = generate_volumes(total_volume, sizer)
    n = len(sim_volumes)
    sim_days = [choice(days) for _ in range(n)]
    sim_prices = [pricer.generate() for _ in range(n)]
    return [Order(d, v, p) for (d, v, p) in zip(sim_days, sim_volumes, sim_prices)]


# Оптимизационная модель


def accumulate(var, i) -> LpExpression:
    return pulp.lpSum([var[k] for k in range(i + 1)])


class MultiProductModel:
    obj = pulp.LpMaximize

    def __init__(self, name: str, n_days: int, all_products=Product):
        self.model = pulp.LpProblem(name, self.obj)
        self.days = list(range(n_days))
        self.all_products = all_products
        # создаем нулевые выражения для покупок и запасов
        self.purchases = self._create_dict()
        self.inventory = self._create_dict()
        self.order_dict = {p: [] for p in all_products}
        # при иницилизации указываем нулевые производственные мощности
        self.production = {}
        self.set_daily_capacity({p: 0 for p in all_products})
        # не создам выражения для заказов, потому что не знаем их количесвто

    def _create_dict(self):
        return {p: [pulp.lpSum(0) for d in self.days] for p in self.all_products}

    def set_daily_capacity(self, daily_capacity: CapacityDict):
        """Создать переменные объема производства, ограничить снизу и сверху."""
        for p, cap in daily_capacity.items():
            self.production[p] = pulp.LpVariable.dict(
                f"Production_{p.name}", self.days, lowBound=0, upBound=cap
            )

    def capacities(self):
        return {
            p: [x.upBound for x in self.production[p].values()]
            for p in self.all_products
        }

    def add_orders(self, order_dict: OrderDict):
        """Добавить заказы и создать бинарные переменные (принят/не принят заказ.)"""
        self.order_dict.update(order_dict)
        self.accept_dict = {p: dict() for p in self.all_products}
        for p, orders in order_dict.items():
            order_nums = range(len(orders))
            self.accept_dict[p] = pulp.LpVariable.dicts(
                f"{p.name}_AcceptOrder", order_nums, cat="Binary"
            )
        self._init_purchases()

    def _init_purchases(self):
        """Создать выражения для величины покупок каждого товара в каждый день."""
        for p, orders in self.order_dict.items():
            accept = self.accept_dict[p]
            for d in self.days:
                daily_orders_sum = [
                    order.volume * accept[i]
                    for i, order in enumerate(orders)
                    if d == order.day
                ]
                self.purchases[p][d] = pulp.lpSum(daily_orders_sum)

    def set_non_zero_inventory(self):
        """Установить неотрицательную величину запасов. 
           Без этого требования запасы переносятся обратно во времени.
        """
        for p in self.all_products:
            prod = self.production[p]
            pur = self.purchases[p]
            for d in self.days:
                self.inventory[p][d] = accumulate(prod, d) - accumulate(pur, d)
                self.model += (
                    self.inventory[p][d] >= 0,
                    f"Non-negative inventory of {p.name} at day {d}",
                )

    def sales_items(self) -> List[LpExpression]:
        """Элементы расчета величины продаж в деньгах."""
        for p, orders in self.order_dict.items():
            accept = self.accept_dict[p]
            for i, order in enumerate(orders):
                yield order.volume * order.price * accept[i]

    def set_closed_sum(self):
        """Установить производство равным объему покупок."""
        for p in self.all_products:
            self.model += pulp.lpSum(self.production[p]) == pulp.lpSum(
                self.purchases[p]
            )

    def set_objective(self):
        self.model += pulp.lpSum(self.sales_items())

    def solve(self):
        self.feasibility = self.model.solve()

    @property
    def status(self):
        return pulp.LpStatus[self.feasibility]


# Функции для просмотра результатов


def sales_value(m):
    return pulp.lpSum(m.sales_items()).value()


def obj_value(m):
    return pulp.value(m.model.objective)


def collect(orders, days):
    acc = [0 for _ in days]
    for order in orders:
        acc[order.day] += order.volume
    return acc


def demand_dict(m):
    return {p: collect(orders, m.days) for p, orders in m.order_dict.items()}


def order_status(m, p: Product):
    res = []
    for order, status in zip(mp.order_dict[p], mp.accept_dict[p].values()):
        x = order.__dict__
        x["accepted"] = True if status.value() == 1 else False
        res.append(x)
    return sorted(res, key=lambda x: x["day"])


def evaluate(holder):
    return {p: [item.value() for item in holder[p]] for p in holder.keys()}


def evaluate_dict(holder):
    return {p: [item.value() for item in holder[p].values()] for p in holder.keys()}


if __name__ == "__main__":

    N_DAYS = 10
    print("Период планирования, дней:", N_DAYS)
    capacity_dict: CapacityDict = {Product.A: 200, Product.B: 100}
    print("\nМощности производства, тонн в день:")
    for k, v in capacity_dict.items():
        print("  ", k.name, "-", v)

    orders_a = generate_orders(
        n_days=N_DAYS,
        total_volume=1.35 * capacity_dict[Product.A] * N_DAYS,
        sizer=Volume(min_order=100, max_order=300, round_to=20),
        pricer=Price(mean=150, delta=30),
    )

    orders_b = generate_orders(
        n_days=N_DAYS,
        total_volume=0.52 * capacity_dict[Product.B] * N_DAYS,
        sizer=Volume(min_order=80, max_order=120, round_to=5),
        pricer=Price(mean=50, delta=15),
    )
    order_dict: OrderDict = {Product.A: orders_a, Product.B: orders_b}

    # Определение модели
    mp = MultiProductModel("Two products", n_days=N_DAYS, all_products=Product)
    mp.set_daily_capacity(capacity_dict)
    mp.add_orders(order_dict)
    mp.set_non_zero_inventory()
    mp.set_closed_sum()
    mp.set_objective()

    # Решение
    mp.solve()

    # Демонстрация решения
    prod = evaluate_dict(mp.production)
    pur = evaluate(mp.purchases)
    inv = evaluate(mp.inventory)
    accepted = evaluate_dict(mp.accept_dict)
    dem = demand_dict(mp)

    def df(dict_, index_name="день"):
        df = pd.DataFrame(dict_)
        df.index.name = index_name
        return df

    print("\nСтатус решения:", mp.status)
    print("\nЗаказы")
    for p in Product:
        cd = df(order_status(mp, p), "N заказа")
        if not cd.empty:
            print("\nЗаказы на продукт", p.name)
            print(cd)
    print("\nСпрос (тонн)")
    print(df(dem))
    print("\nПродажи (тонн)")
    print(df(pur))
    print("\nПроизводство (тонн)")
    print(df(prod))
    print("\nЗапасы (тонн)")
    print(df(inv))

    print("\nОбъемы мощностей, заказов, производства, покупок (тонн)\n")
    prop = df(
        dict(
            мощности=df(mp.capacities()).mean() * N_DAYS,
            спрос=df(demand_dict(mp)).sum(),
            производство=df(prod).sum(),
            продажи=df(pur).sum(),
        ),
        "",
    )
    print(prop.T)

    print()
    print("Выручка (долл.США):", sales_value(mp))
    print("Целевая функция:   ", sales_value(mp))

    # Отдельные тесты

    assert mp.production[Product.A][0].name == "Production_A_0"
    assert mp.production[Product.A][0].upBound == 200
    assert mp.production[Product.B][6].upBound == 100
    se = list(mp.sales_items())
    assert len(accepted[Product.A]) == len(orders_a)
    assert len(accepted[Product.B]) == len(orders_b)
    assert sum(prod[Product.A]) == sum(pur[Product.A])
    assert sum(prod[Product.B]) == sum(pur[Product.B])
    from pandas.testing import assert_series_equal  # type: ignore

    assert_series_equal(df(pur).sum(), df(prod).sum())

    # TODO:
    # - [ ] срок хранения
    # - [ ] связанное производство
    # - [ ] затарты на производство
    # - [ ] варианты целевых функций
    # - [ ] приблизить к ценам на фактические товары