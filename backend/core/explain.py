"""Plain-language explanations for every reason code.

Templates use one sentence where possible, no jargon, no scores, no rule names
and never raw sensitive values.
"""

from __future__ import annotations

from collections import defaultdict

TEMPLATES: dict[str, str] = {
    "PROTECTED_PATH": "The agent tried to open {target}, which is marked as protected. Blocked.",
    "PROTECTED_TABLE": "The agent asked for rows from the {target} table, which holds private data. It was paused for your approval.",
    "SECRET_DETECTED": "A password or key was found in {target}. It was hidden before the agent saw it.",
    "PII_DETECTED": "Personal information was found in {target}. It was hidden before the agent saw it.",
    "PATH_ESCAPE": "The agent tried to reach a location outside the workspace. Blocked.",
    "POLICY_TAMPER": "The agent tried to touch the security policy. Blocked.",
    "EGRESS_DENIED": "The agent tried to send data to {host}, which is not on the approved list. Blocked.",
    "EXFIL_DETECTED": "The agent tried to send what looks like a secret or private data out of the system. Blocked.",
    "OUTBOUND_WRITE": "The agent wants to send data to {host}. It was paused for your approval.",
    "DESTRUCTIVE_COMMAND": "The agent wants to run a command that can delete or overwrite work: {target}. It was paused for your approval.",
    "DENIED_COMMAND": "The agent tried to run a command that is never allowed: {target}. Blocked.",
    "UNLISTED_COMMAND": "The agent wants to run a command that is not on the approved list: {target}. It was paused for your approval.",
    "SHELL_OPERATORS": "The agent tried to chain commands together, which can be used to bypass safety checks. Blocked.",
    "DESTRUCTIVE_SQL": "The agent wants to run a database change that can remove or rewrite many records. It was paused for your approval.",
    "UNSAFE_SQL": "The agent sent a database command that could not be verified as safe. Blocked.",
    "TASK_MISMATCH": "The request is outside what the agent was asked to do.",
    "SENSITIVE_LOCATION": "The location is marked as sensitive.",
    "CONTENT_SENSITIVE": "The content looks sensitive.",
    "BEHAVIOUR_ESCALATION": "The agent has been reading many sensitive items in a short time.",
    "KILL_SWITCH": "The agent kept trying forbidden actions, so the session was ended.",
    "SESSION_LOCKED": "The session was already ended. The request was refused.",
    "MASKED_FOR_PRIVACY": "Sensitive parts of {target} were replaced with placeholders.",
    "UNMASKABLE_FORMAT": "{target} could not be safely masked, so it was paused for your approval.",
    "MASKED_BLOCK_REMOVED": "The agent's edit removes hidden code. It was paused for your approval.",
    "STALE_MASKED_BASE": "{target} changed after the agent last read it. The edit was paused for your approval.",
    "HUMAN_APPROVED": "You approved this action.",
    "HUMAN_EDITED": "You approved this action with masking turned on.",
    "HUMAN_DENIED": "You denied this action.",
    "APPROVAL_TIMEOUT": "No one answered in time, so the action was denied.",
    "BENIGN_CONTEXT": "Looks like sample or test data, so it was allowed.",
    "UNSUPPORTED_BINARY": "Binary files are not available to the agent.",
    "TOO_LARGE": "The content is larger than the allowed limit.",
    "WATCHDOG_ALERT": "The agent's process tried to act outside the safety gateway.",
    "ROLLBACK": "You restored the state from before this action.",
    "INTERNAL_ERROR": "Something went wrong inside the safety gateway, so the action was blocked to be safe.",
    "ALLOWED": "The action is within what the agent was asked to do.",
    "ACTION_FAILED": "The action could not be completed.",
}


def explain(code: str, **fields: object) -> str:
    """Render a plain-language sentence for a reason code."""
    template = TEMPLATES.get(code, TEMPLATES["ALLOWED"])
    safe_fields: defaultdict[str, object] = defaultdict(lambda: "the target", fields)
    try:
        return template.format_map(safe_fields)
    except (ValueError, IndexError):
        return TEMPLATES["ALLOWED"]
