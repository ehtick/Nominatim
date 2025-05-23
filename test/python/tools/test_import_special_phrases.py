# SPDX-License-Identifier: GPL-3.0-or-later
#
# This file is part of Nominatim. (https://nominatim.org)
#
# Copyright (C) 2025 by the Nominatim developer community.
# For a full list of authors see the git log.
"""
    Tests for import special phrases methods
    of the class SPImporter.
"""
import pytest
from nominatim_db.tools.special_phrases.sp_importer import SPImporter
from nominatim_db.tools.special_phrases.sp_wiki_loader import SPWikiLoader
from nominatim_db.tools.special_phrases.special_phrase import SpecialPhrase


@pytest.fixture
def sp_importer(temp_db_conn, def_config, monkeypatch):
    """
        Return an instance of SPImporter.
    """
    monkeypatch.setenv('NOMINATIM_LANGUAGES', 'en')
    loader = SPWikiLoader(def_config)
    return SPImporter(def_config, temp_db_conn, loader)


@pytest.fixture
def xml_wiki_content(src_dir):
    """
        return the content of the static xml test file.
    """
    xml_test_content = src_dir / 'test' / 'testdata' / 'special_phrases_test_content.txt'
    return xml_test_content.read_text()


@pytest.fixture
def default_phrases(table_factory):
    table_factory('place_classtype_testclasstypetable_to_delete')
    table_factory('place_classtype_testclasstypetable_to_keep')


def test_fetch_existing_place_classtype_tables(sp_importer, table_factory):
    """
        Check for the fetch_existing_place_classtype_tables() method.
        It should return the table just created.
    """
    table_factory('place_classtype_testclasstypetable')

    sp_importer._fetch_existing_place_classtype_tables()
    contained_table = sp_importer.table_phrases_to_delete.pop()
    assert contained_table == 'place_classtype_testclasstypetable'


def test_check_sanity_class(sp_importer):
    """
        Check for _check_sanity() method.
        If a wrong class or type is given, an UsageError should raise.
        If a good class and type are given, nothing special happens.
    """

    assert not sp_importer._check_sanity(SpecialPhrase('en', '', 'type', ''))
    assert not sp_importer._check_sanity(SpecialPhrase('en', 'class', '', ''))

    assert sp_importer._check_sanity(SpecialPhrase('en', 'class', 'type', ''))


def test_load_white_and_black_lists(sp_importer):
    """
        Test that _load_white_and_black_lists() well return
        black list and white list and that they are of dict type.
    """
    black_list, white_list = sp_importer._load_white_and_black_lists()

    assert isinstance(black_list, dict) and isinstance(white_list, dict)


def test_create_place_classtype_indexes(temp_db_with_extensions,
                                        temp_db_conn, temp_db_cursor,
                                        table_factory, sp_importer):
    """
        Test that _create_place_classtype_indexes() create the
        place_id index and centroid index on the right place_class_type table.
    """
    phrase_class = 'class'
    phrase_type = 'type'
    table_name = 'place_classtype_{}_{}'.format(phrase_class, phrase_type)

    table_factory(table_name, 'place_id BIGINT, centroid GEOMETRY')

    sp_importer._create_place_classtype_indexes('', phrase_class, phrase_type)
    temp_db_conn.commit()

    assert check_placeid_and_centroid_indexes(temp_db_cursor, phrase_class, phrase_type)


def test_create_place_classtype_table(temp_db_conn, temp_db_cursor, placex_table, sp_importer):
    """
        Test that _create_place_classtype_table() create
        the right place_classtype table.
    """
    phrase_class = 'class'
    phrase_type = 'type'
    sp_importer._create_place_classtype_table('', phrase_class, phrase_type)
    temp_db_conn.commit()

    assert check_table_exist(temp_db_cursor, phrase_class, phrase_type)


def test_grant_access_to_web_user(temp_db_conn, temp_db_cursor, table_factory,
                                  def_config, sp_importer):
    """
        Test that _grant_access_to_webuser() give
        right access to the web user.
    """
    phrase_class = 'class'
    phrase_type = 'type'
    table_name = 'place_classtype_{}_{}'.format(phrase_class, phrase_type)

    table_factory(table_name)

    sp_importer._grant_access_to_webuser(phrase_class, phrase_type)
    temp_db_conn.commit()

    assert check_grant_access(temp_db_cursor, def_config.DATABASE_WEBUSER,
                              phrase_class, phrase_type)


def test_create_place_classtype_table_and_indexes(
        temp_db_cursor, def_config, placex_table,
        sp_importer, temp_db_conn, monkeypatch):
    """
        Test that _create_place_classtype_table_and_indexes()
        create the right place_classtype tables and place_id indexes
        and centroid indexes and grant access to the web user
        for the given set of pairs.
    """
    pairs = set([('class1', 'type1'), ('class2', 'type2')])
    for pair in pairs:
        placex_table.add(cls=pair[0], typ=pair[1])   # adding to db
    sp_importer._create_classtype_table_and_indexes(pairs)
    temp_db_conn.commit()

    for pair in pairs:
        assert check_table_exist(temp_db_cursor, pair[0], pair[1])
        assert check_placeid_and_centroid_indexes(temp_db_cursor, pair[0], pair[1])
        assert check_grant_access(temp_db_cursor, def_config.DATABASE_WEBUSER, pair[0], pair[1])


