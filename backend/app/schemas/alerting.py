from pydantic import BaseModel, Field
from typing import Optional, List

class IncidentAlertPayload(BaseModel):
    """Payload representing an incident or anomaly to be dispatched to webhooks."""
    
    incident_id: str = Field(..., description="Unique identifier for the incident or anomaly")
    root_cause_service: str = Field(..., description="The suspected root cause service name")
    triggering_template: Optional[str] = Field(None, description="The Drain3 template or error message that triggered this")
    affected_services: List[str] = Field(default_factory=list, description="List of downstream services affected")
    propagation_chain: List[str] = Field(default_factory=list, description="Ordered list of services representing the failure chain")
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Confidence score of the incident (0.0 to 1.0)")
    is_critical: bool = Field(default=True, description="Whether this is a critical cascade or just a degraded state")
