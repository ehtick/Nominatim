# SPDX-License-Identifier: GPL-3.0-or-later
#
# This file is part of Nominatim. (https://nominatim.org)
#
# Copyright (C) 2025 by the Nominatim developer community.
# For a full list of authors see the git log.
"""
Test functions for adapting results to the user's locale.
"""
import pytest

from nominatim_api import Locales


def test_display_name_empty_names():
    loc = Locales(['en', 'de'])

    assert loc.display_name(None) == ''
    assert loc.display_name({}) == ''


def test_display_name_none_localized():
    loc = Locales()

    assert loc.display_name({}) == ''
    assert loc.display_name({'name:de': 'DE', 'name': 'ALL'}) == 'ALL'
    assert loc.display_name({'ref': '34', 'name:de': 'DE'}) == '34'


def test_display_name_localized():
    loc = Locales(['en', 'de'])

    assert loc.display_name({}) == ''
    assert loc.display_name({'name:de': 'DE', 'name': 'ALL'}) == 'DE'
    assert loc.display_name({'ref': '34', 'name:de': 'DE'}) == 'DE'


def test_display_name_preference():
    loc = Locales(['en', 'de'])

    assert loc.display_name({}) == ''
    assert loc.display_name({'name:de': 'DE', 'name:en': 'EN'}) == 'EN'
    assert loc.display_name({'official_name:en': 'EN', 'name:de': 'DE'}) == 'DE'


@pytest.mark.parametrize('langstr,langlist',
                         [('fr', ['fr']),
                          ('fr-FR', ['fr-FR', 'fr']),
                          ('de,fr-FR', ['de', 'fr-FR', 'fr']),
                          ('fr,de,fr-FR', ['fr', 'de', 'fr-FR']),
                          ('en;q=0.5,fr', ['fr', 'en']),
                          ('en;q=0.5,fr,en-US', ['fr', 'en-US', 'en']),
                          ('en,fr;garbage,de', ['en', 'de'])])
def test_from_language_preferences(langstr, langlist):
    assert Locales.from_accept_languages(langstr).languages == langlist
