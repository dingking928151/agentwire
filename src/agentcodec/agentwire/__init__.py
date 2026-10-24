"""Public, side-effect-free AgentWire v1 contracts."""

from .contracts import (
    AgentWireError,
    AgentWireReasonCode,
    BenchmarkCandidate,
    DictionaryIdentity,
    GatewayDelivery,
    GatewayResult,
    NegotiationResult,
    ReceiverCapabilities,
    dictionary_identity_for,
)

__all__ = [
    "DictionaryIdentity",
    "BenchmarkCandidate",
    "ReceiverCapabilities",
    "NegotiationResult",
    "GatewayResult",
    "GatewayDelivery",
    "AgentWireError",
    "dictionary_identity_for",
    "AgentWireReasonCode",
]
