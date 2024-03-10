import logging
import numpy as np
from decimal import Decimal
from autotrader import Order
from datetime import datetime
from autotrader.strategy import Strategy
from autotrader.brokers.ccxt import Broker
from autotrader.utilities import get_logger
from autotrader.brokers.virtual import Broker as VirtualBroker


class Grid(Strategy):
    """Grid trading bot.

    This bot will buy and/or sell at set grid levels. When you expect market conditions
    to be choppy and sideways-moving, a long/short grid works best. In this scenario, the
    bot will buy when price dips, and sell when price rises. When you expect the market to
    be trending (up or down!), a one-directional grid works best.

    Setttings
    ---------
    Direction: this is the grid trading direction. If "1", only buy orders will be created.
    If "-1", only sell orders will be created. If "0", then both buy and sell orders will
    be created.

    Grid spacing: this is the distance between grid price levels, specified as a percentage
    number of the origin (starting) price. For example, if you specify "1", grid levels will
    be spaced 1% apart.

    Stop loss: the maximum possible loss, specified as a percentage of the maximum investment.

    Maximum investment: the maximum amount you want to allocate to the grid, in USDT.

    tp_multiplier : this is how much to increase the take profit by on subsequent entries when
    trading in trend mode. The more certain you are about a trend, the higher you can set this.
    The default is 1, which will use a constant take profit distance. If direction is 0, this
    will be overridden to be 1.

    Notes
    ------
    Please makes sure your account has enough capital in it to support the settings you have
    configured. In future versions, an automatic check will be conducted to do this for you.
    """

    def __init__(
        self, parameters: dict, instrument: str, broker: Broker, *args, **kwargs
    ) -> None:
        self.name = "Grid Bot"
        self.instrument = instrument
        self.exchange: Broker = broker

        # Get logger
        self.logger = get_logger(
            name="gridbot", stdout=False, file=True, file_level=logging.DEBUG
        )
        self.logger.info("Strategy instantiated.")

        # Check for spot vs. perp
        if isinstance(self.exchange, VirtualBroker):
            # Backtesting; mock api
            # NOTE - these will not be appropriate for all symbols
            self.instrument_info = {
                "base": self.instrument.split("/")[0],
                "type": "swap" if ":" in self.instrument else "spot",
                "precision": {"price": 1e-4, "amount": 1e-4},
            }

        else:
            # Call CCXT API to get information
            self.instrument_info = self.exchange.api.markets[self.instrument]

        # Save instrument info
        self.instrument_type = self.instrument_info["type"]
        self.price_precision = Decimal(
            str(self.instrument_info["precision"]["price"])
        ).normalize()
        self.size_precision = Decimal(
            str(self.instrument_info["precision"]["amount"])
        ).normalize()

        # Check a position isn't already open on this instrument
        net_position = self.current_position()
        self._bad_start = False
        if net_position != 0:
            self.logger.error(f"A position for {self.instrument} already exists.")
            self._bad_start = True

        # TODO - Allow setting leverage from config, and check account balance
        # for grid settings.

        # Unpack parameters
        self.grid_spacing = Decimal(str(parameters["grid_spacing"])) / 100
        self.direction = Decimal(str(np.sign(int(parameters["direction"]))))
        self.stop_loss_pc = Decimal(str(parameters["stop_loss"])) / 100
        self.max_investment = Decimal(str(parameters["max_investment"]))
        self.tp_multiplier = (
            Decimal(str(parameters["tp_multiplier"]))
            if self.direction != 0
            else Decimal("1")
        )
        self.no_grids = 2 * self.stop_loss_pc / self.grid_spacing - 1
        order_value = self.max_investment / self.no_grids
        self.logger.debug(
            f"Initialised for maximum investment of ${self.max_investment} "
            + f"and {100*self.stop_loss_pc:.2f}% stop-loss."
        )

        # Initialise
        self.reference_price = self.get_mid_price()
        self.unit_size = (order_value / self.reference_price).quantize(
            self.size_precision
        )  # conservative estimate
        self.sell_levels_filled = 0
        self.buy_levels_filled = 0
        self.logger.debug(f"Grid order size set to {self.unit_size}.")

    def get_mid_price(self):
        book = self.exchange.get_orderbook(self.instrument)
        mid_price = Decimal(str(book.midprice))
        return mid_price

    def calculate_prices(self):
        """Calculate buy and sell order prices based on number of levels filled already."""
        b1 = self.reference_price * (1 - self.grid_spacing)
        s1 = self.reference_price * (1 + self.grid_spacing)
        sell_order_price = max(
            s1,
            self.reference_price
            * (1 + (self.sell_levels_filled + 1) * self.grid_spacing),
        ).quantize(self.price_precision)
        buy_order_price = min(
            b1,
            self.reference_price
            * (1 - (self.buy_levels_filled + 1) * self.grid_spacing),
        ).quantize(self.price_precision)
        return sell_order_price, buy_order_price

    def calculate_sl(self):
        # TODO - move SL for trend
        sell_sl_price = self.reference_price * (1 + self.no_grids * self.grid_spacing)
        buy_sl_price = self.reference_price * (1 - self.no_grids * self.grid_spacing)
        return {-1: sell_sl_price, 1: buy_sl_price}

    def calculate_tp(self, price: Decimal):
        buy_tp = price * (
            1 + self.grid_spacing * self.tp_multiplier**self.buy_levels_filled
        )
        sell_tp = price * (
            1 - self.grid_spacing * self.tp_multiplier**self.sell_levels_filled
        )
        return {1: buy_tp, -1: sell_tp}

    def generate_signal(self, dt: datetime):
        # Check for bad start conditions
        if self._bad_start:
            return self.stop_trading()

        # Initialise orders
        orders = []

        # Get current net position
        net_position = self.current_position()

        # Check that position is never against grid direction (!=0)
        if (
            self.direction != 0
            and np.sign(net_position) != 0
            and np.sign(net_position) != self.direction
        ):
            self.logger.error("Position contradicts grid direction.")
            return self.stop_trading()

        # Check for initial entry
        mid_price = self.get_mid_price()
        if self.direction != 0 and net_position == 0:
            # Make initial entry to join trend
            tp_map = self.calculate_tp(mid_price)
            sl_map = self.calculate_sl()
            entry = Order(
                instrument=self.instrument,
                direction=self.direction,
                size=self.unit_size,
                take_profit=tp_map[self.direction],
                stop_loss=sl_map[self.direction],
            )

            # Update reference price
            self.reference_price = mid_price

            return entry

        # Calculate order prices based on position only
        self.buy_levels_filled = max(0, np.floor(net_position / self.unit_size))
        self.sell_levels_filled = -min(0, np.ceil(net_position / self.unit_size))
        sell_order_price, buy_order_price = self.calculate_prices()

        # Calculate sizes
        sell_order_size = self.unit_size - abs(min(0, net_position)) % self.unit_size
        buy_order_size = self.unit_size - max(0, net_position) % self.unit_size

        # Adjust based on grid direction
        if self.direction > 0:
            # Only long
            sell_order_size = 0

        elif self.direction < 0:
            # Only short
            buy_order_size = 0

        # Update orders
        current_orders = self.exchange.get_orders(self.instrument)
        if sell_order_size != 0:
            orders = self.adjust_order(
                current_orders=current_orders,
                direction=-1,
                price=sell_order_price,
                order_size=sell_order_size,
                orders=orders,
            )

        if buy_order_size != 0:
            orders = self.adjust_order(
                current_orders=current_orders,
                direction=1,
                price=buy_order_price,
                order_size=buy_order_size,
                orders=orders,
            )

        return orders

    def current_position(self) -> Decimal:
        """Return the current position as a signed number."""
        if self.instrument_type == "swap":
            # Perp
            positions = self.exchange.get_positions(instrument=self.instrument)
            if positions:
                # Already have a position
                position = positions[self.instrument]
                return Decimal(str(position.net_position))

            else:
                # No position
                return 0

        else:
            # Spot
            return Decimal(
                str(self.exchange.get_balance(instrument=self.instrument_info["base"]))
            )

    @staticmethod
    def check_parameters(parameters: dict) -> bool:
        """Check the parameters defined are valid for this strategy."""
        valid = True
        reason = None
        strategy_params = parameters["PARAMETERS"]
        if int(strategy_params["direction"]) <= 0:
            # Check symbol is for a perp
            if ":" not in parameters["WATCHLIST"][0]:
                # Not a perp
                valid = False
                reason = "short or bi-directional grids can only be traded on perpetual markets."

        # Check SL and grid spacing
        if float(strategy_params["stop_loss"]) <= 3 * float(
            strategy_params["grid_spacing"]
        ):
            valid = False
            reason = "stop loss is too tight for the specified grid spacing."

        return valid, reason

    def adjust_order(
        self,
        current_orders: dict[str, Order],
        direction: int,
        price: Decimal,
        order_size: Decimal,
        orders: list[Order],
    ):
        """Adjust the price of the existing buy or sell order."""
        # Get order matching direction
        matched_order = False
        for oid, order in current_orders.items():
            if order.direction == direction:
                # This is the order to edit; exit here
                matched_order = True
                break

        # Calculate take profit prices
        tp_map = self.calculate_tp(price)

        # Calculate stop loss prices
        sl_map = self.calculate_sl()

        # Build orders
        if matched_order:
            # Check the price of the current order
            if Decimal(str(order.order_limit_price)) != price:
                # Edit the order
                o = Order(
                    instrument=self.instrument,
                    direction=int(direction),
                    order_type="modify",
                    size=order_size,
                    order_limit_price=price,
                    take_profit=tp_map[direction],
                    stop_loss=sl_map[direction],
                    related_orders=[oid],
                )
                orders.append(o)

        else:
            # Didn't find the order - make it
            o = Order(
                instrument=self.instrument,
                direction=int(direction),
                order_type="limit",
                size=order_size,
                order_limit_price=price,
                take_profit=tp_map[direction],
                stop_loss=sl_map[direction],
            )
            orders.append(o)

        return orders
