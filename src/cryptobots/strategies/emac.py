import numpy as np
import pandas as pd
from finta import TA
from decimal import Decimal
from autotrader import Order
from datetime import datetime
from autotrader.strategy import Strategy
from autotrader.brokers.ccxt import Broker
from autotrader.indicators import crossover
from autotrader.brokers.virtual import Broker as VirtualBroker


class Emac(Strategy):
    """Exponential moving average crossover strategy.

    Trades are made when the fast EMA crosses the slow EMA, with a stop-loss
    set using the ATR. An overall market trend filter EMA is also used, to filter
    out noisy trades.

    Setttings
    ---------
    direction: this is the direction to allow trading in. If it is 1, the bot
    will only open long positions. If it is -1, the bot will only open short
    positions. If it is 0, the bot will open both long and short positions.

    fast_ema: this is the period of the fast exponential moving average.

    slow_ema: this is the period of the slow exponential moving average.

    trend_ema: this is the period of the trend exponential moving average.

    trade_pc: this is the percentage value used to calculate the position size per
    trade. For example, '5' means that each trade will be worth 5% of you account
    balance.
    """

    def __init__(
        self, parameters: dict, instrument: str, broker: Broker, *args, **kwargs
    ) -> None:
        """Define all indicators used in the strategy."""
        self.name = "EMA Crossover Strategy"
        self.instrument = instrument
        self.parameters = parameters
        self.exchange = broker

        # Get instrument info
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

        # Unpack parameters
        self.slow_ema_period = int(self.parameters["slow_ema"])
        self.fast_ema_period = int(self.parameters["fast_ema"])
        self.trend_ema_period = int(self.parameters["trend_ema"])
        self.trade_pc = Decimal(str(self.parameters["trade_pc"]))
        self.atr_stop_mulitplier = float(self.parameters["atr_stop_mulitplier"])
        self.granularity = parameters["granularity"]
        self.direction = Decimal(str(np.sign(int(parameters["direction"]))))

    def create_plotting_indicators(self, data: pd.DataFrame):
        # Construct indicators dict for plotting
        self.indicators = {
            "Fast EMA": {
                "type": "MA",
                "data": TA.EMA(data, self.fast_ema_period),
            },
            "Slow EMA": {
                "type": "MA",
                "data": TA.EMA(data, self.slow_ema_period),
            },
            "Trend EMA": {
                "type": "MA",
                "data": TA.EMA(data, self.trend_ema_period),
            },
        }

    def calculate_size(self, price: Decimal):
        nav = Decimal(str(self.exchange.get_NAV()))
        return self.trade_pc * nav / 100 / price

    def generate_features(self, data: pd.DataFrame):
        """Calculates the indicators required to run the strategy."""
        # EMA's
        slow_ema = TA.EMA(data, self.slow_ema_period)
        fast_ema = TA.EMA(data, self.fast_ema_period)
        trend_ema = TA.EMA(data, self.trend_ema_period)

        # Find EMA crossovers
        crossovers = crossover(fast_ema, slow_ema)

        # ATR for stops
        atr = TA.ATR(data, 14)

        # Determine overall market trend
        trend = np.sign(data["Close"].iloc[-1] - trend_ema.iloc[-1])

        return crossovers, atr, trend

    def generate_signal(self, dt: datetime):
        """Define strategy to determine entry signals."""
        # Initialise order list
        orders = []

        # Get OHLCV data
        n = 2 * self.trend_ema_period
        data = self.exchange.get_candles(
            self.instrument, granularity=self.granularity, count=n
        )
        if len(data) < n:
            # Not eough data
            return None

        # Get current position
        net_position = self.current_position()

        # Calculate indicators
        crossovers, atr, trend = self.generate_features(data)

        # Build orders
        if trend > 0:
            # Market up trend; look for long entries
            if crossovers.iloc[-1] > 0 and self.direction >= 0:
                # Long signal
                long_entry = Order(
                    instrument=self.instrument,
                    direction=1,
                    size=self.calculate_size(Decimal(str(data["Close"].iloc[-1]))),
                    stop_loss=data["Close"].iloc[-1]
                    - self.atr_stop_mulitplier * atr.iloc[-1],
                )
                orders.append(long_entry)

            elif crossovers.iloc[-1] < 0 and net_position > 0:
                # Close signal
                close_long = Order(
                    instrument=self.instrument,
                    direction=-1,
                    size=net_position,
                )
                orders.append(close_long)

        else:
            # Market down trend; look for short entries
            if crossovers.iloc[-1] < 0 and self.direction <= 0:
                # Short signal
                short_entry = Order(
                    instrument=self.instrument,
                    direction=-1,
                    size=self.calculate_size(Decimal(str(data["Close"].iloc[-1]))),
                    stop_loss=data["Close"].iloc[-1]
                    + self.atr_stop_mulitplier * atr.iloc[-1],
                )
                orders.append(short_entry)

            elif crossovers.iloc[-1] > 0 and net_position > 0:
                # Close signal
                close_short = Order(
                    instrument=self.instrument,
                    direction=1,
                    size=net_position,
                )
                orders.append(close_short)

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
                return Decimal("0")

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
                reason = "short trades can only be made on perpetual markets."

        return valid, reason
