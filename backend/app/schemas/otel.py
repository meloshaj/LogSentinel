from typing import Any, List, Optional
from pydantic import BaseModel, Field

class OtelAnyValue(BaseModel):
    string_value: Optional[str] = Field(None, alias="stringValue")
    bool_value: Optional[bool] = Field(None, alias="boolValue")
    int_value: Optional[int] = Field(None, alias="intValue")
    double_value: Optional[float] = Field(None, alias="doubleValue")
    array_value: Optional[dict] = Field(None, alias="arrayValue")
    kvlist_value: Optional[dict] = Field(None, alias="kvlistValue")
    bytes_value: Optional[str] = Field(None, alias="bytesValue")

    def get_value(self) -> Any:
        if self.string_value is not None: return self.string_value
        if self.bool_value is not None: return self.bool_value
        if self.int_value is not None: return self.int_value
        if self.double_value is not None: return self.double_value
        if self.array_value is not None: return self.array_value
        if self.kvlist_value is not None: return self.kvlist_value
        if self.bytes_value is not None: return self.bytes_value
        return None

class OtelKeyValue(BaseModel):
    key: str
    value: Optional[OtelAnyValue] = None

class OtelResource(BaseModel):
    attributes: List[OtelKeyValue] = Field(default_factory=list)

class OtelScope(BaseModel):
    name: Optional[str] = None
    version: Optional[str] = None

class OtelLogRecord(BaseModel):
    time_unix_nano: Optional[str] = Field(None, alias="timeUnixNano")
    observed_time_unix_nano: Optional[str] = Field(None, alias="observedTimeUnixNano")
    severity_number: Optional[int] = Field(None, alias="severityNumber")
    severity_text: Optional[str] = Field(None, alias="severityText")
    body: Optional[OtelAnyValue] = None
    attributes: List[OtelKeyValue] = Field(default_factory=list)
    dropped_attributes_count: Optional[int] = Field(None, alias="droppedAttributesCount")
    flags: Optional[int] = None
    trace_id: Optional[str] = Field(None, alias="traceId")
    span_id: Optional[str] = Field(None, alias="spanId")

class OtelScopeLogs(BaseModel):
    scope: Optional[OtelScope] = None
    log_records: List[OtelLogRecord] = Field(default_factory=list, alias="logRecords")
    schema_url: Optional[str] = Field(None, alias="schemaUrl")

class OtelResourceLogs(BaseModel):
    resource: Optional[OtelResource] = None
    scope_logs: List[OtelScopeLogs] = Field(default_factory=list, alias="scopeLogs")
    schema_url: Optional[str] = Field(None, alias="schemaUrl")

class ExportLogsServiceRequest(BaseModel):
    resource_logs: List[OtelResourceLogs] = Field(default_factory=list, alias="resourceLogs")

class ExportLogsPartialSuccess(BaseModel):
    rejected_log_records: Optional[int] = Field(None, alias="rejectedLogRecords")
    error_message: Optional[str] = Field(None, alias="errorMessage")

class ExportLogsServiceResponse(BaseModel):
    partial_success: Optional[ExportLogsPartialSuccess] = Field(None, alias="partialSuccess")
