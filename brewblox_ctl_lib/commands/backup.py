"""
Saving / loading backups
"""


import json
import zipfile
from contextlib import suppress
from datetime import datetime
from os import mkdir, path
from tempfile import NamedTemporaryFile

import click
import requests
import urllib3
import yaml
from brewblox_ctl import click_helpers, sh
from brewblox_ctl.commands import http

from brewblox_ctl_lib import const, utils


@click.group(cls=click_helpers.OrderedGroup)
def cli():
    """Top-level commands"""


@cli.group()
def backup():
    """Save or load backups."""


@backup.command()
@click.option('--save-compose/--no-save-compose',
              default=True,
              help='Include docker-compose.yml in backup.')
def save(save_compose):
    """Create a backup of Brewblox settings.

    A zip archive containing JSON/YAML files is created in the ./backup/ directory.
    The archive name will include current date and time to ensure uniqueness.

    The backup is not exported to any kind of remote/cloud storage.

    To use this command in scripts, run it as `brewblox-ctl --quiet backup save`.
    Its only output to stdout will be the absolute path to the created backup.

    The command will fail if any of the Spark services could not be contacted.

    As it does not make any destructive changes to configuration,
    this command is not affected by --dry-run.

    \b
    Stored data:
    - docker-compose.yml.   (Optional)
    - Datastore databases.
    - Spark service blocks.

    \b
    NOT stored:
    - History data.

    """
    utils.check_config()
    urllib3.disable_warnings()

    file = 'backup/brewblox_backup_{}.zip'.format(datetime.now().strftime('%Y%m%d_%H%M'))
    with suppress(FileExistsError):
        mkdir(path.abspath('backup/'))

    url = utils.datastore_url()

    utils.info('Waiting for the datastore...')
    http.wait(url, info_updates=True)
    resp = requests.get(url + '/_all_dbs', verify=False)
    resp.raise_for_status()
    dbs = [v for v in resp.json() if not v.startswith('_')]

    config = utils.read_compose()
    sparks = [
        k for k, v in config['services'].items()
        if v.get('image', '').startswith('brewblox/brewblox-devcon-spark')
    ]
    zipf = zipfile.ZipFile(file, 'w', zipfile.ZIP_DEFLATED)

    if save_compose:
        utils.info('Exporting docker-compose.yml')
        zipf.write('docker-compose.yml')

    utils.info('Exporting databases: {}'.format(', '.join(dbs)))
    for db in dbs:
        resp = requests.get('{}/{}/_all_docs'.format(url, db),
                            params={'include_docs': True},
                            verify=False)
        resp.raise_for_status()
        docs = [v['doc'] for v in resp.json()['rows']]
        for d in docs:
            del d['_rev']
        zipf.writestr(db + '.datastore.json', json.dumps(docs))

    for spark in sparks:
        utils.info('Exporting Spark blocks from \'{}\''.format(spark))
        resp = requests.get('{}/{}/export_objects'.format(utils.host_url(), spark), verify=False)
        resp.raise_for_status()
        zipf.writestr(spark + '.spark.json', resp.text)

    zipf.close()
    click.echo(path.abspath(file))
    utils.info('Done!')


@backup.command()
@click.argument('archive')
@click.option('--load-compose/--no-load-compose',
              default=True,
              help='Load and write docker-compose.yml.')
@click.option('--load-datastore/--no-load-datastore',
              default=True,
              help='Load and write datastore databases.')
@click.option('--load-spark/--no-load-spark',
              default=True,
              help='Load and write Spark blocks.')
def load(archive, load_compose, load_datastore, load_spark):
    """Load and apply Brewblox settings backup.

    This function uses files generated by `brewblox-ctl backup save` as input.
    You can use the --load-XXXX options to partially load the backup.

    This does not attempt to merge data: it will overwrite current docker-compose.yml,
    datastore databases, and Spark blocks.

    Blocks on Spark services not in the backup file will not be affected.

    If dry-run is enabled, it will echo all configuration from the backup archive.

    Steps:
        - Write docker-compose.yml, run `docker-compose up`.
        - Write all datastore files found in backup.
        - Write all Spark blocks found in backup.
    """
    utils.check_config()
    utils.confirm_mode()
    urllib3.disable_warnings()

    sudo = utils.optsudo()
    host_url = utils.host_url()
    store_url = utils.datastore_url()

    zipf = zipfile.ZipFile(archive, 'r', zipfile.ZIP_DEFLATED)
    available = zipf.namelist()
    datastore_files = [v for v in available if v.endswith('.datastore.json')]
    spark_files = [v for v in available if v.endswith('.spark.json')]

    if load_compose:
        if 'docker-compose.yml' in available:
            utils.info('Writing docker-compose.yml...')
            utils.write_compose(yaml.safe_load(zipf.read('docker-compose.yml')))
            sh('{} docker-compose up -d --remove-orphans'.format(sudo))
        else:
            utils.info('docker-compose.yml file not found in backup archive')

    if load_datastore:
        if datastore_files:
            utils.info('Waiting for the datastore...')
            sh('{} http wait {}'.format(const.CLI, store_url))
        else:
            utils.info('No datastore files found in backup archive')

        for f in datastore_files:
            db = f[:-len('.datastore.json')]

            utils.info('Recreating database {}...'.format(db))
            sh('{} http delete {}/{} --allow-fail'.format(const.CLI, store_url, db))
            sh('{} http put {}/{}'.format(const.CLI, store_url, db))

            utils.info('Writing database {}...'.format(db))
            with NamedTemporaryFile('w') as tmp:
                data = {'docs': json.loads(zipf.read(f).decode())}
                utils.show_data(data)
                json.dump(data, tmp)
                tmp.flush()
                sh('{} http post {}/{}/_bulk_docs -f {}'.format(const.CLI, store_url, db, tmp.name))

    if load_spark:
        if not spark_files:
            utils.info('No Spark files found in backup archive')

        for f in spark_files:
            spark = f[:-len('.spark.json')]
            utils.info('Writing blocks to Spark service {}...'.format(spark))
            with NamedTemporaryFile('w') as tmp:
                data = json.loads(zipf.read(f).decode())
                utils.show_data(data)
                json.dump(data, tmp)
                tmp.flush()
                sh('{} http post {}/{}/import_objects -f {}'.format(const.CLI, host_url, spark, tmp.name))

    zipf.close()
    utils.info('Done!')