import logging
import numpy as np
from typing import List
from decimal import Decimal
from autotrader import Order
from datetime import datetime
from autotrader.strategy import Strategy
from autotrader.brokers.ccxt import Broker
from autotrader.utilities import get_logger


class Cc(Strategy):
    """Perpetual futures cash and carry bot.

    Scan for opportunities using the 'cryptobots cash-and-carry' command!

    This bot will buy a spot token and go short on the perp market, capturing the
    funding rate paid by the perp. Note that the funding rate must be positive for
    the bot to work, as it goes long on the spot market and short on the perp.

    Setttings
    ---------
    symbol: this should be the token name. For example, "ETH" or "BTC".

    INTERVAL: this is the refresh rate of the bot.

    value: this is the value you would like each leg of the trade to be. For example,
    if value is 1000, then $1000 of the spot token will be bought, and $1000 worth of
    the perpetual will be shorted.

    slippage_pc: this is the slippage amount used to calculate order prices. For example,
    a slippage_pc of 0.05 means order prices will be calculated using 0.05% of allowable
    slippage.

    funding_pc_threshold: this is the funding rate percentage threshold to decide when to
    stop trading. If the funding rate falls below this value, the target sizes of the bot
    will be set to zero, and no position is held anymore, the bot will self destruct,
    completing the cash and carry.
    """

    def __init__(
        self, parameters: dict, instrument: str, broker: Broker, *args, **kwargs
    ) -> None:
        """Define all indicators used in the strategy."""
        self.name = "EMA Crossover Strategy"
        self.parameters = parameters
        self.exchange = broker

        # Get logger
        self.logger = get_logger(
            name=f"{instrument}-cc", stdout=False, file=True, file_level=logging.DEBUG
        )
        self.logger.info("Strategy instantiated.")

        # Unpack parameters
        self.target_value = float(self.parameters["value"])
        self.slippage_limit = float(self.parameters["slippage_pc"]) / 100
        self.funding_pc_threshold = float(self.parameters["funding_pc_threshold"]) / 100

        # Set instruments
        self.configure_instruments(instrument)

        # Check balance
        self.bad_start = False
        usdt_balance = self.exchange.get_balance("USDT")
        if usdt_balance < self.target_value:
            self.logger.error(
                "You do not have enough USDT to run this strategy with "
                + f"a ${self.target_value}USDT target value."
            )
            self.bad_start = True

        # Initialise exit flag
        self.winding_down = False

    def generate_signal(self, dt: datetime) -> Order | List[Order] | None:
        if self.bad_start:
            return self.stop_trading()

        # Check current funding rate
        funding_rate = self.exchange.api.fetch_funding_rate(symbol=self.perp)[
            "fundingRate"
        ]
        self.logger.debug(f"Funding rate is currently {funding_rate*100:.4f}%")
        if funding_rate < self.funding_pc_threshold:
            # Funding below threshold - update target sizes
            self.winding_down = True
            self.target_sizes = {self.spot: Decimal("0"), self.perp: Decimal("0")}

        # Check position
        self.check_position()

        # Check for exit flag
        if self.winding_down:
            # Get current sizes after position update
            current_sizes = self.current_position()
            if not all(current_sizes.values()):
                # No positions held, ready to stop bot
                self.stop_trading()

    def check_position(self):
        """Minimise trading impact of reaching a target position."""
        # Get current position
        current_sizes = self.current_position()

        # Compare to target positions
        deltas = {s: self.target_sizes[s] - c for s, c in current_sizes.items()}

        # Adjust deltas by amount limits
        deltas = {
            s: d if d > self.min_amounts[s] else Decimal("0") for s, d in deltas.items()
        }

        # Create orders for deltas
        for symbol, delta in deltas.items():
            if delta != 0:
                # Fire limit order (not post only)
                direction = np.sign(delta)
                book = self.exchange.get_orderbook(symbol)
                ref_price = (
                    book.bids["price"].iloc[0]
                    if direction < 0
                    else book.asks["price"].iloc[0]
                )
                price = Decimal(
                    ref_price * (1 + direction * self.slippage_limit)
                ).quantize(self.price_precision[symbol])
                order = Order(
                    instrument=symbol,
                    direction=direction,
                    size=abs(delta),
                    order_type="limit",
                    order_limit_price=price,
                )
                self.exchange.place_order(order)

    def configure_instruments(self, instrument: str):
        """Configure the perp and spot instrument symbols."""
        for symbol, info in self.exchange.api.markets.items():
            if f"{instrument}/USDT" in symbol:
                if info["spot"] and info["base"] == instrument:
                    spot = symbol
                    spot_info = info
                elif info["swap"]:
                    perp = symbol
                    perp_info = info

        # Log
        self.logger.info(f"Trading spot symbol {spot} and perpetual token {perp}.")

        # Assign
        self.token = instrument
        self.perp = perp
        self.spot = spot

        # Get instrument info for each
        self.instrument_info = {spot: spot_info, perp: perp_info}
        self.price_precision = {
            s: Decimal(str(i["precision"]["price"])).normalize()
            for s, i in self.instrument_info.items()
        }
        self.size_precision = {
            s: Decimal(str(i["precision"]["amount"])).normalize()
            for s, i in self.instrument_info.items()
        }
        self.min_amounts = {
            s: Decimal(str(i["limits"]["amount"]["min"]))
            for s, i in self.instrument_info.items()
        }

        # Calculate target position based on current price and target value
        self.target_sizes = {}
        for symbol in [self.spot, self.perp]:
            book = self.exchange.get_orderbook(symbol)
            self.target_sizes[symbol] = Decimal(
                str(self.target_value / book.midprice)
            ).quantize(self.size_precision[symbol])

        # Adjust sizes to be equal in token amount
        perp_multiplier = (
            Decimal("1")
            if self.perp.startswith(instrument)
            else Decimal(self.perp.split(instrument)[0])
        )
        min_size = min(
            [self.target_sizes[spot], perp_multiplier * self.target_sizes[perp]]
        )
        self.target_sizes = {s: min_size for s in self.target_sizes}

        # Negate perp position target
        self.target_sizes[self.perp] *= -1

    def current_position(self) -> dict[str, Decimal]:
        """Return the current perp and spot position."""
        perp_position_dict = self.exchange.get_positions(self.perp)
        perp_position = (
            Decimal(str(perp_position_dict[self.perp].net_position)).quantize(
                self.size_precision[self.perp]
            )
            if perp_position_dict
            else Decimal("0")
        )
        spot_balance = Decimal(str(self.exchange.get_balance(self.token))).quantize(
            self.size_precision[self.spot]
        )
        return {self.spot: spot_balance, self.perp: perp_position}


if __name__ == "__main__":
    from autotrader import AutoTrader

    at = AutoTrader()
    at.configure(
        verbosity=2,
        # notify=2,
        broker="ccxt:bybit",
        environment="live",
    )
    at.add_strategy("cc")
    at.run()
