"""
Config-dependent commands
"""


import click
from brewblox_ctl import click_helpers, utils

from brewblox_ctl_lib import (const, lib_utils, log_command, migrate_command,
                              setup_command)


@click.group(cls=click_helpers.OrderedGroup)
def cli():
    """Command collector"""


@cli.command()
def ports():
    """Update used ports"""
    utils.check_config()

    cfg = {}

    cfg[const.HTTP_PORT_KEY] = utils.select(
        'Which port do you want to use for HTTP connections?',
        '80'
    )

    cfg[const.HTTPS_PORT_KEY] = utils.select(
        'Which port do you want to use for HTTPS connections?',
        '443'
    )

    cfg[const.MDNS_PORT_KEY] = utils.select(
        'Which port do you want to use for discovering Spark controllers?',
        '5000'
    )

    shell_commands = [
        '{} -m dotenv.cli --quote never set {} {}'.format(const.PY, key, val)
        for key, val in cfg.items()
    ]

    utils.run_all(shell_commands)


@cli.command()
def setup():
    """Run first-time setup"""
    setup_command.action()


@cli.command()
def update():
    """Update services and scripts"""
    utils.check_config()
    sudo = utils.optsudo()
    shell_commands = [
        '{}docker-compose down'.format(sudo),
        '{}docker-compose pull'.format(sudo),
        'sudo {} -m pip install -U brewblox-ctl'.format(const.PY),
        *utils.lib_loading_commands(),
        '{} migrate'.format(const.CLI),
    ]

    utils.run_all(shell_commands)


@cli.command()
def migrate():
    """Update configuration files to the lastest version"""
    migrate_command.action()


@cli.command()
@click.option('--port', type=click.INT, default=8300, help='Port on which the editor is served')
def editor(port):
    """Run web-based docker-compose.yml editor"""
    utils.check_config()
    orig = lib_utils.read_file('docker-compose.yml')

    sudo = utils.optsudo()
    editor = 'brewblox/brewblox-web-editor:{}'.format(utils.docker_tag())
    editor_commands = [
        '{}docker pull {}'.format(sudo, editor),
        '{}docker run --rm --init -p "{}:8300" -v "$(pwd):/app/config" {} --hostPort {}'.format(
            sudo,
            port,
            editor,
            port
        )
    ]

    try:
        utils.run_all(editor_commands)
    except KeyboardInterrupt:
        pass

    if orig != lib_utils.read_file('docker-compose.yml') \
        and utils.confirm('Configuration changes detected. '
                          'Do you want to restart your BrewBlox services?'):
        utils.run_all([
            '{} restart'.format(const.CLI),
        ], prompt=False)


@cli.command()
def status():
    """Check system status"""
    utils.check_config()
    shell_commands = [
        'echo "Your release track is \\"${}\\""; '.format(const.RELEASE_KEY) +
        'echo "Your config version is \\"${}\\""; '.format(const.CFG_VERSION_KEY) +
        '{}docker-compose ps'.format(utils.optsudo()),
    ]
    utils.run_all(shell_commands)


@cli.command()
def log():
    """Generate and share log file for bug reports"""
    log_command.action()


@cli.command()
@click.option('--image', default='brewblox/brewblox-devcon-spark')
@click.option('--file', default='docker-compose.yml')
def list_services(image, file):
    """List all services of a specific type"""
    utils.check_config()
    services = lib_utils.list_services(image, file)
    click.echo('\n'.join(services), nl=bool(services))
