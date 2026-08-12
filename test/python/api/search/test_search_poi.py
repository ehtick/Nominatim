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
def test_simple_near_search_large_radius(apiobj, frontend, coord, pid):
    apiobj.add_placex(place_id=1, class_='highway', type='bus_stop',
                      centroid=(5.0, 4.6))
    apiobj.add_placex(place_id=2, class_='highway', type='bus_stop',
                      centroid=(34.3, 56.1))

    details = SearchDetails.from_kwargs({'near': coord, 'near_radius': 0.5})

    results = run_search(apiobj, frontend, 0.1, [('highway', 'bus_stop')], [0.5], details=details)

    assert [r.place_id for r in results] == [pid]


@pytest.mark.parametrize('args', [{'near': '34.3, 56.100021', 'near_radius': 0.001},
                                  {'near': '34.3, 56.4', 'near_radius': 0.5},
                                  {'bounded_viewbox': True,
                                   'viewbox': '34.29,56.0,34.31,56.2'}])
def test_linked_places_excluded(apiobj, frontend, args):
    apiobj.add_placex(place_id=1, class_='highway', type='bus_stop',
                      centroid=(34.3, 56.1))
    apiobj.add_placex(place_id=2, class_='highway', type='bus_stop',
                      linked_place_id=1, centroid=(34.3, 56.10003))

    results = run_search(apiobj, frontend, 0.1, [('highway', 'bus_stop')], [0.5],
                         details=SearchDetails.from_kwargs(args))

    assert [r.place_id for r in results] == [1]


class TestPoiSearchWithRestrictions:

    @pytest.fixture(autouse=True, params=["small_radius", "large_radius"])
    def fill_database(self, apiobj, request):
        apiobj.add_placex(place_id=1, class_='highway', type='bus_stop',
                          country_code='au',
                          centroid=(34.3, 56.10003))
        apiobj.add_placex(place_id=2, class_='highway', type='bus_stop',
                          country_code='nz',
                          centroid=(34.3, 56.1))
        if request.param == 'large_radius':
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


class TestCategoryFilters:

    @pytest.fixture(autouse=True)
    def fill_database(self, apiobj):
        # A restaurant that is also a hotel.
        apiobj.add_placex(place_id=1, class_='amenity', type='restaurant',
                          categories=['osm.amenity.restaurant', 'osm.tourism.hotel'],
                          centroid=(10.0, 10.0))
        # A plain restaurant.
        apiobj.add_placex(place_id=2, class_='amenity', type='restaurant',
                          categories=['osm.amenity.restaurant'],
                          centroid=(10.0, 10.0))
        # A restaurant that is also a fast food place.
        apiobj.add_placex(place_id=3, class_='amenity', type='restaurant',
                          categories=['osm.amenity.restaurant', 'osm.amenity.fast_food'],
                          centroid=(10.0, 10.0))

    def run(self, apiobj, frontend, **kwargs):
        results = run_search(apiobj, frontend, 0.1, [('amenity', 'restaurant')],
                             details=SearchDetails.from_kwargs(kwargs))
        return sorted(r.place_id for r in results)

    def test_no_filter(self, apiobj, frontend):
        assert self.run(apiobj, frontend) == [1, 2, 3]

    def test_include_exact(self, apiobj, frontend):
        assert self.run(apiobj, frontend, include=['osm.tourism.hotel']) == [1]

    @pytest.mark.parametrize('category,expected', [('osm.amenity', [1, 2, 3]),
                                                   ('osm.tourism', [1])])
    def test_include_matches_descendants(self, apiobj, frontend, category, expected):
        assert self.run(apiobj, frontend, include=[category]) == expected

    @pytest.mark.parametrize('category', ['osm.amenity.fast', 'osm.amen.restaurant'])
    def test_include_does_not_match_partial_label(self, apiobj, frontend, category):
        assert self.run(apiobj, frontend, include=[category]) == []

    def test_include_comma_is_or(self, apiobj, frontend):
        assert self.run(apiobj, frontend,
                        include=['osm.tourism.hotel,osm.amenity.fast_food']) == [1, 3]

    @pytest.mark.parametrize('categories,expected', [
        (['osm.tourism.hotel', 'osm.amenity.fast_food'], []),
        (['osm.amenity.restaurant', 'osm.tourism.hotel'], [1])])
    def test_include_repeated_is_and(self, apiobj, frontend, categories, expected):
        assert self.run(apiobj, frontend, include=categories) == expected

    def test_exclude_exact(self, apiobj, frontend):
        assert self.run(apiobj, frontend, exclude=['osm.tourism.hotel']) == [2, 3]

    def test_exclude_matches_descendants(self, apiobj, frontend):
        assert self.run(apiobj, frontend, exclude=['osm.amenity']) == []

    @pytest.mark.parametrize('group,expected', [
        ('osm.tourism.hotel,osm.amenity.fast_food', [1, 2, 3]),
        ('osm.tourism.hotel,osm.amenity.restaurant', [2, 3])])
    def test_exclude_comma_needs_all(self, apiobj, frontend, group, expected):
        assert self.run(apiobj, frontend, exclude=[group]) == expected

    def test_exclude_repeated_is_or(self, apiobj, frontend):
        assert self.run(apiobj, frontend,
                        exclude=['osm.tourism.hotel', 'osm.amenity.fast_food']) == [2]

    def test_include_and_exclude(self, apiobj, frontend):
        assert self.run(apiobj, frontend, include=['osm.amenity'],
                        exclude=['osm.tourism.hotel']) == [2, 3]
