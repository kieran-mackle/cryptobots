import os
import sys
import time
import ccxt
import json
import click
import importlib
from art import tprint
from typing import Optional


def strategy_name_from_module_name(name: str):
    return "".join([s.capitalize() for s in name.split("_")])


def check_dir_exists(dir_path: str, create: bool = True):
    """Checks if a directory exists, and optionally creates it
    if it does not."""
    exists = False
    if not os.path.exists(dir_path):
        # Directory does not exist
        if create:
            # Create directory
            os.mkdir(dir_path)
            exists = True
    else:
        # Directory does exist
        exists = True

    return exists


def print_banner(animation: bool = False):
    click.clear()
    tprint("CryptoBots", font="tarty1")
    trail = []
    if animation:
        for i in range(81):
            time.sleep(0.05)
            char = "₿" if i % 10 == 0 else "-"
            trail.append(char)
            print("".join(trail) + "🙮", end="\r")
        print()
        subtitle = "AN AUTOTRADER PROJECT"
        bold = "\033[1m"
        reset = "\033[0m"
        spacer = "                             "
        for i in range(len(subtitle)):
            time.sleep(0.1)
            print(spacer, bold, subtitle[: i + 1], reset, end="\r")
        time.sleep(0.5)
        print()
    print()


def update_keys_config(keys_config: dict, exchange: str):
    exchange_config_key = f"CCXT:{exchange.upper()}"

    # Check for main/test net
    net_response: str = click.prompt(
        text=click.style(
            text=f"Would you like to add keys for {exchange} mainnet or testnet?",
            fg="green",
        ),
        type=click.STRING,
        default="mainnet",
    )
    net_key = "mainnet" if "main" in net_response.lower() else "testnet"

    # Check existing config
    if exchange_config_key in keys_config:
        # This exchange has been configured before
        if net_key in keys_config[exchange_config_key]:
            # This net has been configured already
            overwrite = click.prompt(
                text=click.style(
                    text=f"  You have already configured keys for {exchange} {net_key}. Would you like to continue and overwrite?",
                    fg="green",
                ),
                default=True,
            )
            if not overwrite:
                # Exit
                return keys_config

    click.echo(f"  Configuring API keys for {exchange} {net_key}.")
    api_key = click.prompt(text="    Please enter your API key")
    secret = click.prompt(text="    Please enter your API secret", hide_input=True)

    # Add exchange to config
    # TODO - ccxt options customisation
    if exchange_config_key in keys_config:
        # Append to existing
        keys_config[exchange_config_key][net_key] = {
            "api_key": api_key,
            "secret": secret,
        }
    else:
        # Create new key for this exchange
        keys_config[exchange_config_key] = {
            net_key: {
                "api_key": api_key,
                "secret": secret,
            },
        }

    return keys_config


def check_for_update():
    """Checks if there is a newer version of cryptobots available."""
    import pip
    import johnnydep.logs

    # Quieten logging
    johnnydep.logs.configure_logging(verbosity=-1)

    # Get dist
    dist = johnnydep.JohnnyDist("cryptobots")
    i, l = dist.version_installed, dist.version_latest
    if i != l:
        click.echo("A new version of cryptobots is available!")
        update = click.prompt(
            text=click.style(text="Would you like to update cryptobots?", fg="green"),
            default=True,
        )
        if update:
            pip.main(["install", "--upgrade", "cryptobots"])


def check_home_dir():
    """Check if the default cryptobots home directory exists, and if not, ask user
    for path.
    """
    # Find home directory and configure paths
    home_dir = os.path.join(os.path.expanduser("~"), ".cryptobots")
    if not os.path.exists(home_dir):
        home_dir: str = click.prompt(
            text=click.style(text="Enter cryptobots home directory", fg="green"),
        )
    return home_dir


def check_valid_env(keys_config: dict, exchange: str, env: str):
    if env is None:
        return False, ""
    environment = "live" if env.lower() == "live" else "paper"
    net = "mainnet" if environment == "live" else "testnet"
    if net in keys_config[f"CCXT:{exchange.upper()}"]:
        valid_env = True
    else:
        valid_env = False
        click.echo(
            f"{environment} mode has not been configured for {exchange}. Please "
            + "switch mode and try again, or exit and use the configure method."
        )
    return valid_env, environment


def select_exchange(
    exchange: Optional[str] = None,
    default_exchange: Optional[str] = None,
):
    # Check inputted exchange
    valid_exchange = False
    if exchange is not None:
        if exchange.lower() in ccxt.exchanges:
            valid_exchange = True

    while not valid_exchange:
        exchange: str = click.prompt(
            text=click.style(
                text="What is the name of the exchange you would like to trade on?",
                fg="green",
            ),
            type=click.STRING,
            default=default_exchange,
            prompt_suffix=" ",
        )
        if exchange.lower() in ccxt.exchanges:
            valid_exchange = True
        else:
            click.echo("Invalid exchange. Please check spelling and try again.")
    return exchange


