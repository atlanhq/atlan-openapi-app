"""Unit tests for typed JSONL write/read round-trip.

Tests that OpenAPISpecRecord and OpenAPIPathRecord survive encode/decode
cycles through JSONL files using msgspec.
"""

from pathlib import Path

import msgspec

from openapi.api_types import OpenAPIPathRecord, OpenAPISpecRecord


def _enc_hook(obj: object) -> object:
    """Handle non-standard types during msgspec JSON encoding."""
    return str(obj)


_encoder = msgspec.json.Encoder(enc_hook=_enc_hook)


# =============================================================================
# OpenAPISpecRecord round-trips
# =============================================================================


class TestOpenAPISpecRecordRoundTrip:
    def test_single_spec_record_round_trip(self, tmp_path: Path) -> None:
        """Write a single OpenAPISpecRecord and read it back."""
        record = OpenAPISpecRecord(
            title="Petstore",
            openapi_version="3.0.4",
            description="A sample API",
            spec_url="https://example.com/api.json",
        )
        file_path = tmp_path / "api_spec.jsonl"

        with file_path.open("wb") as f:
            f.write(_encoder.encode(record) + b"\n")

        with file_path.open("rb") as f:
            decoded = msgspec.json.decode(f.readline().strip(), type=OpenAPISpecRecord)

        assert decoded.title == "Petstore"
        assert decoded.openapi_version == "3.0.4"
        assert decoded.description == "A sample API"
        assert decoded.spec_url == "https://example.com/api.json"

    def test_spec_record_default_fields(self, tmp_path: Path) -> None:
        """Only the required field 'title' must be provided; defaults survive round-trip."""
        record = OpenAPISpecRecord(title="MinimalAPI")
        file_path = tmp_path / "api_spec.jsonl"

        with file_path.open("wb") as f:
            f.write(_encoder.encode(record) + b"\n")

        with file_path.open("rb") as f:
            decoded = msgspec.json.decode(f.readline().strip(), type=OpenAPISpecRecord)

        assert decoded.title == "MinimalAPI"
        assert decoded.openapi_version == ""
        assert decoded.description == ""
        assert decoded.terms_of_service == ""
        assert decoded.contact_name == ""
        assert decoded.contact_email == ""
        assert decoded.contact_url == ""
        assert decoded.license_name == ""
        assert decoded.license_url == ""
        assert decoded.spec_version == ""
        assert decoded.external_docs_url == ""
        assert decoded.external_docs_description == ""
        assert decoded.spec_url == ""

    def test_spec_record_all_fields(self, tmp_path: Path) -> None:
        """All fields survive round-trip."""
        record = OpenAPISpecRecord(
            title="Full API",
            openapi_version="3.1.0",
            description="Full description",
            terms_of_service="https://example.com/tos",
            contact_name="Team",
            contact_email="team@example.com",
            contact_url="https://example.com/contact",
            license_name="MIT",
            license_url="https://opensource.org/licenses/MIT",
            spec_version="2.5.0",
            external_docs_url="https://docs.example.com",
            external_docs_description="Full docs",
            spec_url="https://example.com/openapi.json",
        )
        file_path = tmp_path / "api_spec.jsonl"

        with file_path.open("wb") as f:
            f.write(_encoder.encode(record) + b"\n")

        with file_path.open("rb") as f:
            decoded = msgspec.json.decode(f.readline().strip(), type=OpenAPISpecRecord)

        assert decoded.title == "Full API"
        assert decoded.openapi_version == "3.1.0"
        assert decoded.terms_of_service == "https://example.com/tos"
        assert decoded.contact_name == "Team"
        assert decoded.contact_email == "team@example.com"
        assert decoded.contact_url == "https://example.com/contact"
        assert decoded.license_name == "MIT"
        assert decoded.license_url == "https://opensource.org/licenses/MIT"
        assert decoded.spec_version == "2.5.0"
        assert decoded.external_docs_url == "https://docs.example.com"
        assert decoded.external_docs_description == "Full docs"
        assert decoded.spec_url == "https://example.com/openapi.json"

    def test_multiple_spec_records_round_trip(self, tmp_path: Path) -> None:
        """Write multiple OpenAPISpecRecords and read them all back."""
        records = [
            OpenAPISpecRecord(title="API-A", openapi_version="3.0.0"),
            OpenAPISpecRecord(title="API-B", openapi_version="2.0"),
        ]
        file_path = tmp_path / "api_spec.jsonl"

        with file_path.open("wb") as f:
            for r in records:
                f.write(_encoder.encode(r) + b"\n")

        decoded: list[OpenAPISpecRecord] = []
        with file_path.open("rb") as f:
            for line in f:
                line = line.strip()
                if line:
                    decoded.append(msgspec.json.decode(line, type=OpenAPISpecRecord))

        assert len(decoded) == 2
        assert decoded[0].title == "API-A"
        assert decoded[0].openapi_version == "3.0.0"
        assert decoded[1].title == "API-B"
        assert decoded[1].openapi_version == "2.0"

    def test_empty_file_produces_no_records(self, tmp_path: Path) -> None:
        """Reading an empty file should yield no records."""
        file_path = tmp_path / "empty.jsonl"
        file_path.touch()

        decoded: list[OpenAPISpecRecord] = []
        with file_path.open("rb") as f:
            for line in f:
                line = line.strip()
                if line:
                    decoded.append(msgspec.json.decode(line, type=OpenAPISpecRecord))

        assert decoded == []


