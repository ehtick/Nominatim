# SPDX-License-Identifier: GPL-3.0-or-later
#
# This file is part of Nominatim. (https://nominatim.org)
#
# Copyright (C) 2025 by the Nominatim developer community.
# For a full list of authors see the git log.
"""
Fixtures for BDD test steps
"""
import sys
import json
import re
from pathlib import Path

import psycopg
from psycopg import sql as pysql

# always test against the source
SRC_DIR = (Path(__file__) / '..' / '..' / '..').resolve()
sys.path.insert(0, str(SRC_DIR / 'src'))

import pytest
from pytest_bdd.parsers import re as step_parse
from pytest_bdd import given, when, then, scenario
from pytest_bdd.feature import get_features

pytest.register_assert_rewrite('utils')

from utils.api_runner import APIRunner
from utils.api_result import APIResult
from utils.checks import ResultAttr, COMPARATOR_TERMS
from utils.geometry_alias import ALIASES
from utils.grid import Grid
from utils.db import DBManager

from nominatim_db.config import Configuration
from nominatim_db.data.country_info import setup_country_config


def _strlist(inp):
    return [s.strip() for s in inp.split(',')]


def _pretty_json(inp):
    return json.dumps(inp, indent=2)


def pytest_addoption(parser, pluginmanager):
    parser.addoption('--nominatim-purge', dest='NOMINATIM_PURGE', action='store_true',
                     help='Force recreation of test databases from scratch.')
    parser.addoption('--nominatim-keep-db', dest='NOMINATIM_KEEP_DB', action='store_true',
                     help='Do not drop the database after tests are finished.')
    parser.addoption('--nominatim-api-engine', dest='NOMINATIM_API_ENGINE',
                     default='falcon',
                     help='Chose the API engine to use when sending requests.')
    parser.addoption('--nominatim-tokenizer', dest='NOMINATIM_TOKENIZER',
                     metavar='TOKENIZER',
                     help='Use the specified tokenizer for importing data into '
                          'a Nominatim database.')

    parser.addini('nominatim_test_db', default='test_nominatim',
                  help='Name of the database used for running a single test.')
    parser.addini('nominatim_api_test_db', default='test_api_nominatim',
                  help='Name of the database for storing API test data.')
    parser.addini('nominatim_template_db', default='test_template_nominatim',
                  help='Name of database used as a template for test databases.')


@pytest.fixture
def datatable():
    """ Default fixture for datatables, so that their presence can be optional.
    """
    return None


@pytest.fixture
def node_grid():
    """ Default fixture for node grids. Nothing set.
    """
    return Grid([[]], None, None)


@pytest.fixture(scope='session', autouse=True)
def setup_country_info():
    setup_country_config(Configuration(None))


@pytest.fixture(scope='session')
def template_db(pytestconfig):
    """ Create a template database containing the extensions and base data
        needed by Nominatim. Using the template instead of doing the full
        setup can speed up the tests.

        The template database will only be created if it does not exist yet
        or a purge has been explicitly requested.
    """
    dbm = DBManager(purge=pytestconfig.option.NOMINATIM_PURGE)

    template_db = pytestconfig.getini('nominatim_template_db')

    template_config = Configuration(
        None, environ={'NOMINATIM_DATABASE_DSN': f"pgsql:dbname={template_db}"})

    dbm.setup_template_db(template_config)

    return template_db


@pytest.fixture
def def_config(pytestconfig):
    dbname = pytestconfig.getini('nominatim_test_db')

    return Configuration(None,
                         environ={'NOMINATIM_DATABASE_DSN': f"pgsql:dbname={dbname}"})


@pytest.fixture
def db(template_db, pytestconfig):
    """ Set up an empty database for use with osm2pgsql.
    """
    dbm = DBManager(purge=pytestconfig.option.NOMINATIM_PURGE)

    dbname = pytestconfig.getini('nominatim_test_db')

    dbm.create_db_from_template(dbname, template_db)

    yield dbname

    if not pytestconfig.option.NOMINATIM_KEEP_DB:
        dbm.drop_db(dbname)


@pytest.fixture
def db_conn(db, def_config):
    with psycopg.connect(def_config.get_libpq_dsn()) as conn:
        info = psycopg.types.TypeInfo.fetch(conn, "hstore")
        psycopg.types.hstore.register_hstore(info, conn)
        yield conn


@when(step_parse(r'reverse geocoding (?P<lat>[\d.-]*),(?P<lon>[\d.-]*)'),
      target_fixture='nominatim_result')
def reverse_geocode_via_api(test_config_env, pytestconfig, datatable, lat, lon):
    runner = APIRunner(test_config_env, pytestconfig.option.NOMINATIM_API_ENGINE)
    api_response = runner.run_step('reverse',
                                   {'lat': float(lat), 'lon': float(lon)},
                                   datatable, 'jsonv2', {})

    assert api_response.status == 200
    assert api_response.headers['content-type'] == 'application/json; charset=utf-8'

    result = APIResult('json', 'reverse', api_response.body)
    assert result.is_simple()

    assert isinstance(result.result['lat'], str)
    assert isinstance(result.result['lon'], str)
    result.result['centroid'] = f"POINT({result.result['lon']} {result.result['lat']})"

    return result


