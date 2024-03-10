import numpy as np
import pandas as pd
from autotrader import Order
from autotrader.strategy import Strategy
from autotrader.brokers.ccxt import Broker


class Twap(Strategy):
    """Time-weighted average price bot.

    This bot will buy a chunk of tokens every update period until the
    target position is reached.

    Settings
    ---------
    The bot is configured by setting the trading interval, total duration and target
    position size.

    The bot will trade at the frequency set by the trading interval, using the total
    duration and target position size to determine how much to trade each time.
    """

    def __init__(
        self, parameters: dict, instrument: str, broker: Broker, *args, **kwargs
    ) -> None:
        self.instrument = instrument
        self.exchange = broker

        # Check for spot vs. perp
        self.instrument_info = self.exchange.api.markets[self.instrument]
        self.instrument_type = self.instrument_info["type"]

        # Unpack parameters
        self.target = float(parameters["target"])
        self.interval = pd.Timedelta(parameters["granularity"])

        # Get minimum trade size
        instrument_limits = self.instrument_info["limits"]
        self.min_size = instrument_limits["amount"]["min"]

        # Get initial position to calculate trade unit size
        updates = pd.Timedelta(parameters["duration"]) / pd.Timedelta(
            parameters["granularity"]
        )
        delta = self.target - self.current_position()
        self.unit_size = max(self.min_size, delta / updates)

    def generate_signal(self, *args, **kwargs):
        # Check current position
        delta = self.target - self.current_position()
        if abs(delta) < self.min_size:
            # Finished
            return self.stop_trading()

        # Create order
        order = Order(
            instrument=self.instrument,
            direction=np.sign(delta),
            size=self.unit_size,
        )

        return order

    def current_position(self):
        if self.instrument_type == "swap":
            # Perp
            positions = self.exchange.get_positions(instrument=self.instrument)
            if positions:
                # Already have a position
                position = positions[self.instrument]
                return position.net_position

            else:
                return 0

        else:
            # Spot
            return self.exchange.get_balance(instrument=self.instrument_info["base"])


if __name__ == "__main__":
    from autotrader import AutoTrader

    at = AutoTrader()
    at.configure(
        verbosity=2,
        broker="ccxt:bybit",
        feed="ccxt:bybit",
        environment="paper",
        update_interval="5s",
    )
    at.add_strategy("twap")
    at.run()
