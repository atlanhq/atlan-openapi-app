"""Unit tests for the OpenAPI asset mapper."""

from openapi.api_types import OpenAPIPathRecord, OpenAPISpecRecord
from openapi.asset_mapper import (
    CONNECTOR_NAME,
    build_api_path_qn,
    build_api_spec_qn,
    map_api_path,
    map_api_spec,
    map_connection,
)
from pyatlan_v9.model.assets import APIPath, APISpec, Connection, RelatedAPISpec

CONN_QN = "default/api/test-conn"
WORKFLOW_ID = "test-wf-123"
WORKFLOW_TYPE = "openapi"
WORKFLOW_RUN_AT_MS = 1700000000000


# =============================================================================
# QN builders
# =============================================================================


class TestBuildApiSpecQN:
    def test_basic_format(self) -> None:
        qn = build_api_spec_qn("default/api/my-conn", "Petstore")
        assert qn == "default/api/my-conn/Petstore"

    def test_extends_connection_qn(self) -> None:
        qn = build_api_spec_qn(CONN_QN, "MyAPI")
        assert qn.startswith(CONN_QN)

    def test_different_titles_produce_different_qns(self) -> None:
        qn1 = build_api_spec_qn(CONN_QN, "API-A")
        qn2 = build_api_spec_qn(CONN_QN, "API-B")
        assert qn1 != qn2
        assert qn1 == f"{CONN_QN}/API-A"
        assert qn2 == f"{CONN_QN}/API-B"

    def test_different_connections_produce_different_qns(self) -> None:
        qn1 = build_api_spec_qn("default/api/conn-a", "SameTitle")
        qn2 = build_api_spec_qn("default/api/conn-b", "SameTitle")
        assert qn1 != qn2


class TestBuildApiPathQN:
    def test_basic_format(self) -> None:
        spec_qn = build_api_spec_qn(CONN_QN, "Petstore")
        path_qn = build_api_path_qn(spec_qn, "/pet/{petId}")
        assert path_qn == f"{spec_qn}/pet/{{petId}}"

    def test_extends_spec_qn(self) -> None:
        spec_qn = build_api_spec_qn(CONN_QN, "MyAPI")
        path_qn = build_api_path_qn(spec_qn, "/users")
        assert path_qn.startswith(spec_qn)

    def test_no_double_slash_for_root_path(self) -> None:
        """path_url starts with '/', spec_qn has no trailing '/' — no double slash."""
        spec_qn = "default/api/conn/MyAPI"
        path_qn = build_api_path_qn(spec_qn, "/pets")
        assert "//" not in path_qn
        assert path_qn == "default/api/conn/MyAPI/pets"

    def test_different_paths_produce_different_qns(self) -> None:
        spec_qn = build_api_spec_qn(CONN_QN, "MyAPI")
        qn1 = build_api_path_qn(spec_qn, "/users")
        qn2 = build_api_path_qn(spec_qn, "/orders")
        assert qn1 != qn2


# =============================================================================
# map_connection
# =============================================================================


def _make_connection(qn: str = CONN_QN, name: str = "test-conn") -> Connection:
    return Connection(
        qualified_name=qn, name=name, category="API", admin_groups=["admins"]
    )


class TestMapConnection:
    def test_returns_connection_instance(self) -> None:
        conn = map_connection(_make_connection())
        assert isinstance(conn, Connection)

    def test_correct_qualified_name(self) -> None:
        conn = map_connection(_make_connection())
        assert conn.qualified_name == CONN_QN

    def test_correct_name(self) -> None:
        conn = map_connection(_make_connection())
        assert conn.name == "test-conn"

    def test_correct_connector_name(self) -> None:
        conn = map_connection(_make_connection())
        assert conn.connector_name == CONNECTOR_NAME

    def test_connector_name_is_api(self) -> None:
        assert CONNECTOR_NAME == "api"

    def test_connection_qualified_name_set(self) -> None:
        conn = map_connection(_make_connection())
        assert conn.connection_qualified_name == CONN_QN

    def test_category_is_api(self) -> None:
        conn = map_connection(_make_connection())
        assert conn.category == "API"

    def test_name_falls_back_to_last_segment(self) -> None:
        """When connection.name is not set, fall back to last QN segment."""
        bare_conn = Connection(
            qualified_name="default/api/fallback-name",
            category="API",
            admin_groups=["admins"],
        )
        result = map_connection(bare_conn)
        assert result.name == "fallback-name"


