# SPDX-License-Identifier: GPL-3.0-or-later
#
# This file is part of Nominatim. (https://nominatim.org)
#
# Copyright (C) 2025 by the Nominatim developer community.
# For a full list of authors see the git log.
"""
Tests for command line interface wrapper for refresk command.
"""
import pytest

import nominatim_db.tools.refresh
import nominatim_db.tools.postcodes
import nominatim_db.indexer.indexer


class TestRefresh:

    @pytest.fixture(autouse=True)
    def setup_cli_call(self, cli_call, temp_db, cli_tokenizer_mock):
        self.call_nominatim = cli_call
        self.tokenizer_mock = cli_tokenizer_mock

    @pytest.mark.parametrize("command,func", [
                             ('address-levels', 'load_address_levels_from_config'),
                             ('wiki-data', 'import_wikipedia_articles'),
                             ('importance', 'recompute_importance'),
                             ])
    def test_refresh_command(self, mock_func_factory, command, func):
        mock_func_factory(nominatim_db.tools.refresh, 'create_functions')
        func_mock = mock_func_factory(nominatim_db.tools.refresh, func)

        assert self.call_nominatim('refresh', '--' + command) == 0
        assert func_mock.called == 1

    def test_refresh_word_count(self):
        assert self.call_nominatim('refresh', '--word-count') == 0
        assert self.tokenizer_mock.update_statistics_called

    def test_refresh_word_tokens(self):
        assert self.call_nominatim('refresh', '--word-tokens') == 0
        assert self.tokenizer_mock.update_word_tokens_called

    def test_refresh_postcodes(self, async_mock_func_factory, mock_func_factory, place_table):
        func_mock = mock_func_factory(nominatim_db.tools.postcodes, 'update_postcodes')
        idx_mock = async_mock_func_factory(nominatim_db.indexer.indexer.Indexer, 'index_postcodes')

        assert self.call_nominatim('refresh', '--postcodes') == 0
        assert func_mock.called == 1
        assert idx_mock.called == 1

    def test_refresh_postcodes_no_place_table(self):
        # Do nothing without the place table
        assert self.call_nominatim('refresh', '--postcodes') == 0

    def test_refresh_create_functions(self, mock_func_factory):
        func_mock = mock_func_factory(nominatim_db.tools.refresh, 'create_functions')

        assert self.call_nominatim('refresh', '--functions') == 0
        assert func_mock.called == 1
        assert self.tokenizer_mock.update_sql_functions_called

    def test_refresh_wikidata_file_not_found(self, monkeypatch):
        monkeypatch.setenv('NOMINATIM_WIKIPEDIA_DATA_PATH', 'gjoiergjeroi345Q')

        assert self.call_nominatim('refresh', '--wiki-data') == 1

    def test_refresh_secondary_importance_file_not_found(self):
        assert self.call_nominatim('refresh', '--secondary-importance') == 1

    def test_refresh_secondary_importance_new_table(self, mock_func_factory):
        mocks = [mock_func_factory(nominatim_db.tools.refresh, 'import_secondary_importance'),
                 mock_func_factory(nominatim_db.tools.refresh, 'create_functions')]

        assert self.call_nominatim('refresh', '--secondary-importance') == 0
        assert mocks[0].called == 1
        assert mocks[1].called == 1

    def test_refresh_importance_computed_after_wiki_import(self, monkeypatch, mock_func_factory):
        calls = []
        monkeypatch.setattr(nominatim_db.tools.refresh, 'import_wikipedia_articles',
                            lambda *args, **kwargs: calls.append('import') or 0)
        monkeypatch.setattr(nominatim_db.tools.refresh, 'recompute_importance',
                            lambda *args, **kwargs: calls.append('update'))
        func_mock = mock_func_factory(nominatim_db.tools.refresh, 'create_functions')

        assert self.call_nominatim('refresh', '--importance', '--wiki-data') == 0

        assert calls == ['import', 'update']
        assert func_mock.called == 1

    @pytest.mark.parametrize('params', [('--data-object', 'w234'),
                                        ('--data-object', 'N23', '--data-object', 'N24'),
                                        ('--data-area', 'R7723'),
                                        ('--data-area', 'r7723', '--data-area', 'r2'),
                                        ('--data-area', 'R9284425',
                                         '--data-object', 'n1234567894567')])
    def test_refresh_objects(self, params, mock_func_factory):
        func_mock = mock_func_factory(nominatim_db.tools.refresh, 'invalidate_osm_object')

        assert self.call_nominatim('refresh', *params) == 0

        assert func_mock.called == len(params)/2

    @pytest.mark.parametrize('func', ('--data-object', '--data-area'))
    @pytest.mark.parametrize('param', ('234', 'a55', 'R 453', 'Rel'))
    def test_refresh_objects_bad_param(self, func, param, mock_func_factory):
        func_mock = mock_func_factory(nominatim_db.tools.refresh, 'invalidate_osm_object')

        self.call_nominatim('refresh', func, param) == 1
        assert func_mock.called == 0
