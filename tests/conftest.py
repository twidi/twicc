"""Pytest configuration for Django tests."""

import django
import pytest
from django.conf import settings


def pytest_configure():
    """Configure Django settings before tests run."""
    if not settings.configured:
        settings.configure()
    django.setup()


@pytest.fixture
def db_setup(db):
    """Fixture that provides database access and creates test data helpers."""
    return db
