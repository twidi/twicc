"""The read-through cache the CLI session payload uses for project_directory."""

from __future__ import annotations

import pytest

from twicc import projects
from twicc.core.models import Project


@pytest.fixture(autouse=True)
def empty_cache(monkeypatch):
    monkeypatch.setattr(projects, "_project_directories", {})


@pytest.fixture
def two_projects(db):
    Project.objects.create(id="-p-one", directory="/p/one")
    Project.objects.create(id="-p-two", directory=None)


def test_a_hit_asks_no_query(db, django_assert_num_queries):
    projects._project_directories["-p-one"] = "/cached"
    with django_assert_num_queries(0):
        assert projects.project_directory_cached("-p-one") == "/cached"


def test_a_miss_asks_one_query_then_none(two_projects, django_assert_num_queries):
    with django_assert_num_queries(1):
        assert projects.project_directory_cached("-p-one") == "/p/one"
    assert projects._project_directories["-p-one"] == "/p/one"
    with django_assert_num_queries(0):
        assert projects.project_directory_cached("-p-one") == "/p/one"


def test_a_stored_none_is_a_hit(db, django_assert_num_queries):
    projects._project_directories["-p-two"] = None
    with django_assert_num_queries(0):
        assert projects.project_directory_cached("-p-two") is None


def test_several_misses_cost_one_query(two_projects, django_assert_num_queries):
    with django_assert_num_queries(1):
        found = projects.project_directories_cached(["-p-one", "-p-two", "-p-none", "-p-one"])
    assert found == {"-p-one": "/p/one", "-p-two": None, "-p-none": None}
    assert "-p-none" not in projects._project_directories, "no row: not stored"
    assert projects._project_directories["-p-two"] is None


def test_a_cached_key_survives_a_call(two_projects):
    projects._project_directories["-p-kept"] = "/kept"
    projects.project_directories_cached(["-p-one"])
    assert projects._project_directories["-p-kept"] == "/kept"
