import os
import json
import click
import pickle
from trogon import tui
from cryptobots._cli.utilities import check_home_dir


@tui(help="Open the textual terminal UI.")
@click.group()
def cli():
    """The cryptobots command line interface."""


@click.command()
@click.option(
    "--exchange",
    "-e",
    help="Specify the exchange to trade on.",
)
@click.option(
    "--mode",
    "-m",
    help="Specify the trading mode/environment to trade on.",
)
@click.option(
    "--strategy",
    "-s",
    help="Specify the strategy to run.",
)
@click.option(
    "--config",
    "-c",
    help="Specify the strategy configuration file.",
)
@click.option(
    "--background",
    "-b",
    help="Run the strategy in a background process.",
    default=False,
    show_default=True,
    is_flag=True,
)
@click.option(
    "--launch",
    "-l",
    help="Specify the launch file. This is used internally.",
)
def run(
    exchange: str, mode: str, strategy: str, config: str, background: bool, launch: str
):
    """Run cryptobots."""
    # Imports
    import os
    import sys
    import glob
    import subprocess
    from autotrader import AutoTrader, utilities
    from cryptobots._cli.utilities import (
        print_banner,
        select_strategy,
        select_exchange_and_env,
        get_strategy_object,
        configure_strategy_params,
        show_strategy_params,
        create_at_inputs,
    )

    # _check_for_update()

    # Find home directory and configure paths
    home_dir = check_home_dir()
    config_dir = os.path.join(home_dir, "config")
    file_dir = os.path.dirname(os.path.abspath(__file__))
    strat_config_dir = os.path.normpath(os.path.join(file_dir, "..", "config"))
    strategy_dir = os.path.normpath(os.path.join(file_dir, "..", "strategies"))
    keys_config = utilities.read_yaml(os.path.join(config_dir, "keys.yaml"))
    user_config_dir = os.path.normpath(os.path.join(home_dir, "user_configurations"))

    # Check for launch file
    ready_to_run = False
    if launch:
        # TODO - needs debugging, control verbosity and printouts.
        # Add strategy directory to path to support unpickling configuration
        sys.path.append(strategy_dir)

        # Un-pickle
        with open(launch, "rb") as f:
            launch_config = pickle.load(f)

        # Unpack
        configure_kwargs = launch_config["configure"]
        strategy_kwargs = launch_config["add_strategy"]

        # Remove launch file
        os.remove(launch)

        # Switch ready flag
        ready_to_run = True

    else:
        print_banner()

        # Get exchange to trade on
        exchange, environment = select_exchange_and_env(keys_config, exchange, mode)

        # Get strategy to run
        strat_configs = glob.glob(os.path.join(strat_config_dir, "*.yaml"))
        strategy_name = select_strategy(strat_configs, strategy)

        # Load the strategy object
        strategy_obj_name, strategy_object = get_strategy_object(
            strategy_name, strategy_dir
        )

        # Load strategy configuration
        load_config = True
        if config is not None:
            if os.path.exists(config):
                # Config found in cwd
                strategy_config = utilities.read_yaml(os.path.join(config))
                load_config = False

            elif os.path.exists(os.path.join(user_config_dir, config)):
                # Config found in user configurations dir
                strategy_config = utilities.read_yaml(
                    os.path.join(user_config_dir, config)
                )
                load_config = False

            else:
                # Did not find
                click.echo(
                    click.style(
                        f"Can not locate strategy configuration '{config}'.", fg="red"
                    )
                )

        if load_config:
            # Load default configuration
            strategy_config = utilities.read_yaml(
                os.path.join(strat_config_dir, f"{strategy_name}.yaml")
            )

        # Show strategy parameters
        param_map = show_strategy_params(strategy_config, strategy_name)

        # Prompt to change any
        change_config = click.prompt(
            text=click.style(text="Edit strategy configuration?", fg="green"),
            default=False,
        )
        if change_config:
            configure_strategy_params(strategy_config, param_map)

            # Show strategy parameters again
            param_map = show_strategy_params(
                strategy_config, strategy_name, msg="Updated strategy parameters:\n"
            )

        # Check strategy parameters
        if hasattr(strategy_object, "check_parameters"):
            valid, reason = strategy_object.check_parameters(strategy_config)
            if not valid:
                click.echo(f"Invalid strategy setup: {reason}")
                return

        # Prompt for confirmation of settings
        confirmed = click.confirm(
            text=click.style(
                text=f"Deploy {strategy_obj_name} on {exchange} in {environment} mode?",
                fg="green",
            ),
            default=True,
        )

        if confirmed:
            # Create input kwargs
            configure_kwargs, strategy_kwargs = create_at_inputs(
                home_dir=home_dir,
                exchange=exchange,
                environment=environment,
                strategy_config=strategy_config,
                strategy_name=strategy_name,
                strategy_object=strategy_object,
            )

            # Check for background mode
            if background:
                # Edit logging options
                configure_kwargs["verbosity"] = 0  # Only display errors to stdout
                configure_kwargs["logger_kwargs"] = {
                    "file": True,  # Log to file too
                    "log_dir": "logs",
                }

                # Pickle launch file configuration
                configuration = {
                    "configure": configure_kwargs,
                    "add_strategy": strategy_kwargs,
                }
                launchfile = os.path.join(home_dir, ".launch_config")
                with open(launchfile, "wb") as f:
                    pickle.dump(configuration, f)

                # Start new process to run bot
                p = subprocess.Popen(["cryptobots", "run", "--launch", launchfile])

                click.echo(f"Bot deployed as background process (PID: {p.pid}).")

            else:
                # Run autotrader now
                ready_to_run = True

        else:
            # Exit
            click.echo("Aborting.")

    if ready_to_run:
        # Run autotrader now
        os.chdir(home_dir)
        click.clear()
        at = AutoTrader()
        at.configure(**configure_kwargs)
        at.add_strategy(**strategy_kwargs)
        at.run()


