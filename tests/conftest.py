"""Shared pytest fixtures for the factory simulator test suite."""

from hypothesis import HealthCheck, settings

settings.register_profile("ci", max_examples=50, suppress_health_check=[HealthCheck.too_slow])
settings.load_profile("ci")
