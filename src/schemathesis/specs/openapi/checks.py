from contextlib import ExitStack, contextmanager
from typing import TYPE_CHECKING, Any, Dict, Generator, List, Optional, Union

import jsonschema
import requests

from ...exceptions import (
    get_headers_error,
    get_missing_content_type_error,
    get_response_type_error,
    get_schema_validation_error,
    get_status_code_error,
)
from ...utils import GenericResponse, are_content_types_equal, parse_content_type
from .schemas import BaseOpenAPISchema
from .utils import expand_status_code

if TYPE_CHECKING:
    from ...models import Case


def status_code_conformance(response: GenericResponse, case: "Case") -> Optional[bool]:
    if not isinstance(case.endpoint.schema, BaseOpenAPISchema):
        raise TypeError("This check can be used only with Open API schemas")
    responses = case.endpoint.definition.raw.get("responses", {})
    # "default" can be used as the default response object for all HTTP codes that are not covered individually
    if "default" in responses:
        return None
    allowed_response_statuses = list(_expand_responses(responses))
    if response.status_code not in allowed_response_statuses:
        responses_list = ", ".join(map(str, responses))
        message = (
            f"Received a response with a status code, which is not defined in the schema: "
            f"{response.status_code}\n\nDeclared status codes: {responses_list}"
        )
        exc_class = get_status_code_error(response.status_code)
        raise exc_class(message)
    return None  # explicitly return None for mypy


def _expand_responses(responses: Dict[Union[str, int], Any]) -> Generator[int, None, None]:
    for code in responses:
        yield from expand_status_code(code)


def content_type_conformance(response: GenericResponse, case: "Case") -> Optional[bool]:
    if not isinstance(case.endpoint.schema, BaseOpenAPISchema):
        raise TypeError("This check can be used only with Open API schemas")
    content_types = case.endpoint.schema.get_content_types(case.endpoint, response)
    if not content_types:
        return None
    content_type = response.headers.get("Content-Type")
    if not content_type:
        raise get_missing_content_type_error()("Response is missing the `Content-Type` header")
    for option in content_types:
        if are_content_types_equal(option, content_type):
            return None
        expected_main, expected_sub = parse_content_type(option)
        received_main, received_sub = parse_content_type(content_type)
    exc_class = get_response_type_error(f"{expected_main}_{expected_sub}", f"{received_main}_{received_sub}")
    raise exc_class(
        f"Received a response with '{content_type}' Content-Type, "
        f"but it is not declared in the schema.\n\n"
        f"Defined content types: {', '.join(content_types)}"
    )


def response_headers_conformance(response: GenericResponse, case: "Case") -> Optional[bool]:
    if not isinstance(case.endpoint.schema, BaseOpenAPISchema):
        raise TypeError("This check can be used only with Open API schemas")
    defined_headers = case.endpoint.schema.get_headers(case.endpoint, response)
    if not defined_headers:
        return None

    missing_headers = [header for header in defined_headers if header not in response.headers]
    if not missing_headers:
        return None
    message = ",".join(missing_headers)
    exc_class = get_headers_error(message)
    raise exc_class(f"Received a response with missing headers: {message}")


def response_schema_conformance(response: GenericResponse, case: "Case") -> None:
    if not isinstance(case.endpoint.schema, BaseOpenAPISchema):
        raise TypeError("This check can be used only with Open API schemas")
    content_type = response.headers.get("Content-Type")
    if content_type is None:
        raise get_missing_content_type_error()("Response is missing the `Content-Type` header")
    if not content_type.startswith("application/json"):
        return
    # the keys should be strings
    responses = {str(key): value for key, value in case.endpoint.definition.raw.get("responses", {}).items()}
    status_code = str(response.status_code)
    if status_code in responses:
        definition = responses[status_code]
    elif "default" in responses:
        definition = responses["default"]
    else:
        # No response defined for the received response status code
        return
    scopes, schema = case.endpoint.schema.get_response_schema(definition, case.endpoint.definition.scope)
    if not schema:
        return
    if isinstance(response, requests.Response):
        data = response.json()
    else:
        data = response.json
    try:
        resolver = case.endpoint.schema.resolver
        with in_scopes(resolver, scopes):
            jsonschema.validate(data, schema, cls=jsonschema.Draft4Validator, resolver=resolver)
    except jsonschema.ValidationError as exc:
        exc_class = get_schema_validation_error(exc)
        raise exc_class(f"The received response does not conform to the defined schema!\n\nDetails: \n\n{exc}") from exc
    return None  # explicitly return None for mypy


@contextmanager
def in_scopes(resolver: jsonschema.RefResolver, scopes: List[str]) -> Generator[None, None, None]:
    """Push all available scopes into the resolver.

    There could be an additional scope change during a schema resolving in `get_response_schema`, so in total there
    could be a stack of two scopes maximum. This context manager handles both cases (1 or 2 scope changes) in the same
    way.
    """
    with ExitStack() as stack:
        for scope in scopes:
            stack.enter_context(resolver.in_scope(scope))
        yield