@click.command()
@click.option(
    "--exchange",
    "-e",
    help="Specify the exchange to backtest on.",
)
@click.option(
    "--strategy",
    "-s",
    help="Specify the strategy to backtest.",
)
@click.option(
    "--duration",
    "-d",
    help="Specify the backtest duration.",
    default="3d",
    show_default=True,
)
@click.option(
    "--plot",
    "-p",
    help="Generate a backtest plot.",
    default=True,
    show_default=True,
    is_flag=True,
)
def backtest(exchange: str, strategy: str, duration: str, plot: bool):
    """Backtest a strategy."""
    import glob
    import pandas as pd
    from datetime import datetime
    from autotrader import AutoTrader, utilities
    from cryptobots._cli.utilities import (
        print_banner,
        select_strategy,
        select_exchange,
        get_strategy_object,
        configure_strategy_params,
        show_strategy_params,
        create_at_inputs,
        save_backtest_config,
    )

    # Build paths
    home_dir = check_home_dir()
    file_dir = os.path.dirname(os.path.abspath(__file__))
    strat_config_dir = os.path.normpath(os.path.join(file_dir, "..", "config"))
    strategy_dir = os.path.normpath(os.path.join(file_dir, "..", "strategies"))
    print_banner()

    # Get exchange to trade on
    exchange = select_exchange(exchange=exchange)

    # Get strategy to run
    strat_configs = glob.glob(os.path.join(strat_config_dir, "*.yaml"))
    strategy_name = select_strategy(strat_configs, strategy)

    # Load the strategy object
    _, strategy_object = get_strategy_object(strategy_name, strategy_dir)

    # Load strategy configuration
    strategy_config = utilities.read_yaml(
        os.path.join(strat_config_dir, f"{strategy_name}.yaml")
    )

    # Make sure strategy is backtest ready
    if not strategy_config.get("BACKTEST_READY", True):
        return click.echo("Sorry - this strategy is not backtest ready.")

    # Show strategy parameters
    param_map = show_strategy_params(strategy_config, strategy_name)

    # Prompt to change any
    change_config = click.prompt(
        text=click.style(text="Edit strategy configuration?", fg="green"),
        default=False,
    )
    if change_config:
        configure_strategy_params(strategy_config, param_map)

    # Check strategy parameters
    if hasattr(strategy_object, "check_parameters"):
        valid, reason = strategy_object.check_parameters(strategy_config)
        if not valid:
            click.echo(f"Invalid strategy setup: {reason}")
            return

    # Create input kwargs
    configure_kwargs, strategy_kwargs = create_at_inputs(
        home_dir=home_dir,
        exchange=exchange,
        environment="paper",
        strategy_config=strategy_config,
        strategy_name=strategy_name,
        strategy_object=strategy_object,
    )

    # Configure backtest period
    # TODO - support ccxt download data
    end_dt = datetime.now() - pd.Timedelta(strategy_config["INTERVAL"])
    start_dt = end_dt - pd.Timedelta(duration)

    # Run autotrader
    os.chdir(home_dir)
    at = AutoTrader()
    at.configure(**configure_kwargs, show_plot=plot)
    at.add_strategy(**strategy_kwargs)
    at.backtest(start_dt=start_dt, end_dt=end_dt)
    at.run()

    save_params = click.prompt(
        text=click.style(text="Save strategy configuration?", fg="green"),
        type=click.BOOL,
        default=False,
    )
    if save_params:
        default_filename = f"{strategy_name}.yaml"
        filename = click.prompt(
            text=click.style(text="Enter filename to save as", fg="green"),
            type=click.STRING,
            default=default_filename,
        )
        fp = save_backtest_config(home_dir, filename, strategy_config)
        click.echo(f"Saved backtest configuration to {fp}.")