@when(step_parse(r'reverse geocoding at node (?P<node>[\d]+)'),
      target_fixture='nominatim_result')
def reverse_geocode_via_api_and_grid(test_config_env, pytestconfig, node_grid, datatable, node):
    coords = node_grid.get(node)
    if coords is None:
        raise ValueError('Unknown node id')

    return reverse_geocode_via_api(test_config_env, pytestconfig, datatable, coords[1], coords[0])


@when(step_parse(r'geocoding(?: "(?P<query>.*)")?'),
      target_fixture='nominatim_result')
def forward_geocode_via_api(test_config_env, pytestconfig, datatable, query):
    runner = APIRunner(test_config_env, pytestconfig.option.NOMINATIM_API_ENGINE)

    params = {'addressdetails': '1'}
    if query:
        params['q'] = query

    api_response = runner.run_step('search', params, datatable, 'jsonv2', {})

    assert api_response.status == 200
    assert api_response.headers['content-type'] == 'application/json; charset=utf-8'

    result = APIResult('json', 'search', api_response.body)
    assert not result.is_simple()

    for res in result.result:
        assert isinstance(res['lat'], str)
        assert isinstance(res['lon'], str)
        res['centroid'] = f"POINT({res['lon']} {res['lat']})"

    return result


@then(step_parse(r'(?P<op>[a-z ]+) (?P<num>\d+) results? (?:are|is) returned'),
      converters={'num': int})
def check_number_of_results(nominatim_result, op, num):
    assert not nominatim_result.is_simple()
    assert COMPARATOR_TERMS[op](num, len(nominatim_result))


@then(step_parse('the result metadata contains'))
def check_metadata_for_fields(nominatim_result, datatable):
    if datatable[0] == ['param', 'value']:
        pairs = datatable[1:]
    else:
        pairs = zip(datatable[0], datatable[1])

    for k, v in pairs:
        assert ResultAttr(nominatim_result.meta, k) == v


@then(step_parse('the result metadata has no attributes (?P<attributes>.*)'),
      converters={'attributes': _strlist})
def check_metadata_for_field_presence(nominatim_result, attributes):
    assert all(a not in nominatim_result.meta for a in attributes), \
        f"Unexpectedly have one of the attributes '{attributes}' in\n" \
        f"{_pretty_json(nominatim_result.meta)}"


@then(step_parse(r'the result contains(?: in field (?P<field>\S+))?'))
def check_result_for_fields(nominatim_result, datatable, node_grid, field):
    assert nominatim_result.is_simple()

    if datatable[0] == ['param', 'value']:
        pairs = datatable[1:]
    else:
        pairs = zip(datatable[0], datatable[1])

    prefix = field + '+' if field else ''

    for k, v in pairs:
        assert ResultAttr(nominatim_result.result, prefix + k, grid=node_grid) == v


@then(step_parse('the result has attributes (?P<attributes>.*)'),
      converters={'attributes': _strlist})
def check_result_for_field_presence(nominatim_result, attributes):
    assert nominatim_result.is_simple()
    assert all(a in nominatim_result.result for a in attributes)


@then(step_parse('the result has no attributes (?P<attributes>.*)'),
      converters={'attributes': _strlist})
def check_result_for_field_absence(nominatim_result, attributes):
    assert nominatim_result.is_simple()
    assert all(a not in nominatim_result.result for a in attributes)


@then(step_parse(
    r'the result contains array field (?P<field>\S+) where element (?P<num>\d+) contains'),
      converters={'num': int})
def check_result_array_field_for_attributes(nominatim_result, datatable, field, num):
    assert nominatim_result.is_simple()

    if datatable[0] == ['param', 'value']:
        pairs = datatable[1:]
    else:
        pairs = zip(datatable[0], datatable[1])

    prefix = f"{field}+{num}+"

    for k, v in pairs:
        assert ResultAttr(nominatim_result.result, prefix + k) == v


@then(step_parse('the result set contains(?P<exact> exactly)?'))
def check_result_list_match(nominatim_result, datatable, exact):
    assert not nominatim_result.is_simple()

    result_set = set(range(len(nominatim_result.result)))

    for row in datatable[1:]:
        for idx in result_set:
            for key, value in zip(datatable[0], row):
                if ResultAttr(nominatim_result.result[idx], key) != value:
                    break
            else:
                # found a match
                result_set.remove(idx)
                break
        else:
            assert False, f"Missing data row {row}. Full response:\n{nominatim_result}"

    if exact:
        assert not [nominatim_result.result[i] for i in result_set]