# =============================================================================
# map_api_spec
# =============================================================================


def _sample_spec_record(**kwargs) -> OpenAPISpecRecord:
    defaults = dict(
        title="Petstore",
        openapi_version="3.0.4",
        description="A pet store API",
        terms_of_service="https://example.com/tos",
        contact_name="Support",
        contact_email="support@example.com",
        contact_url="https://example.com/contact",
        license_name="Apache 2.0",
        license_url="https://www.apache.org/licenses/LICENSE-2.0",
        spec_version="1.0.0",
        external_docs_url="https://docs.example.com",
        external_docs_description="Full documentation",
        spec_url="https://petstore3.swagger.io/api/v3/openapi.json",
    )
    defaults.update(kwargs)
    return OpenAPISpecRecord(**defaults)


class TestMapApiSpec:
    def test_returns_api_spec_instance(self) -> None:
        result = map_api_spec(
            _sample_spec_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert isinstance(result, APISpec)

    def test_correct_qualified_name(self) -> None:
        result = map_api_spec(
            _sample_spec_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert result.qualified_name == f"{CONN_QN}/Petstore"

    def test_correct_name(self) -> None:
        result = map_api_spec(
            _sample_spec_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert result.name == "Petstore"

    def test_correct_connector_name(self) -> None:
        result = map_api_spec(
            _sample_spec_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert result.connector_name == CONNECTOR_NAME

    def test_correct_connection_qualified_name(self) -> None:
        result = map_api_spec(
            _sample_spec_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert result.connection_qualified_name == CONN_QN

    def test_source_url_set(self) -> None:
        result = map_api_spec(
            _sample_spec_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert result.source_url == "https://petstore3.swagger.io/api/v3/openapi.json"

    def test_api_spec_type_set(self) -> None:
        result = map_api_spec(
            _sample_spec_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert result.api_spec_type == "3.0.4"

    def test_description_set_when_present(self) -> None:
        result = map_api_spec(
            _sample_spec_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert result.description == "A pet store API"

    def test_terms_of_service_set(self) -> None:
        result = map_api_spec(
            _sample_spec_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert result.api_spec_terms_of_service_url == "https://example.com/tos"

    def test_spec_version_set(self) -> None:
        result = map_api_spec(
            _sample_spec_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert result.api_spec_version == "1.0.0"

    def test_contact_email_set(self) -> None:
        result = map_api_spec(
            _sample_spec_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert result.api_spec_contact_email == "support@example.com"

    def test_contact_name_set(self) -> None:
        result = map_api_spec(
            _sample_spec_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert result.api_spec_contact_name == "Support"

    def test_contact_url_set(self) -> None:
        result = map_api_spec(
            _sample_spec_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert result.api_spec_contact_url == "https://example.com/contact"

    def test_license_name_set(self) -> None:
        result = map_api_spec(
            _sample_spec_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert result.api_spec_license_name == "Apache 2.0"

    def test_license_url_set(self) -> None:
        result = map_api_spec(
            _sample_spec_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert (
            result.api_spec_license_url == "https://www.apache.org/licenses/LICENSE-2.0"
        )

    def test_external_docs_set(self) -> None:
        result = map_api_spec(
            _sample_spec_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert result.api_external_docs == {
            "url": "https://docs.example.com",
            "description": "Full documentation",
        }

    def test_optional_fields_not_set_when_empty(self) -> None:
        """Empty optional fields should not be set on the asset."""
        record = OpenAPISpecRecord(
            title="MinimalAPI",
            openapi_version="",
            description="",
            spec_url="",
        )
        result = map_api_spec(
            record, CONN_QN, WORKFLOW_ID, WORKFLOW_TYPE, WORKFLOW_RUN_AT_MS
        )
        # These should not be set (None or UNSET) when the record fields are empty
        from msgspec import UNSET

        assert (
            result.api_spec_type is None
            or result.api_spec_type is UNSET
            or result.api_spec_type == ""
        )
        assert (
            result.description is None
            or result.description is UNSET
            or result.description == ""
        )

    def test_contact_not_set_when_all_contact_fields_empty(self) -> None:
        """Contact fields should not be set when all contact fields are empty."""
        record = OpenAPISpecRecord(
            title="NoContact",
            contact_name="",
            contact_email="",
            contact_url="",
        )
        result = map_api_spec(
            record, CONN_QN, WORKFLOW_ID, WORKFLOW_TYPE, WORKFLOW_RUN_AT_MS
        )
        from msgspec import UNSET

        assert (
            result.api_spec_contact_email is None
            or result.api_spec_contact_email is UNSET
            or result.api_spec_contact_email == ""
        )


# =============================================================================
# map_api_path
# =============================================================================


SPEC_QN = f"{CONN_QN}/Petstore"


def _sample_path_record(**kwargs) -> OpenAPIPathRecord:
    defaults = dict(
        path_url="/pet/{petId}",
        spec_title="Petstore",
        spec_qualified_name=SPEC_QN,
        summary="Find pet by ID",
        available_operations=["GET", "POST", "DELETE"],
        description="| Method | Summary|\n|---|---|\n| `GET` |Find pet by ID |",
        is_templated=True,
    )
    defaults.update(kwargs)
    return OpenAPIPathRecord(**defaults)


class TestMapApiPath:
    def test_returns_api_path_instance(self) -> None:
        result = map_api_path(
            _sample_path_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert isinstance(result, APIPath)

    def test_correct_qualified_name(self) -> None:
        result = map_api_path(
            _sample_path_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert result.qualified_name == f"{SPEC_QN}/pet/{{petId}}"

    def test_correct_name(self) -> None:
        """name is the path_url."""
        result = map_api_path(
            _sample_path_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert result.name == "/pet/{petId}"

    def test_correct_connector_name(self) -> None:
        result = map_api_path(
            _sample_path_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert result.connector_name == CONNECTOR_NAME

    def test_correct_connection_qualified_name(self) -> None:
        result = map_api_path(
            _sample_path_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert result.connection_qualified_name == CONN_QN

    def test_api_spec_name_set(self) -> None:
        result = map_api_path(
            _sample_path_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert result.api_spec_name == "Petstore"

    def test_api_spec_qualified_name_set(self) -> None:
        result = map_api_path(
            _sample_path_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert result.api_spec_qualified_name == SPEC_QN

    def test_api_path_raw_uri_set(self) -> None:
        result = map_api_path(
            _sample_path_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert result.api_path_raw_uri == "/pet/{petId}"

    def test_api_path_is_templated_true(self) -> None:
        result = map_api_path(
            _sample_path_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert result.api_path_is_templated is True

    def test_api_path_is_templated_false(self) -> None:
        record = _sample_path_record(path_url="/pets", is_templated=False)
        result = map_api_path(
            record, CONN_QN, WORKFLOW_ID, WORKFLOW_TYPE, WORKFLOW_RUN_AT_MS
        )
        assert result.api_path_is_templated is False

    def test_api_path_summary_set_when_present(self) -> None:
        result = map_api_path(
            _sample_path_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert result.api_path_summary == "Find pet by ID"

    def test_api_path_available_operations_set(self) -> None:
        result = map_api_path(
            _sample_path_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert result.api_path_available_operations == ["GET", "POST", "DELETE"]

    def test_description_set_when_present(self) -> None:
        result = map_api_path(
            _sample_path_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert result.description is not None
        assert "GET" in result.description

    def test_api_spec_relationship_set(self) -> None:
        """api_spec relationship should point to the parent spec."""
        result = map_api_path(
            _sample_path_record(),
            CONN_QN,
            WORKFLOW_ID,
            WORKFLOW_TYPE,
            WORKFLOW_RUN_AT_MS,
        )
        assert isinstance(result.api_spec, RelatedAPISpec)
        assert result.api_spec.unique_attributes == {"qualifiedName": SPEC_QN}

    def test_non_templated_path(self) -> None:
        record = _sample_path_record(path_url="/store/inventory", is_templated=False)
        result = map_api_path(
            record, CONN_QN, WORKFLOW_ID, WORKFLOW_TYPE, WORKFLOW_RUN_AT_MS
        )
        assert result.qualified_name == f"{SPEC_QN}/store/inventory"
        assert result.api_path_is_templated is False

    def test_empty_operations_not_set(self) -> None:
        """When available_operations is empty, it should not be set."""
        record = _sample_path_record(available_operations=[])
        result = map_api_path(
            record, CONN_QN, WORKFLOW_ID, WORKFLOW_TYPE, WORKFLOW_RUN_AT_MS
        )
        from msgspec import UNSET

        assert (
            result.api_path_available_operations is None
            or result.api_path_available_operations is UNSET
            or result.api_path_available_operations == []
        )
