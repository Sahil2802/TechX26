"""Multi-agent sub-package for AquaGrid.

Three specialized agents, each with a focused job and minimal tool surface:
  SensorGuard     — sensor health & leak-vs-fault diagnosis
  GridOptimizer   — tariff-aware production scheduling & reservoir safety
  SecuritySentinel— cyber-physical threat detection & incident reporting

All agents share the same LLM provider chain (llm.py), the same gateway
(gateway.py), and the same deterministic guards (guards.py). They communicate
only through the shared SQLite audit_log and actions tables — never directly.
"""