@then(step_parse('all results have attributes (?P<attributes>.*)'),
      converters={'attributes': _strlist})
def check_all_results_for_field_presence(nominatim_result, attributes):
    assert not nominatim_result.is_simple()
    assert len(nominatim_result) > 0
    for res in nominatim_result.result:
        assert all(a in res for a in attributes), \
            f"Missing one of the attributes '{attributes}' in\n{_pretty_json(res)}"


@then(step_parse('all results have no attributes (?P<attributes>.*)'),
      converters={'attributes': _strlist})
def check_all_result_for_field_absence(nominatim_result, attributes):
    assert not nominatim_result.is_simple()
    assert len(nominatim_result) > 0
    for res in nominatim_result.result:
        assert all(a not in res for a in attributes), \
            f"Unexpectedly have one of the attributes '{attributes}' in\n{_pretty_json(res)}"


@then(step_parse(r'all results contain(?: in field (?P<field>\S+))?'))
def check_all_results_contain(nominatim_result, datatable, node_grid, field):
    assert not nominatim_result.is_simple()
    assert len(nominatim_result) > 0

    if datatable[0] == ['param', 'value']:
        pairs = datatable[1:]
    else:
        pairs = zip(datatable[0], datatable[1])

    prefix = field + '+' if field else ''

    for k, v in pairs:
        for r in nominatim_result.result:
            assert ResultAttr(r, prefix + k, grid=node_grid) == v


@then(step_parse(r'result (?P<num>\d+) contains(?: in field (?P<field>\S+))?'),
      converters={'num': int})
def check_specific_result_for_fields(nominatim_result, datatable, num, field):
    assert not nominatim_result.is_simple()
    assert len(nominatim_result) > num

    if datatable[0] == ['param', 'value']:
        pairs = datatable[1:]
    else:
        pairs = zip(datatable[0], datatable[1])

    prefix = field + '+' if field else ''

    for k, v in pairs:
        assert ResultAttr(nominatim_result.result[num], prefix + k) == v


@given(step_parse(r'the (?P<step>[0-9.]+ )?grid(?: with origin (?P<origin>.*))?'),
       target_fixture='node_grid')
def set_node_grid(datatable, step, origin):
    if step is not None:
        step = float(step)

    if origin:
        if ',' in origin:
            coords = origin.split(',')
            if len(coords) != 2:
                raise RuntimeError('Grid origin expects origin with x,y coordinates.')
            origin = list(map(float, coords))
        elif origin in ALIASES:
            origin = ALIASES[origin]
        else:
            raise RuntimeError('Grid origin must be either coordinate or alias.')

    return Grid(datatable, step, origin)


@then(step_parse('(?P<table>placex?) has no entry for '
                 r'(?P<osm_type>[NRW])(?P<osm_id>\d+)(?::(?P<osm_class>\S+))?'),
      converters={'osm_id': int})
def check_place_missing_lines(db_conn, table, osm_type, osm_id, osm_class):
    sql = pysql.SQL("""SELECT count(*) FROM {}
                       WHERE osm_type = %s and osm_id = %s""").format(pysql.Identifier(table))
    params = [osm_type, int(osm_id)]
    if osm_class:
        sql += pysql.SQL(' AND class = %s')
        params.append(osm_class)

    with db_conn.cursor() as cur:
        assert cur.execute(sql, params).fetchone()[0] == 0


if pytest.version_tuple >= (8, 0, 0):
    def pytest_pycollect_makemodule(module_path, parent):
        return BddTestCollector.from_parent(parent, path=module_path)


class BddTestCollector(pytest.Module):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def collect(self):
        for item in super().collect():
            yield item

        if hasattr(self.obj, 'PYTEST_BDD_SCENARIOS'):
            for path in self.obj.PYTEST_BDD_SCENARIOS:
                for feature in get_features([str(Path(self.path.parent, path).resolve())]):
                    yield FeatureFile.from_parent(self,
                                                  name=str(Path(path, feature.rel_filename)),
                                                  path=Path(feature.filename),
                                                  feature=feature)


# borrowed from pytest-bdd: src/pytest_bdd/scenario.py
def make_python_name(string: str) -> str:
    """Make python attribute name out of a given string."""
    string = re.sub(r"\W", "", string.replace(" ", "_"))
    return re.sub(r"^\d+_*", "", string).lower()


class FeatureFile(pytest.File):
    class obj:
        pass

    def __init__(self, feature, **kwargs):
        self.feature = feature
        super().__init__(**kwargs)

    def collect(self):
        for sname, sobject in self.feature.scenarios.items():
            class_name = f"L{sobject.line_number}"
            test_name = "test_" + make_python_name(sname)

            @scenario(self.feature.filename, sname)
            def _test():
                pass

            tclass = type(class_name, (),
                          {test_name: staticmethod(_test)})
            setattr(self.obj, class_name, tclass)

            yield pytest.Class.from_parent(self, name=class_name, obj=tclass)