def select_exchange_and_env(
    keys_config: dict[str, str],
    exchange: Optional[str] = None,
    mode: Optional[str] = None,
):
    """Select an exchange name and trading environment."""
    # Get default exchange name
    if len(keys_config) == 1:
        # Only one exchange configured, use this as default
        default_exchange = list(keys_config)[0].split(":")[-1]
    else:
        default_exchange = None

    # Get exchange
    exchange = select_exchange(
        exchange=exchange,
        default_exchange=default_exchange,
    )

    # Get trading environment (try from args first)
    valid_env, environment = check_valid_env(keys_config, exchange, mode)

    # Set default environment based on config
    _exchange_config = keys_config[f"CCXT:{exchange.upper()}"]
    if len(_exchange_config) == 1:
        # Only one environment configured, use this as default
        default_env = list(_exchange_config)[0]
    else:
        default_env = None

    # Complete getting trading environment
    while not valid_env:
        environment_response: str = click.prompt(
            text=click.style(
                text="Trade in live mode or test mode?",
                fg="green",
            ),
            type=click.STRING,
            default=default_env,
        )
        valid_env, environment = check_valid_env(
            keys_config, exchange, environment_response
        )

    return exchange, environment


def select_strategy(strat_configs: list[str], strategy: Optional[str] = None):
    """Select a strategy name."""
    # Get list of strategy names
    strategy_names = [
        f.split(os.sep)[-1].split(".yaml")[0] for f in strat_configs if "keys" not in f
    ]

    # Check user specified strategy
    strategy_name = None
    if strategy is not None:
        # Check strategy exists
        if strategy not in strategy_names:
            msg = f"Error: '{strategy}' strategy not found."
            click.echo(click.style(msg, fg="red"))
        else:
            # Valid strategy
            strategy_name = strategy

    while strategy_name is None:
        # Prompt user for strategy
        mapper = {}
        msg = "Select a strategy to run:\n"
        for i, name in enumerate(strategy_names):
            msg += f"  [{i+1}] {name}\n"
            mapper[i + 1] = name
        click.echo(msg)

        # Get param to update
        strategy_selection: int = click.prompt(
            text=click.style(text="Enter strategy number", fg="green"),
            type=click.INT,
        )
        strategy_name = mapper[strategy_selection]

    return strategy_name


def get_strategy_object(strategy_name: str, strategy_dir: str):
    spec = importlib.util.spec_from_file_location(
        strategy_name, os.path.join(strategy_dir, f"{strategy_name}.py")
    )
    strat_module = importlib.util.module_from_spec(spec)
    sys.modules[strategy_name] = strat_module
    spec.loader.exec_module(strat_module)
    strategy_obj_name = strategy_name_from_module_name(strategy_name)
    strategy_object = getattr(strat_module, strategy_obj_name)
    return strategy_obj_name, strategy_object


def show_strategy_params(strategy_config: dict, strategy_name: str, msg: str = None):
    param_map = {}
    EXCLUDE = [
        "name",
        "module",
        "class",
        "interval",
        "period",
        "include",
        "backtest_ready",
    ]
    INCLUDE = strategy_config.get("INCLUDE", [])
    if msg is None:
        msg = f"Parameters for {strategy_name} strategy:\n"
    for key, val in strategy_config.items():
        # Check for nested param
        if isinstance(val, dict):
            for key_j, val_j in val.items():
                msg += f"  [{len(param_map)+1}] {key_j}: {val_j}\n"
                param_map[len(param_map) + 1] = f"nested:{key}.{key_j}"
        else:
            if key.lower() == "watchlist":
                msg += f"  [{len(param_map)+1}] symbol: {val[0]}\n"
                param_map[len(param_map) + 1] = f"nested:WATCHLIST.symbol"

            elif key.lower() not in EXCLUDE or key in INCLUDE:
                msg += f"  [{len(param_map)+1}] {key}: {val}\n"
                param_map[len(param_map) + 1] = key

    click.echo(msg)

    return param_map


def configure_strategy_params(
    strategy_config: dict[str, any], param_map: dict[str, any]
):
    while True:
        param_selection: int = click.prompt(
            text=click.style(text="Enter parameter number", fg="green"),
            type=click.INT,
        )
        param_name: str = param_map[param_selection]
        parent = None
        if param_name.startswith("nested"):
            # Nested parameter
            parent, param_name = param_name.split(":")[-1].split(".")

        # Prompt for new value
        param_value = click.prompt(
            text=click.style(text=f"Enter new value for {param_name}", fg="green"),
        )

        # Update parameter
        if parent is not None:
            if parent.lower() == "watchlist":
                strategy_config[parent] = [param_value]
            else:
                strategy_config[parent][param_name] = param_value
        else:
            strategy_config[param_name] = param_value

        # Print
        click.echo(f"Updated {param_name} to {param_value}.")
        change_config = click.prompt(
            text=click.style(text="Edit another?", fg="green"),
            default=True,
        )
        if not change_config:
            break


def create_at_inputs(
    home_dir: str,
    exchange: str,
    environment: str,
    strategy_config: dict,
    strategy_name: str,
    strategy_object: object,
):
    feed = f"ccxt:{exchange}"
    instance_str = f"{strategy_name}_{strategy_config['WATCHLIST'][0].replace('/','_')}"
    configure_kwargs = {
        "verbosity": 2,
        "home_dir": home_dir,
        "feed": feed,
        "broker": f"ccxt:{exchange}",
        "environment": environment,
        "instance_str": instance_str,
    }
    strategy_kwargs = {
        "config_dict": strategy_config,
        "strategy": strategy_object,
    }

    return configure_kwargs, strategy_kwargs


def save_backtest_config(home_dir: str, filename: str, config: dict):
    # Check for strategy config directory
    strat_conf_dir = os.path.join(home_dir, "user_configurations")
    if not os.path.exists(strat_conf_dir):
        os.mkdir(strat_conf_dir)

    # Save
    fp = os.path.join(strat_conf_dir, filename)
    with open(fp, "w") as f:
        json.dump(config, f)

    return fp
