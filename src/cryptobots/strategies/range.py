import logging
import numpy as np
from typing import List
from copy import deepcopy
from decimal import Decimal
from autotrader import Order
from datetime import datetime
from autotrader.strategy import Strategy
from autotrader.brokers.ccxt import Broker
from autotrader.utilities import get_logger
from autotrader.comms.notifier import Notifier


class Range(Strategy):
    """Range-bound grid trading bot.

    This is similar to the original grid bot, but can be configured by specifying the
    upper and lower bounds of the grid directly. It is also only for neutral grids (long
    and short trading about the starting price). Since this is a mean reversion strategy,
    only limit orders will be used.

    If price leaves the range of the grid, the bot will stop placing orders, but continue
    to run.

    Setttings
    ---------
    lower_price: the lower price bound of the grid.

    upper_price: the upper price bound of the grid.

    no_levels: the total number of grid levels to use.

    max_position: the maximum position size to hold in the grid at any one time. This is
    used to size the orders per grid level.
    """

    MAX_ORDERS = 16

    def __init__(
        self,
        parameters: dict,
        instrument: str,
        broker: Broker,
        notifier: Notifier,
        logger_kwargs: dict[str, any],
        *args,
        **kwargs,
    ) -> None:
        self.name = "Range-Bound Grid Strategy"
        self.parameters = parameters
        self.instrument = instrument
        self.exchange = broker

        # Get logger
        self.logger = get_logger(
            name=f"{instrument}-range",
            **logger_kwargs,
        )
        self.logger.info("Strategy instantiated.")

        # Configure instrument parameters
        self.configure_instrument()

        # Unpack parameters
        self.lower_price = Decimal(str(parameters["lower_price"]))
        self.upper_price = Decimal(str(parameters["upper_price"]))
        self.no_levels = int(2 * np.ceil(float(parameters["no_levels"]) / 2))
        self.max_position = Decimal(str(parameters["max_position"]))

        # Initialise state
        self.ref_price = (self.upper_price + self.lower_price) / 2
        self.order_size = (2 * self.max_position / self.no_levels).quantize(
            self.size_precision
        )
        self.grid_space = (
            (self.upper_price - self.lower_price) / self.no_levels
        ).quantize(self.price_precision)
        buy_prices = {
            i: self.ref_price - i * self.grid_space
            for i in range(1, int(self.no_levels / 2) + 1)
        }
        sell_prices = {
            i: self.ref_price + i * self.grid_space
            for i in range(1, int(self.no_levels / 2) + 1)
        }
        self.target_order_prices = {1: buy_prices, -1: sell_prices}
        self.logger.info(f"Order size per level: {self.order_size} units.")
        self.logger.info(
            f"Estimated profit per level: ${self.grid_space*self.order_size:,} "
            + f"(~{100*self.grid_space/self.ref_price:.2f}%)."
        )

    def generate_signal(self, dt: datetime) -> Order | List[Order] | None:
        # Initialise orders list
        new_orders = []

        # Compare the current orders to the target grid orders
        self.check_orders(new_orders=new_orders)

        return new_orders

    def configure_instrument(self):
        # Save instrument info
        self.instrument_info = self.exchange.api.markets[self.instrument]
        self.price_precision = Decimal(
            str(self.instrument_info["precision"]["price"])
        ).normalize()
        self.size_precision = Decimal(
            str(self.instrument_info["precision"]["amount"])
        ).normalize()

    def current_position(self) -> Decimal:
        """Return the current position as a signed number."""
        positions = self.exchange.get_positions(instrument=self.instrument)
        if positions:
            # Already have a position
            position = positions[self.instrument]
            return Decimal(str(position.net_position))

        else:
            # No position
            return 0

    def check_orders(self, new_orders: list[Order]):
        """Make sure that the grid orders exist by comparing the current orders
        to the target orders, adding or amending orders to the new orders list.
        """
        # Get current orders
        current_orders = self.exchange.get_orders(self.instrument)

        # Categorise current orders into buys and sells
        categorised_orders: dict[int, dict[float, Order]] = {}
        for order in current_orders.values():
            cat = categorised_orders.setdefault(order.direction, {})
            if order.order_limit_price in cat:
                # Duplicate order - cancel it
                self.logger.info(f"Cancelling duplicate order: {order}")
                self.exchange.cancel_order(order_id=order.id, symbol=order.instrument)

            else:
                # New order at this price, store it
                if not order.ccxt_order["reduceOnly"]:
                    # This is not a TP, add it
                    cat[order.order_limit_price] = order

        # Determine orders to be placed based on current price and MAX_ORDERS
        mid_price = Decimal(str(self.exchange.get_orderbook(self.instrument).midprice))

        # Calculate distance of midprice from each level of buy and sell prices
        distances = {
            d: {l: abs(mid_price - p) for l, p in ps.items()}
            for d, ps in self.target_order_prices.items()
        }
        min_d = {d: {min(v, key=v.get): min(v.values())} for d, v in distances.items()}
        dir_mins = {d: min(v.values()) for d, v in min_d.items()}

        # Determine order direction mid price is closest too
        direction = min(dir_mins, key=dir_mins.get)

        # Determine nearest level matching the direction
        nearest_level = min_d[direction].popitem()[0]

        # Calculate allowable grid ranges for each direction
        levels_per_side = int(np.ceil(self.MAX_ORDERS / 2))
        allowed_levels = {
            direction: [
                i
                for i in range(
                    nearest_level - levels_per_side, nearest_level + levels_per_side
                )
                if i > 0
            ]
        }
        allowed_levels[-direction] = [
            i for i in range(1, self.MAX_ORDERS - len(allowed_levels[direction]) + 1)
        ]

        # Replicate target_order_prices dict with only the allowable levels
        target_order_prices = {
            d: {l: p for l, p in lp.items() if l in allowed_levels[d]}
            for d, lp in self.target_order_prices.items()
        }

        # Keep track of target orders which exist
        missing_orders = deepcopy(target_order_prices)

        # Compare categorised orders against target orders
        for direction, orders in categorised_orders.items():
            # Get target prices for this trade direction (buy/sell)
            target_prices = list(target_order_prices[direction].values())

            # Check each of the existing buy/sell orders
            for price, order in orders.items():
                if Decimal(str(price)) not in target_prices:
                    # Bad order - cancel it
                    self.logger.info(f"Cancelling out-of-place order: {order}")
                    self.exchange.cancel_order(
                        order_id=order.id, symbol=order.instrument
                    )

                else:
                    # This order is in the target - mark as existing
                    # by removing from missing_orders dict
                    grid_no = target_prices.index(Decimal(str(price))) + 1
                    missing_orders[direction].pop(grid_no)

        # Calculate fills based on current position
        net_position = self.current_position()
        buy_levels_filled = max(0, np.floor(net_position / self.order_size))
        sell_levels_filled = -min(0, np.ceil(net_position / self.order_size))
        grid_levels_filled = {1: buy_levels_filled, -1: sell_levels_filled}

        # Create orders for missing levels
        for direction, orders in missing_orders.items():
            for grid_no, order_price in orders.items():
                # Check order price against current mid price
                price_valid = direction * (mid_price - order_price) > 0

                # Check current position to prevent replacing an order already filled
                level_not_filled = grid_no > grid_levels_filled[direction]

                # Check grid_no is in the allowed levels
                place_level = grid_no in allowed_levels[direction]

                # Check both conditions
                if price_valid and level_not_filled and place_level:
                    # Proceed with order
                    o = Order(
                        instrument=self.instrument,
                        direction=direction,
                        size=self.order_size,
                        order_type="limit",
                        order_limit_price=order_price,
                        take_profit=order_price + direction * self.grid_space,
                    )
                    new_orders.append(o)
                    self.logger.info(f"Added new order for grid level {grid_no}: {o}")

    @staticmethod
    def check_parameters(parameters: dict) -> bool:
        """Check the parameters defined are valid for this strategy."""
        valid = True
        reason = None
        if ":" not in parameters["WATCHLIST"][0]:
            # Not a perp
            valid = False
            reason = "this bot can only be traded on perpetual markets."

        return valid, reason
