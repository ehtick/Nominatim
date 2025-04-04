# SPDX-License-Identifier: GPL-3.0-or-later
#
# This file is part of Nominatim. (https://nominatim.org)
#
# Copyright (C) 2024 by the Nominatim developer community.
# For a full list of authors see the git log.
"""
Implementation of the 'import' subcommand.
"""
from typing import Optional
import argparse
import logging
from pathlib import Path
import asyncio

import psutil

from ..errors import UsageError
from ..config import Configuration
from ..db.connection import connect
from ..db import status, properties
from ..tokenizer.base import AbstractTokenizer
from ..version import NOMINATIM_VERSION
from .args import NominatimArgs


LOG = logging.getLogger()


class SetupAll:
    """\
    Create a new Nominatim database from an OSM file.

    This sub-command sets up a new Nominatim database from scratch starting
    with creating a new database in Postgresql. The user running this command
    needs superuser rights on the database.
    """

    def add_args(self, parser: argparse.ArgumentParser) -> None:
        group1 = parser.add_argument_group('Required arguments')
        group1.add_argument('--osm-file', metavar='FILE', action='append',
                            help='OSM file to be imported'
                                 ' (repeat for importing multiple files)',
                            default=None)
        group1.add_argument('--continue', dest='continue_at',
                            choices=['import-from-file', 'load-data', 'indexing', 'db-postprocess'],
                            help='Continue an import that was interrupted',
                            default=None)
        group2 = parser.add_argument_group('Optional arguments')
        group2.add_argument('--osm2pgsql-cache', metavar='SIZE', type=int,
                            help='Size of cache to be used by osm2pgsql (in MB)')
        group2.add_argument('--reverse-only', action='store_true',
                            help='Do not create tables and indexes for searching')
        group2.add_argument('--no-partitions', action='store_true',
                            help="Do not partition search indices "
                                 "(speeds up import of single country extracts)")
        group2.add_argument('--no-updates', action='store_true',
                            help="Do not keep tables that are only needed for "
                                 "updating the database later")
        group2.add_argument('--offline', action='store_true',
                            help="Do not attempt to load any additional data from the internet")
        group3 = parser.add_argument_group('Expert options')
        group3.add_argument('--ignore-errors', action='store_true',
                            help='Continue import even when errors in SQL are present')
        group3.add_argument('--index-noanalyse', action='store_true',
                            help='Do not perform analyse operations during index (expert only)')
        group3.add_argument('--prepare-database', action='store_true',
                            help='Create the database but do not import any data')

    def run(self, args: NominatimArgs) -> int:
        if args.osm_file is None and args.continue_at is None and not args.prepare_database:
            raise UsageError("No input files (use --osm-file).")

        if args.osm_file is not None and args.continue_at not in ('import-from-file', None):
            raise UsageError(f"Cannot use --continue {args.continue_at} and --osm-file together.")

        if args.continue_at is not None and args.prepare_database:
            raise UsageError(
                "Cannot use --continue and --prepare-database together."
            )

        return asyncio.run(self.async_run(args))

    async def async_run(self, args: NominatimArgs) -> int:
        from ..data import country_info
        from ..tools import database_import, postcodes, freeze
        from ..indexer.indexer import Indexer

        num_threads = args.threads or psutil.cpu_count() or 1
        country_info.setup_country_config(args.config)

        if args.prepare_database or args.continue_at is None:
            LOG.warning('Creating database')
            database_import.setup_database_skeleton(args.config.get_libpq_dsn(),
                                                    rouser=args.config.DATABASE_WEBUSER)
            if args.prepare_database:
                return 0

        if args.continue_at in (None, 'import-from-file'):
            self._base_import(args)

        if args.continue_at in ('import-from-file', 'load-data', None):
            LOG.warning('Initialise tables')
            with connect(args.config.get_libpq_dsn()) as conn:
                database_import.truncate_data_tables(conn)

            LOG.warning('Load data into placex table')
            await database_import.load_data(args.config.get_libpq_dsn(), num_threads)

        LOG.warning("Setting up tokenizer")
        tokenizer = self._get_tokenizer(args.continue_at, args.config)

        if args.continue_at in ('import-from-file', 'load-data', None):
            LOG.warning('Calculate postcodes')
            postcodes.update_postcodes(args.config.get_libpq_dsn(),
                                       args.project_dir, tokenizer)

        if args.continue_at in ('import-from-file', 'load-data', 'indexing', None):
            LOG.warning('Indexing places')
            indexer = Indexer(args.config.get_libpq_dsn(), tokenizer, num_threads)
            await indexer.index_full(analyse=not args.index_noanalyse)

        LOG.warning('Post-process tables')
        with connect(args.config.get_libpq_dsn()) as conn:
            conn.autocommit = True
            await database_import.create_search_indices(conn, args.config,
                                                        drop=args.no_updates,
                                                        threads=num_threads)
            LOG.warning('Create search index for default country names.')
            conn.autocommit = False
            country_info.create_country_names(conn, tokenizer,
                                              args.config.get_str_list('LANGUAGES'))
            if args.no_updates:
                conn.autocommit = True
                freeze.drop_update_tables(conn)
        tokenizer.finalize_import(args.config)

        LOG.warning('Recompute word counts')
        tokenizer.update_statistics(args.config, threads=num_threads)

        self._finalize_database(args.config.get_libpq_dsn(), args.offline)

        return 0

    def _base_import(self, args: NominatimArgs) -> None:
        from ..tools import database_import, refresh
        from ..data import country_info

        files = args.get_osm_file_list()
        if not files:
            raise UsageError("No input files (use --osm-file).")

        if args.continue_at in ('import-from-file', None):
            # Check if the correct plugins are installed
            database_import.check_existing_database_plugins(args.config.get_libpq_dsn())
            LOG.warning('Setting up country tables')
            country_info.setup_country_tables(args.config.get_libpq_dsn(),
                                              args.config.lib_dir.data,
                                              args.no_partitions)

            LOG.warning('Importing OSM data file')
            database_import.import_osm_data(files,
                                            args.osm2pgsql_options(0, 1),
                                            drop=args.no_updates,
                                            ignore_errors=args.ignore_errors)

            LOG.warning('Importing wikipedia importance data')
            data_path = Path(args.config.WIKIPEDIA_DATA_PATH or args.project_dir)
            if refresh.import_wikipedia_articles(args.config.get_libpq_dsn(),
                                                 data_path) > 0:
                LOG.error('Wikipedia importance dump file not found. '
                          'Calculating importance values of locations will not '
                          'use Wikipedia importance data.')

            LOG.warning('Importing secondary importance raster data')
            if refresh.import_secondary_importance(args.config.get_libpq_dsn(),
                                                   args.project_dir) != 0:
                LOG.error('Secondary importance file not imported. '
                          'Falling back to default ranking.')

            self._setup_tables(args.config, args.reverse_only)

    def _setup_tables(self, config: Configuration, reverse_only: bool) -> None:
        """ Set up the basic database layout: tables, indexes and functions.
        """
        from ..tools import database_import, refresh

        with connect(config.get_libpq_dsn()) as conn:
            conn.autocommit = True
            LOG.warning('Create functions (1st pass)')
            refresh.create_functions(conn, config, False, False)
            LOG.warning('Create tables')
            database_import.create_tables(conn, config, reverse_only=reverse_only)
            refresh.load_address_levels_from_config(conn, config)
            LOG.warning('Create functions (2nd pass)')
            refresh.create_functions(conn, config, False, False)
            LOG.warning('Create table triggers')
            database_import.create_table_triggers(conn, config)
            LOG.warning('Create partition tables')
            database_import.create_partition_tables(conn, config)
            LOG.warning('Create functions (3rd pass)')
            refresh.create_functions(conn, config, False, False)

    def _get_tokenizer(self, continue_at: Optional[str],
                       config: Configuration) -> AbstractTokenizer:
        """ Set up a new tokenizer or load an already initialised one.
        """
        from ..tokenizer import factory as tokenizer_factory

        if continue_at in ('import-from-file', 'load-data', None):
            # (re)initialise the tokenizer data
            return tokenizer_factory.create_tokenizer(config)

        # just load the tokenizer
        return tokenizer_factory.get_tokenizer_for_db(config)

    def _finalize_database(self, dsn: str, offline: bool) -> None:
        """ Determine the database date and set the status accordingly.
        """
        with connect(dsn) as conn:
            properties.set_property(conn, 'database_version', str(NOMINATIM_VERSION))

            try:
                dbdate = status.compute_database_date(conn, offline)
                status.set_status(conn, dbdate)
                LOG.info('Database is at %s.', dbdate)
            except Exception as exc:
                LOG.error('Cannot determine date of database: %s', exc)
