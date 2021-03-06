"""Linear programming model for orders and production.
   This is the main module in 'aloh' package.
"""

from dataclasses import dataclass
from time import perf_counter
from typing import Dict, List

import pandas as pd
import pulp

import aloh.interface
from aloh.interface import Product
from aloh.requirements import Materials

# This is a dict of dicts that mimics a matrix.
# We need this data structure to work with pulp.
Matrix = Dict[str, Dict[int, float]]

# Matrix manipulation and helpers


def multiply(vec: Dict, mat: Matrix):
    """Vector by matrix multiplication, results in a matrix."""
    res = {}
    for p in mat.keys():
        res[p] = {}
        m = vec[p]
        for d in mat[p].keys():
            res[p][d] = m * mat[p][d]
    return res


def lp_sum(mat: Matrix):
    return pulp.lpSum([mat[p][d] for p in mat.keys() for d in mat[p].keys()])


def values(mat: Matrix):
    res = {}
    for p in mat.keys():
        res[p] = {}
        for d in mat[p].keys():
            res[p][d] = pulp.value(mat[p][d])
    return res


def values_to_list(mat: Matrix):
    res = {}
    for p in mat.keys():
        res[p] = [pulp.value(x) for x in mat[p].values()]
    return res


def as_df(mat: Matrix):
    return pd.DataFrame(values(mat))


# Orders


def make_accept_dict(order_dict):
    """Create a binary decision variable for each order."""
    accept = {}
    for p in order_dict.keys():
        accept[p] = [
            pulp.LpVariable(f"Accept_{p}_{i}", cat="Binary")
            for i, order in enumerate(order_dict[p])
        ]
    return accept


# Methods to work with (product * days) matrices


@dataclass
class Dim:
    """
    Keeps fucntions that work on matrices of dimensions
    (number of products *  number of days).
    """

    products: [str]
    days: list

    def empty_matrix(self):
        return {p: {d: 0 for d in self.days} for p in self.products}

    def make_production(self, capacities):
        """Create a decision variable for production:
        0 < prod[p][d] <  capacity[p],
        where p is product and d is day.
        """
        prod = self.empty_matrix()
        for p in self.products:
            cap = capacities[p]
            for d in self.days:
                prod[p][d] = pulp.LpVariable(f"Prod_{p}_{d}", lowBound=0, upBound=cap)
        return prod

    def make_shipment_sales(self, order_dict, accept_dict):
        """Create expressions for:
        - shipment (volume of daily off-take)
        - sales (same in dollars).
        """
        ship = self.empty_matrix()
        sales = self.empty_matrix()
        for p in self.products:
            for i, order in enumerate(order_dict[p]):
                for d in self.days:
                    if order.day == d:
                        a = accept_dict[p][i]
                        ship[p][d] += a * order.volume
                        sales[p][d] += a * order.volume * order.price
        return ship, sales

    def make_requirements(self, ship, ms: Materials):
        # 1. We are given a matrix *ship*, sized (product * day).
        # 2. For this matrix we calculate total volume
        #    of products that must be produced to fulfill the shipment.
        #
        # Assumption: all needed products will be produced on the same day.
        #
        req = self.empty_matrix()
        f = ms.requirements_factory()
        # iterate over all products
        for p in self.products:
            # get full requirements for particular product
            req_dict = f(p)
            # iterate over days
            for d in self.days:
                # transmit requirements for each product
                for p2, r in req_dict.items():
                    # if requirement is zero - do nothing
                    if r:
                        req[p2][d] += r * ship[p][d]
        return req

    def calculate_inventory(self, prod, use):
        """Create expressions for inventory.
        Inventory is end of day stock of produced, but not shipped goods.
        """
        inv = self.empty_matrix()
        for p in self.products:
            xs = prod[p]
            ys = use[p]
            for d in self.days:
                inv[p][d] = accum(xs, d) - accum(ys, d)
        return inv


def accum(var, i):
    return pulp.lpSum([var[k] for k in range(i + 1)])


def clean(s: str):
    return s.replace(" ", "_").replace(",", "_")


