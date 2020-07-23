import pathlib
from contextlib import suppress

import pytest
import requests
from hypothesis import HealthCheck, given, settings
from hypothesis_jsonschema._canonicalise import HypothesisRefResolutionError

import schemathesis


def pytest_generate_tests(metafunc):
    apis = pathlib.Path(__file__).parent.absolute() / "openapi-directory/APIs/"

    string_path = f"{apis}/"

    def get_id(path):
        return str(path).replace(string_path, "")

    metafunc.parametrize(
        "schema_path", (path for path in walk(apis) if path.name in ("swagger.yaml", "openapi.yaml")), ids=get_id
    )


def walk(path: pathlib.Path):
    if path.is_dir():
        for item in path.iterdir():
            yield from walk(item)
    else:
        yield path


@pytest.fixture
def schema(schema_path):
    return schemathesis.from_path(schema_path)


@pytest.mark.corpus
def test_something(schema):

    for endpoint in schema.get_all_endpoints():

        @given(case=endpoint.as_strategy())
        @settings(max_examples=5, suppress_health_check=HealthCheck.all(), deadline=None)
        def test(case):
            with suppress(requests.RequestException):
                case.call(base_url="http://127.0.0.1:1")

        with suppress(HypothesisRefResolutionError):
            test()