def test_remove_non_existent_tables_from_db(sp_importer, default_phrases,
                                            temp_db_conn, temp_db_cursor):
    """
        Check for the remove_non_existent_phrases_from_db() method.

        It should removed entries from the word table which are contained
        in the words_phrases_to_delete set and not those also contained
        in the words_phrases_still_exist set.

        place_classtype tables contained in table_phrases_to_delete should
        be deleted.
    """
    sp_importer.table_phrases_to_delete = {
        'place_classtype_testclasstypetable_to_delete'
    }

    query_tables = """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema='public'
        AND table_name like 'place_classtype_%';
    """

    sp_importer._remove_non_existent_tables_from_db()
    temp_db_conn.commit()

    assert temp_db_cursor.row_set(query_tables) \
        == {('place_classtype_testclasstypetable_to_keep', )}


@pytest.mark.parametrize("should_replace", [(True), (False)])
def test_import_phrases(monkeypatch, temp_db_cursor, def_config, sp_importer,
                        placex_table, table_factory, tokenizer_mock,
                        xml_wiki_content, should_replace):
    """
        Check that the main import_phrases() method is well executed.
        It should create the place_classtype table, the place_id and centroid indexes,
        grand access to the web user and executing the SQL functions for amenities.
        It should also update the database well by deleting or preserving existing entries
        of the database.
    """
    # Add some data to the database before execution in order to test
    # what is deleted and what is preserved.
    table_factory('place_classtype_amenity_animal_shelter')
    table_factory('place_classtype_wrongclass_wrongtype')

    monkeypatch.setattr('nominatim_db.tools.special_phrases.sp_wiki_loader._get_wiki_content',
                        lambda lang: xml_wiki_content)

    class_test = 'aerialway'
    type_test = 'zip_line'

    tokenizer = tokenizer_mock()
    placex_table.add(cls=class_test, typ=type_test)  # in db for special phrase filtering
    placex_table.add(cls='amenity', typ='animal_shelter')  # in db for special phrase filtering
    sp_importer.import_phrases(tokenizer, should_replace)

    assert len(tokenizer.analyser_cache['special_phrases']) == 18

    assert check_table_exist(temp_db_cursor, class_test, type_test)
    assert check_placeid_and_centroid_indexes(temp_db_cursor, class_test, type_test)
    assert check_grant_access(temp_db_cursor, def_config.DATABASE_WEBUSER, class_test, type_test)
    assert check_table_exist(temp_db_cursor, 'amenity', 'animal_shelter')
    if should_replace:
        assert not check_table_exist(temp_db_cursor, 'wrong_class', 'wrong_type')

    assert temp_db_cursor.table_exists('place_classtype_amenity_animal_shelter')
    if should_replace:
        assert not temp_db_cursor.table_exists('place_classtype_wrongclass_wrongtype')


def check_table_exist(temp_db_cursor, phrase_class, phrase_type):
    """
        Verify that the place_classtype table exists for the given
        phrase_class and phrase_type.
    """
    return temp_db_cursor.table_exists('place_classtype_{}_{}'.format(phrase_class, phrase_type))


def check_grant_access(temp_db_cursor, user, phrase_class, phrase_type):
    """
        Check that the web user has been granted right access to the
        place_classtype table of the given phrase_class and phrase_type.
    """
    table_name = 'place_classtype_{}_{}'.format(phrase_class, phrase_type)

    temp_db_cursor.execute("""
            SELECT * FROM information_schema.role_table_grants
            WHERE table_name='{}'
            AND grantee='{}'
            AND privilege_type='SELECT'""".format(table_name, user))
    return temp_db_cursor.fetchone()


def check_placeid_and_centroid_indexes(temp_db_cursor, phrase_class, phrase_type):
    """
        Check that the place_id index and centroid index exist for the
        place_classtype table of the given phrase_class and phrase_type.
    """
    table_name = 'place_classtype_{}_{}'.format(phrase_class, phrase_type)
    index_prefix = 'idx_place_classtype_{}_{}_'.format(phrase_class, phrase_type)

    return (
        temp_db_cursor.index_exists(table_name, index_prefix + 'centroid')
        and
        temp_db_cursor.index_exists(table_name, index_prefix + 'place_id')
    )


@pytest.mark.parametrize("should_replace", [(True), (False)])
def test_import_phrases_special_phrase_filtering(monkeypatch, temp_db_cursor, def_config,
                                                 sp_importer, placex_table, tokenizer_mock,
                                                 xml_wiki_content, should_replace):

    monkeypatch.setattr('nominatim_db.tools.special_phrases.sp_wiki_loader._get_wiki_content',
                        lambda lang: xml_wiki_content)

    class_test = 'aerialway'
    type_test = 'zip_line'

    placex_table.add(cls=class_test, typ=type_test)  # add to the database to make valid
    tokenizer = tokenizer_mock()
    sp_importer.import_phrases(tokenizer, should_replace)

    assert ('Zip Line', 'aerialway', 'zip_line', '-') in sp_importer.word_phrases
    assert check_table_exist(temp_db_cursor, class_test, type_test)
    assert check_placeid_and_centroid_indexes(temp_db_cursor, class_test, type_test)
    assert check_grant_access(temp_db_cursor, def_config.DATABASE_WEBUSER, class_test, type_test)


def test_get_classtype_pairs_directly(placex_table, temp_db_conn, sp_importer):
    for _ in range(101):
        placex_table.add(cls='highway', typ='residential')
    for _ in range(99):
        placex_table.add(cls='amenity', typ='toilet')

    temp_db_conn.commit()

    result = sp_importer.get_classtype_pairs(100)
    print("RESULT:", result)
    assert ('highway', 'residential') in result
    assert ('amenity', 'toilet') not in result