# =============================================================================
# OpenAPIPathRecord round-trips
# =============================================================================


SPEC_QN = "default/api/test-conn/Petstore"


class TestOpenAPIPathRecordRoundTrip:
    def test_single_path_record_round_trip(self, tmp_path: Path) -> None:
        """Write a single OpenAPIPathRecord and read it back."""
        record = OpenAPIPathRecord(
            path_url="/pet/{petId}",
            spec_title="Petstore",
            spec_qualified_name=SPEC_QN,
            summary="Find pet by ID",
            available_operations=["GET", "DELETE"],
            description="| Method | Summary|\n|---|---|\n| `GET` |Find pet by ID |",
            is_templated=True,
        )
        file_path = tmp_path / "api_path.jsonl"

        with file_path.open("wb") as f:
            f.write(_encoder.encode(record) + b"\n")

        with file_path.open("rb") as f:
            decoded = msgspec.json.decode(f.readline().strip(), type=OpenAPIPathRecord)

        assert decoded.path_url == "/pet/{petId}"
        assert decoded.spec_title == "Petstore"
        assert decoded.spec_qualified_name == SPEC_QN
        assert decoded.summary == "Find pet by ID"
        assert decoded.available_operations == ["GET", "DELETE"]
        assert decoded.is_templated is True

    def test_path_record_default_fields(self, tmp_path: Path) -> None:
        """Default fields survive round-trip."""
        record = OpenAPIPathRecord(
            path_url="/pets",
            spec_title="API",
            spec_qualified_name=SPEC_QN,
        )
        file_path = tmp_path / "api_path.jsonl"

        with file_path.open("wb") as f:
            f.write(_encoder.encode(record) + b"\n")

        with file_path.open("rb") as f:
            decoded = msgspec.json.decode(f.readline().strip(), type=OpenAPIPathRecord)

        assert decoded.summary == ""
        assert decoded.available_operations == []
        assert decoded.description == ""
        assert decoded.is_templated is False

    def test_boolean_is_templated_false(self, tmp_path: Path) -> None:
        """is_templated=False survives round-trip without type coercion."""
        record = OpenAPIPathRecord(
            path_url="/store/inventory",
            spec_title="API",
            spec_qualified_name=SPEC_QN,
            is_templated=False,
        )
        file_path = tmp_path / "api_path.jsonl"

        with file_path.open("wb") as f:
            f.write(_encoder.encode(record) + b"\n")

        with file_path.open("rb") as f:
            decoded = msgspec.json.decode(f.readline().strip(), type=OpenAPIPathRecord)

        assert decoded.is_templated is False

    def test_available_operations_list_survives(self, tmp_path: Path) -> None:
        """list[str] field available_operations survives round-trip."""
        record = OpenAPIPathRecord(
            path_url="/pet",
            spec_title="API",
            spec_qualified_name=SPEC_QN,
            available_operations=["GET", "POST", "PUT", "PATCH", "DELETE"],
        )
        file_path = tmp_path / "api_path.jsonl"

        with file_path.open("wb") as f:
            f.write(_encoder.encode(record) + b"\n")

        with file_path.open("rb") as f:
            decoded = msgspec.json.decode(f.readline().strip(), type=OpenAPIPathRecord)

        assert decoded.available_operations == ["GET", "POST", "PUT", "PATCH", "DELETE"]

    def test_multiple_path_records_round_trip(self, tmp_path: Path) -> None:
        """Write multiple OpenAPIPathRecords and read them all back."""
        records = [
            OpenAPIPathRecord(path_url="/pets", spec_title="API", spec_qualified_name=SPEC_QN),
            OpenAPIPathRecord(path_url="/pet/{id}", spec_title="API", spec_qualified_name=SPEC_QN, is_templated=True),
            OpenAPIPathRecord(path_url="/orders", spec_title="API", spec_qualified_name=SPEC_QN),
        ]
        file_path = tmp_path / "api_path.jsonl"

        with file_path.open("wb") as f:
            for r in records:
                f.write(_encoder.encode(r) + b"\n")

        decoded: list[OpenAPIPathRecord] = []
        with file_path.open("rb") as f:
            for line in f:
                line = line.strip()
                if line:
                    decoded.append(msgspec.json.decode(line, type=OpenAPIPathRecord))

        assert len(decoded) == 3
        assert decoded[0].path_url == "/pets"
        assert decoded[1].path_url == "/pet/{id}"
        assert decoded[1].is_templated is True
        assert decoded[2].path_url == "/orders"

    def test_empty_file_produces_no_records(self, tmp_path: Path) -> None:
        """Reading an empty file should yield no records."""
        file_path = tmp_path / "empty_paths.jsonl"
        file_path.touch()

        decoded: list[OpenAPIPathRecord] = []
        with file_path.open("rb") as f:
            for line in f:
                line = line.strip()
                if line:
                    decoded.append(msgspec.json.decode(line, type=OpenAPIPathRecord))

        assert decoded == []
