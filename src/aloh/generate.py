"""Generate fake order volumes and prices."""

from dataclasses import dataclass
from random import choice, uniform
from typing import List

__all__ = ["Price", "Volume", "generate_orders"]


def rounds(x, step=1):
    """Округление, обычно до 5 или 10. Используется для выравнивания объема заказа."""
    return round(x / step, 0) * step


@dataclass
class Price:
    """Параметры распределения цены."""

    mean: float
    delta: float

    def generate(self):
        p = uniform(self.mean - self.delta, self.mean + self.delta)
        return round(p, 1)


@dataclass
class Volume:
    """Параметры распределения объема."""

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
            xs.append(x)
            break
        elif remaining > 0:
            xs.append(x)
        else:
            # добавить небольшой остаток, котрый выведет
            # сумму *хs* на величину *total_volume*
            xs.append(total_volume - sum(xs))
            break
    return xs


def generate_day(n_days: int) -> int:
    return choice(range(n_days))


def generate_orders(n_days: int, total_volume: float, pricer: Price, sizer: Volume):
    """Создать гипотетический список заказов."""
    days = list(range(n_days))
    sim_volumes = generate_volumes(total_volume, sizer)
    n = len(sim_volumes)
    sim_days = [choice(days) for _ in range(n)]
    sim_prices = [pricer.generate() for _ in range(n)]
    res = [elem(d, v, p) for (d, v, p) in zip(sim_days, sim_volumes, sim_prices)]
    return sorted(res, key=by_day_and_price)


def elem(d, v, p):
    return dict(day=d, volume=v, price=p)


def by_day_and_price(x):
    return x["day"], x["price"]
