# SPDX-License-Identifier: GPL-3.0-or-later
#
# This file is part of Nominatim. (https://nominatim.org)
#
# Copyright (C) 2025 by the Nominatim developer community.
# For a full list of authors see the git log.
"""
Tests for running the POI searcher.
"""
import pytest

from nominatim_api.types import SearchDetails
from nominatim_api.search.db_searches import PoiSearch
from nominatim_api.search.db_search_fields import WeightedStrings, WeightedCategories


def run_search(apiobj, frontend, global_penalty, poitypes, poi_penalties=None,
               ccodes=[], details=SearchDetails()):
    if poi_penalties is None:
        poi_penalties = [0.0] * len(poitypes)

    class MySearchData:
        penalty = global_penalty
        qualifiers = WeightedCategories(poitypes, poi_penalties)
        countries = WeightedStrings(ccodes, [0.0] * len(ccodes))

    search = PoiSearch(MySearchData())

    api = frontend(apiobj, options=['search'])

    async def run():
        async with api._async_api.begin() as conn:
            return await search.lookup(conn, details)

    return api._loop.run_until_complete(run())


@pytest.mark.parametrize('coord,pid', [('34.3, 56.100021', 2),
                                       ('5.0, 4.59933', 1)])
def test_simple_near_search_in_placex(apiobj, frontend, coord, pid):
    apiobj.add_placex(place_id=1, class_='highway', type='bus_stop',
                      centroid=(5.0, 4.6))
    apiobj.add_placex(place_id=2, class_='highway', type='bus_stop',
                      centroid=(34.3, 56.1))

    details = SearchDetails.from_kwargs({'near': coord, 'near_radius': 0.001})

    results = run_search(apiobj, frontend, 0.1, [('highway', 'bus_stop')], [0.5], details=details)

    assert [r.place_id for r in results] == [pid]


@pytest.mark.parametrize('coord,pid', [('34.3, 56.100021', 2),
                                       ('34.3, 56.4', 2),
                                       ('5.0, 4.59933', 1)])
def test_simple_near_search_in_classtype(apiobj, frontend, coord, pid):
    apiobj.add_placex(place_id=1, class_='highway', type='bus_stop',
                      centroid=(5.0, 4.6))
    apiobj.add_placex(place_id=2, class_='highway', type='bus_stop',
                      centroid=(34.3, 56.1))
    apiobj.add_class_type_table('highway', 'bus_stop')

    details = SearchDetails.from_kwargs({'near': coord, 'near_radius': 0.5})

    results = run_search(apiobj, frontend, 0.1, [('highway', 'bus_stop')], [0.5], details=details)

    assert [r.place_id for r in results] == [pid]


class TestPoiSearchWithRestrictions:

    @pytest.fixture(autouse=True, params=["placex", "classtype"])
    def fill_database(self, apiobj, request):
        apiobj.add_placex(place_id=1, class_='highway', type='bus_stop',
                          country_code='au',
                          centroid=(34.3, 56.10003))
        apiobj.add_placex(place_id=2, class_='highway', type='bus_stop',
                          country_code='nz',
                          centroid=(34.3, 56.1))
        if request.param == 'classtype':
            apiobj.add_class_type_table('highway', 'bus_stop')
            self.args = {'near': '34.3, 56.4', 'near_radius': 0.5}
        else:
            self.args = {'near': '34.3, 56.100021', 'near_radius': 0.001}

    def test_unrestricted(self, apiobj, frontend):
        results = run_search(apiobj, frontend, 0.1, [('highway', 'bus_stop')], [0.5],
                             details=SearchDetails.from_kwargs(self.args))

        assert [r.place_id for r in results] == [1, 2]

    def test_restict_country(self, apiobj, frontend):
        results = run_search(apiobj, frontend, 0.1, [('highway', 'bus_stop')], [0.5],
                             ccodes=['de', 'nz'],
                             details=SearchDetails.from_kwargs(self.args))

        assert [r.place_id for r in results] == [2]

    def test_restrict_by_viewbox(self, apiobj, frontend):
        args = {'bounded_viewbox': True, 'viewbox': '34.299,56.0,34.3001,56.10001'}
        args.update(self.args)
        results = run_search(apiobj, frontend, 0.1, [('highway', 'bus_stop')], [0.5],
                             ccodes=['de', 'nz'],
                             details=SearchDetails.from_kwargs(args))

        assert [r.place_id for r in results] == [2]