class OptModel:
    def __init__(
        self, products: List[Product], model_name: str, inventory_weight: float
    ):
        # model parameters
        self.inventory_weight = inventory_weight
        self.time_elapsed = 0

        #  plant and order parameters
        self.products = aloh.interface.names(products)
        self.capacities = aloh.interface.capacities(products)
        self.unit_costs = aloh.interface.unit_costs(products)
        self.order_dict = aloh.interface.order_dict(products)
        self.days = aloh.interface.days(self.order_dict)
        self.storage_days = aloh.interface.storage_days(
            products, max_allowed_storage_days=self.n_days
        )

        # LP model
        self.accept_dict = make_accept_dict(self.order_dict)
        dim = Dim(self.products, self.days)
        self.prod = dim.make_production(self.capacities)
        self.costs = multiply(self.unit_costs, self.prod)
        self.ship, self.sales = dim.make_shipment_sales(
            self.order_dict, self.accept_dict
        )
        self.ms = aloh.interface.get_materials(products)
        self.req = dim.make_requirements(self.ship, self.ms)
        self.inv = dim.calculate_inventory(self.prod, self.req)
        self.model = pulp.LpProblem(clean(model_name), pulp.LpMaximize)

    @property
    def n_days(self):
        return self.days[-1] + 1

    def set_objective(self):
        # Целевая функция
        self.model += (
            lp_sum(self.sales)
            - lp_sum(self.costs)
            - lp_sum(self.inv) * self.inventory_weight
        )

    def set_non_negative_inventory(self):
        """Ограничение: неотрицательные запасы."""
        for p in self.products:
            for d in self.days:
                self.model += (self.inv[p][d] >= 0, f"Non_negative_inventory_{p}_{d}")

    def set_closed_sum(self):
        """Ограничение: закрытая сумма, нулевые входящие и исходящие остатки."""
        for p in self.products:
            self.model += (
                pulp.lpSum(self.prod[p]) == pulp.lpSum(self.req[p]),
                f"Closed sum for {p}",
            )

    def set_storage_limit(self):
        """Ввести ограничение на срок складирования продукта."""
        for p in self.products:
            s = self.storage_days[p]
            for d in self.days:
                xs = next_use(self.ship[p], d, s)
                self.model += self.inv[p][d] <= pulp.lpSum(xs)

    def evaluate(self):
        self.set_objective()
        self.set_non_negative_inventory()
        self.set_closed_sum()
        self.set_storage_limit()
        self.solve()
        return self.accepted_orders(), self.estimated_production()

    def solve(self):
        start = perf_counter()
        self.model.solve()
        self.time_elapsed = perf_counter() - start
        print("Solved in {:.3f} sec".format(self.time_elapsed))

    def estimated_production(self):
        return values_to_list(self.prod)

    def accepted_orders(self) -> Dict[str, int]:
        return {p: [as_int(x) for x in self.accept_dict[p]] for p in self.products}

    def accepted_orders_full(self):
        return accepted_orders_full(self)

    def save(self, filename: str):
        self.model.writeLP(filename)
        print(f"Cохранили модель в файл {filename}")


def as_int(x):
    try:
        return int(x.value())
    except TypeError:
        return 0


def next_use(xs, d, s):
    """Slice *xs* list between *d* and *d+s* properly."""
    if s == 0:
        return 0
    else:
        last = len(xs) - 1
        up_to = min(last, d + s) + 1
        return [xs[t] for t in range(d + 1, up_to)]


# Data frame functions - report what is inside model


def accepted_orders_full(m: OptModel):
    def accepted_entry(p, m):
        return [
            dict(order=d.__dict__, accepted=as_int(a))
            for a, d in zip(m.accept_dict[p], m.order_dict[p])
        ]

    return {p: accepted_entry(p, m) for p in m.products}


def orders_dataframe(p: str, m: OptModel):
    df = pd.DataFrame(m.order_dict[p])
    df["accept"] = m.accepted_orders()[p]
    df.index.name = "n"
    return df


def series(var, p: str):
    return values(var)[p].values()


def product_dataframe(p: str, m: OptModel):
    df = pd.DataFrame()
    df["x"] = series(m.prod, p)
    df["ship"] = series(m.ship, p)
    df["req"] = series(m.req, p)
    df["inv"] = series(m.inv, p)
    df["sales"] = series(m.sales, p)
    df["costs"] = series(m.costs, p)
    df.index.name = "day"
    return df


def variable_dataframes(m: OptModel):
    keys = ["prod", "ship", "req", "inv", "sales", "costs"]
    return [as_df(m.__getattribute__(key)) for key in keys]


@dataclass
class DataframeViewer:
    om: OptModel

    def orders_dataframe(self, p: str):
        return orders_dataframe(p, self.om)

    def orders_dataframes(self):
        return {p: orders_dataframe(p, self.om) for p in self.om.products}

    def orders_summary(self):
        res = pd.DataFrame()
        for k, v in self.orders_dataframes().items():
            v["product"] = k
            res = pd.concat([res, v], axis=0)
        return (
            res.groupby(["day", "product"])
            .sum()
            .reset_index()
            .pivot(index="day", values="volume", columns="product")
            .fillna(0)
        )

    def product_dataframe(self, p: str):
        return product_dataframe(p, self.om)

    def product_dataframes(self):
        return {p: product_dataframe(p, self.om) for p in self.om.products}

    def inspect_variables(self):
        return variable_dataframes(self.om)

    def summary_dataframe(self):
        """Объемы мощностей, заказов, производства, покупок (тонн)

                Пример:
                            A       B
        capacity       2800.0  1400.0
        orders         3780.0  1120.0
        purchase       2280.0   400.0
        internal_use    500.0     0.0
        requirement    2780.0   400.0
        production     2780.0   400.0
        avg_inventory   174.5     4.6"""

        prod_df, ship_df, req_df, inv_df, sales_df, cost_df = self.inspect_variables()
        df = pd.DataFrame(
            {
                "orders": self.orders_summary().sum(),
                "capacity": self.om.capacities,
                "x": prod_df.sum(),
                "ship": ship_df.sum(),
                "internal_use": (req_df - ship_df).sum(),
                "requirement": req_df.sum(),
                "avg_inventory": inv_df.mean().round(1),
                "sales": sales_df.sum(),
                "costs": cost_df.sum(),
                "profit": (sales_df - cost_df).sum(),
            }
        )
        df.index.name = ""
        df["capacity"] *= self.om.n_days
        return df.T
