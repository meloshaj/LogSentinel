from typing import Any

from pydantic import BaseModel, Field


class OtelAnyValue(BaseModel):
    string_value: str | None = Field(None, alias="stringValue")
    bool_value: bool | None = Field(None, alias="boolValue")
    int_value: int | None = Field(None, alias="intValue")
    double_value: float | None = Field(None, alias="doubleValue")
    array_value: dict | None = Field(None, alias="arrayValue")
    kvlist_value: dict | None = Field(None, alias="kvlistValue")
    bytes_value: str | None = Field(None, alias="bytesValue")

    def get_value(self) -> Any:
        if self.string_value is not None:
            return self.string_value
        if self.bool_value is not None:
            return self.bool_value
        if self.int_value is not None:
            return self.int_value
        if self.double_value is not None:
            return self.double_value
        if self.array_value is not None:
            return self.array_value
        if self.kvlist_value is not None:
            return self.kvlist_value
        if self.bytes_value is not None:
            return self.bytes_value
        return None


class OtelKeyValue(BaseModel):
    key: str
    value: OtelAnyValue | None = None


class OtelResource(BaseModel):
    attributes: list[OtelKeyValue] = Field(default_factory=list)


class OtelScope(BaseModel):
    name: str | None = None
    version: str | None = None


class OtelLogRecord(BaseModel):
    time_unix_nano: str | None = Field(None, alias="timeUnixNano")
    observed_time_unix_nano: str | None = Field(None, alias="observedTimeUnixNano")
    severity_number: int | None = Field(None, alias="severityNumber")
    severity_text: str | None = Field(None, alias="severityText")
    body: OtelAnyValue | None = None
    attributes: list[OtelKeyValue] = Field(default_factory=list)
    dropped_attributes_count: int | None = Field(None, alias="droppedAttributesCount")
    flags: int | None = None
    trace_id: str | None = Field(None, alias="traceId")
    span_id: str | None = Field(None, alias="spanId")


class OtelScopeLogs(BaseModel):
    scope: OtelScope | None = None
    log_records: list[OtelLogRecord] = Field(default_factory=list, alias="logRecords")
    schema_url: str | None = Field(None, alias="schemaUrl")


class OtelResourceLogs(BaseModel):
    resource: OtelResource | None = None
    scope_logs: list[OtelScopeLogs] = Field(default_factory=list, alias="scopeLogs")
    schema_url: str | None = Field(None, alias="schemaUrl")


class ExportLogsServiceRequest(BaseModel):
    resource_logs: list[OtelResourceLogs] = Field(
        default_factory=list, alias="resourceLogs"
    )


class ExportLogsPartialSuccess(BaseModel):
    rejected_log_records: int | None = Field(None, alias="rejectedLogRecords")
    error_message: str | None = Field(None, alias="errorMessage")


class ExportLogsServiceResponse(BaseModel):
    partial_success: ExportLogsPartialSuccess | None = Field(
        None, alias="partialSuccess"
    )