@click.command()
def stop():
    """Stop running the cryptobots."""
    import glob
    from cryptobots._cli.utilities import print_banner

    home_dir = check_home_dir()
    active_dir = os.path.join(home_dir, "active_bots")
    print_banner()

    # Check active bots
    activebots_paths = glob.glob(os.path.join(active_dir, "*"))
    activebots = [p.split(os.sep)[-1] for p in activebots_paths]

    if len(activebots) > 0:
        # Print
        mapper = {}
        msg = "The following AutoTrader instances are running:\n"
        for i, name in enumerate(activebots):
            msg += f"  [{i+1}] {name}\n"
            mapper[i + 1] = name
        click.echo(msg)

        # Get bot to kill
        bot_selection: int = click.prompt(
            text=click.style(text="Enter instance number to terminate", fg="green"),
            type=click.INT,
        )
        instance = mapper[bot_selection]

        # Prompt for confirmation of settings
        confirmed = click.confirm(
            text=click.style(
                text=f"Terminate {instance}?",
                fg="green",
            ),
            default=True,
        )
        if confirmed:
            os.remove(os.path.join(active_dir, instance))
        click.echo("Termination signal sent.")
    else:
        click.echo("No bots appear to be deployed.")


@click.command()
def configure():
    """Configure the cryptobots environment."""
    import ccxt
    import autotrader
    from cryptobots._cli.utilities import (
        check_dir_exists,
        print_banner,
        update_keys_config,
    )

    home_dir: str = click.prompt(
        text=click.style(text="Enter cryptobots home directory", fg="green"),
        default=os.path.join(os.path.expanduser("~"), ".cryptobots"),
    )
    init_file = os.path.join(home_dir, ".initialised")

    # Ensure the directory exists
    if not os.path.isdir(home_dir):
        os.mkdir(home_dir)

    # Print banner
    _show_animation = False if os.path.exists(init_file) else True
    print_banner(_show_animation)

    # Check for config directory
    config_dir = os.path.join(home_dir, "config")
    check_dir_exists(config_dir, create=True)

    # Ask to initialise keys
    configure_keys = click.prompt(
        text=click.style(
            text="Are you ready to configure your exchange API keys?", fg="green"
        ),
        default=True,
    )
    if configure_keys:
        # Look for keys file
        keys_filepath = os.path.join(home_dir, "config", "keys.yaml")
        if os.path.exists(keys_filepath):
            # File already exists, load it
            keys_config = autotrader.utilities.read_yaml(keys_filepath)
        else:
            # Create new file
            keys_config = {}

        # Add keys for exchanges
        click.echo("Configuring API keys...")
        while True:
            valid_exchange = False
            while not valid_exchange:
                exchange: str = click.prompt(
                    text=click.style(
                        text="What is the name of the exchange you would "
                        + "like to configure?",
                        fg="green",
                    ),
                    type=click.STRING,
                )
                if exchange.lower() not in ccxt.exchanges:
                    click.echo("Invalid exchange. Please check spelling and try again.")
                valid_exchange = True

            keys_config = update_keys_config(keys_config, exchange)

            # Continue
            repeat = click.prompt(
                text=click.style(
                    text="Would you like to configure another exchange?", fg="green"
                ),
                default=True,
            )
            if not repeat:
                break

        autotrader.utilities.write_yaml(keys_config, keys_filepath)
        click.echo(f"Done configuring keys - written to {keys_filepath}.")

    # Add init file
    with open(init_file, "w") as f:
        pass

    click.echo("Cryptobots initialised.")


@click.command()
def strategies():
    """Display the implemented strategies."""
    import sys
    import glob
    import importlib.util
    from cryptobots._cli.utilities import strategy_name_from_module_name, print_banner

    print_banner()

    # Configure paths
    file_dir = os.path.dirname(os.path.abspath(__file__))
    strat_config_dir = os.path.normpath(os.path.join(file_dir, "..", "config"))
    strategy_dir = os.path.normpath(os.path.join(file_dir, "..", "strategies"))

    # Get list of strategies
    strat_configs = glob.glob(os.path.join(strat_config_dir, "*.yaml"))
    strategy_names = [
        f.split(os.sep)[-1].split(".yaml")[0] for f in strat_configs if "keys" not in f
    ]

    # Ask if more info is wanted
    mapper = {}
    msg = "Cryptobots has the following strategies:\n"
    for i, name in enumerate(strategy_names):
        msg += f"  [{i+1}] {name}\n"
        mapper[i + 1] = name

    # Get param to update
    while True:
        click.echo(msg)
        strategy_selection: int = click.prompt(
            text=click.style(text="Enter strategy number for more info", fg="green"),
            type=click.INT,
        )
        strategy_name = mapper[strategy_selection]

        # Load the strategy object
        spec = importlib.util.spec_from_file_location(
            strategy_name, os.path.join(strategy_dir, f"{strategy_name}.py")
        )
        strat_module = importlib.util.module_from_spec(spec)
        sys.modules[strategy_name] = strat_module
        spec.loader.exec_module(strat_module)
        strategy_obj_name = strategy_name_from_module_name(strategy_name)
        strategy_object = getattr(strat_module, strategy_obj_name)
        click.echo(strategy_object.__doc__)
        click.pause()
        click.clear()


# Add commands to CLI group
cli.add_command(configure)
cli.add_command(run)
cli.add_command(stop)
cli.add_command(strategies)
cli.add_command(backtest)


if __name__ == "__main__":
    cli()
