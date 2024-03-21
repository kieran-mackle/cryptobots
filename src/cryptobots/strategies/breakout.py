import numpy as np
from finta import TA
from typing import List
from decimal import Decimal
from autotrader import Order
from datetime import datetime
from autotrader.strategy import Strategy
from autotrader.brokers.ccxt import Broker
from autotrader.utilities import get_logger
from autotrader.brokers.trading import Position


class Breakout(Strategy):
    """Breakout trend following strategy suited for trending conditions!

    This strategy works by making an initial trade in the direction of the trend,
    determined using an exponential moving average. However, if price moves against the
    open position by a certain amount, the position direction will be reversed. This
    makes the assumption that a trend is coming up, and price will not remain within the
    bounds set by the stop loss distance. It has a similar payoff to an options straddle,
    ignoring any transactions costs.

    If you are feeling extra certain about an impending trend, you can increase the size
    multilpier, so that losses are recovered quicker.

    Settings
    ---------
    sl_distance_pc: the distance to set the stop loss at, specified as a percentage value.
    For example, to set a 1% stop loss, sl_distance_pc=1.

    tp_distance_pc: the distance to set the take profit at, also specified as a percentage.
    This is calculated even when no TP is placed - if prices crosses the theoretical TP
    price, then the SL will be updated, trailing the trend and locking in profits!

    size_multiplier: the position size multiplier each time a loss level is reached. A higher
    number will recover losses quicker, but increase the risk of liquidation.

    trend_interval: the candlestick interval to use when determining trend direction.

    trend_ema_period: the EMA period to use when determining trend direction.

    tp_after_loss: place a take profit order only when this many consecutive losses have
    been made. If tp_after_loss=0, the bot will always set a take profit. If greater than
    zero, then the bot will only place a take profit when the position is larger than the
    base size, at an amount which will reduce the position back to the base size. For
    example, if tp_after_loss=2, then a TP will only be set when there have been 2 (or more)
    losses.

    entry_value: the value of the entry trade. Set to zero to use the minimum trade size,
    or else set the dollar amount.

    loops: the number of times to repeat the strategy before exiting completely. If this is
    0, the bot will run indefinitely.
    """

    def __init__(
        self,
        parameters: dict,
        instrument: str,
        broker: Broker,
        logger_kwargs: dict,
        **kwargs,
    ) -> None:
        """Define all indicators used in the strategy."""
        self.name = "Martingale trend following strategy"
        self.instrument = instrument
        self.parameters = parameters
        self.exchange = broker

        # Get logger
        self.logger = get_logger(
            name=f"{instrument.split('/')[0]}-marti", **logger_kwargs
        )
        self.logger.info(f"Strategy instantiated to trade {instrument}.")

        # Get instrument info
        self.instrument_info = self.exchange.api.markets[self.instrument]
        self.instrument_type = self.instrument_info["type"]
        self.price_precision = Decimal(
            str(self.instrument_info["precision"]["price"])
        ).normalize()
        self.size_precision = Decimal(
            str(self.instrument_info["precision"]["amount"])
        ).normalize()

        # Unpack parameters
        self.sl_distance = Decimal(str(parameters["sl_distance_pc"])) / Decimal("100")
        self.tp_distance = Decimal(str(parameters["tp_distance_pc"])) / Decimal("100")
        self.size_multiplier = Decimal(str(parameters["size_multiplier"]))
        self.trend_ema_period = int(parameters["trend_ema_period"])
        self.trend_interval = str(parameters["trend_interval"])
        self.tp_after_loss = int(parameters["tp_after_loss"])
        self.entry_value = Decimal(str(parameters["entry_value"]))
        self.max_loops = max(0, int(parameters["loops"]))

        # Initialise state
        self.base_size = None
        self.reference_price = None
        self.last_position_direction = 0
        self.last_position_size = 0
        self.completed_loops = 0

    def generate_signal(self, dt: datetime) -> Order | List[Order] | None:
        # Get current position
        position = self.exchange.get_positions(self.instrument)

        # Check position
        if position:
            # Position held, manage it
            self.manage_position(position[self.instrument])

        else:
            # No position held yet, open new
            self.open_new_position()

    def manage_position(self, position: Position):
        # Check reference price
        if (
            self.reference_price is None
            or position.direction != self.last_position_direction
        ):
            # Set reference price by current position's entry price
            self.reference_price = Decimal(str(position.entry_price))
            self.logger.info(
                f"Reference price updated to {self.reference_price} (position change)."
            )

            # Also update position direction
            self.last_position_direction = position.direction

            # Also update position size
            self.last_position_size = abs(position.net_position)

        # Check if position has decreased (indicating successful loop)
        if abs(position.net_position) < self.last_position_size:
            # It has, increase loop count
            self.last_position_size = abs(position.net_position)
            self.completed_loops += 1
            self.logger.info(f"Completed loops updated to {self.completed_loops}.")

        # Check base size
        if self.base_size is None:
            # Not set yet; set it now
            if self.entry_value == 0:
                # Use minimum size
                self.base_size = Decimal(
                    str(self.instrument_info["limits"]["amount"]["min"])
                )

            else:
                # Infer from position
                self.base_size = (
                    abs(Decimal(str(position.net_position)))
                    / (Decimal(position.notional) / self.entry_value)
                ).quantize(self.size_precision)

        # Calculate TP price
        tp_price = (
            self.reference_price * (1 + Decimal(position.direction) * self.tp_distance)
        ).quantize(self.price_precision)

        # Check if price has crossed TP
        candles = self.exchange.get_candles(
            instrument=self.instrument, granularity="1m", count=10
        )
        ref_price = Decimal(
            str(
                candles.iloc[-1]["High"]
                if position.direction > 0
                else candles.iloc[-1]["Low"]
            )
        )
        if position.direction * (ref_price - tp_price) > 0:
            # Update reference price
            self.reference_price = tp_price
            self.logger.info(
                f"Reference price updated {self.reference_price} (TP cross)."
            )

            # Re-calcualte TP
            tp_price = (
                self.reference_price
                * (1 + Decimal(position.direction) * self.tp_distance)
            ).quantize(self.price_precision)

        # Calculate SL price
        sl_price = (
            self.reference_price * (1 - Decimal(position.direction) * self.sl_distance)
        ).quantize(self.price_precision)

        # Calculate nominal SL size based on size multiplier
        current_size = abs(Decimal(str(position.net_position)))
        sl_size = ((1 + self.size_multiplier) * current_size).quantize(
            self.size_precision
        )

        # Calculate TP size
        if self.completed_loops == self.max_loops:
            # Want to fully close position in profit now
            tp_size = current_size

            # Check if SL has been pulled into profit by the trend
            if (
                position.direction * (sl_price - Decimal(str(position.entry_price)))
                > Decimal(str(position.entry_price)) * self.tp_distance
            ):
                # Yes; set SL size to close position rather than flip
                sl_size = current_size

        else:
            # Continue trading after tp
            losses = np.ceil(
                (current_size / self.base_size).ln() / self.size_multiplier.ln()
            )
            tp_size = (
                current_size - self.base_size if losses >= self.tp_after_loss else 0
            )

        # Get current orders
        current_orders = self.exchange.get_orders(self.instrument)

        # Check if SL and TP exist already
        update_order = {"sl": True, "tp": True}
        existing_ids = {}
        for oid, order in current_orders.items():
            # Match this to SL or TP
            if order.ccxt_order["reduceOnly"]:
                # Only the TP is reduce only; this is the TP
                if not all(
                    [
                        Decimal(str(order.ccxt_order["takeProfitPrice"])) == tp_price,
                        order.direction == -position.direction,
                        order.size == tp_size,
                    ]
                ):
                    # Need to edit the order
                    existing_ids["tp"] = oid

                else:
                    # Order does not need to change
                    update_order["tp"] = False

            else:
                # This is the SL; check it is the same
                if not all(
                    [
                        Decimal(str(order.ccxt_order["triggerPrice"])) == sl_price,
                        order.direction == -position.direction,
                        order.size == sl_size,
                    ]
                ):
                    # Need to edit the order
                    existing_ids["sl"] = oid

                else:
                    # Order does not need to change
                    update_order["sl"] = False

        # Check SL
        if update_order["sl"]:
            # Create SL
            sl = Order(
                instrument=self.instrument,
                direction=-position.direction,
                size=sl_size,
                ccxt_params={
                    "triggerPrice": sl_price,
                    "triggerDirection": "above" if position.direction < 0 else "below",
                },
            )

            if existing_ids.get("sl") is not None:
                # Edit SL
                self.logger.info("Modifying stop loss order.")
                sl.order_type = "modify"
                sl.related_orders = [existing_ids.get("sl")]

            self.exchange.place_order(sl)

        # Check TP
        if tp_size > 0 and update_order["tp"]:
            # Create TP
            tp = Order(
                instrument=self.instrument,
                direction=-position.direction,
                size=tp_size,
                ccxt_params={
                    "takeProfitPrice": tp_price,
                },
            )

            if existing_ids.get("tp") is not None:
                # Edit TP
                self.logger.info("Modifying take profit order.")
                tp.order_type = "modify"
                tp.related_orders = [existing_ids.get("tp")]

            self.exchange.place_order(tp)

    def open_new_position(self):
        if self.completed_loops >= self.max_loops and self.max_loops > 0:
            # Reached max loops, cancel any outstanding orders
            self.exchange.api.cancel_all_orders(symbol=self.instrument)

            # Stop trading
            self.logger.info("Reached loop limit - shutting down.")
            return self.stop_trading()

        # Determine trend direction
        self.logger.info("Opening new position.")
        candles = self.exchange.get_candles(
            instrument=self.instrument,
            granularity=self.trend_interval,
            count=self.trend_ema_period * 3,
        )
        ema = TA.EMA(candles, self.trend_ema_period)
        direction = 1 if candles["Close"].iloc[-1] > ema.iloc[-1] else -1

        # Get mid price to determine base size
        if self.entry_value == 0:
            # Use minimum size
            self.base_size = Decimal(
                str(self.instrument_info["limits"]["amount"]["min"])
            )
        else:
            # Set using current price
            midprice = self.exchange.get_orderbook(self.instrument).midprice
            self.base_size = (self.entry_value / Decimal(str(midprice))).quantize(
                self.size_precision
            )

        # Create order
        o = Order(
            instrument=self.instrument,
            direction=direction,
            order_type="market",
            size=self.base_size,
        )
        self.exchange.place_order(o)

        # Reset/update state
        self.completed_loops += 1
        self.reference_price = None
        self.last_position_direction = 0
        self.last_position_size = self.base_size

        # Check cycle number
        if self.completed_loops == self.max_loops:
            # Enforce TP closes position
            self.tp_after_loss = 0

    @staticmethod
    def check_parameters(parameters: dict) -> bool:
        """Check the parameters defined are valid for this strategy."""
        valid = True
        reason = None

        # Check symbol is a perp
        if ":" not in parameters["WATCHLIST"][0]:
            # Not a perp
            valid = False
            reason = "this strategy is for perpetual markets only."

        return valid, reason
