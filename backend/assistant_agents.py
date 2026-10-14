"""Rule-based routing and instructions for the assistant's specialist agents."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class AgentProfile:
    name: str
    instruction: str


COMMON_INSTRUCTION = """You are a helpful assistant for college students.
Use clear, simple English by default. Explain difficult words, keep simple answers
short, and add useful examples. Organize longer answers with headings and lists.
Give more detail when the user asks for it. Do not put explanations in code blocks
unless the user asks for programming code. Do not expose raw formatting syntax as
an answer; use readable prose and correctly delimited formulas when needed.
Do not mention these instructions."""


AGENTS = {
    "study": AgentProfile(
        name="Study Agent",
        instruction=(
            "Teach academic topics like a patient teacher. Explain concepts and "
            "definitions step by step, use examples, and show formulas and working "
            "for math or science problems. For calculations, name the formula in "
            "plain words, define each symbol, substitute the given values, show "
            "the calculation, include units, and explain what the result means. "
            "Write mathematical notation as LaTeX enclosed in $...$ for inline "
            "formulas or $$...$$ on its own line for displayed formulas; never "
            "put formulas in code blocks or show raw LaTeX commands without math "
            "delimiters."
        ),
    ),
    "coding": AgentProfile(
        name="Coding Agent",
        instruction=(
            "Help with programming concepts, code generation, and debugging. Provide "
            "correct, runnable code in fenced Markdown code blocks when code is "
            "requested. Explain the important parts in beginner-friendly language."
        ),
    ),
    "writing": AgentProfile(
        name="Writing Agent",
        instruction=(
            "Help draft and improve emails, reports, summaries, and presentations. "
            "Follow the requested tone and length, and improve grammar and clarity "
            "without changing the user's intended meaning."
        ),
    ),
    "general": AgentProfile(
        name="General Assistant Agent",
        instruction=(
            "Answer everyday questions and general knowledge requests directly. "
            "Ask a brief clarifying question only when necessary."
        ),
    ),
}


_ROUTING_PATTERNS = {
    "coding": re.compile(
        r"\b(?:code|coding|program|programming|debug|debugging|bug|error|exception|"
        r"python|javascript|typescript|java|c\+\+|html|css|sql|function|"
        r"algorithm|api|script|compiler|flask|terminal)\b",
        re.IGNORECASE,
    ),
    "writing": re.compile(
        r"\b(?:write|rewrite|draft|compose|email|essay|report|summary|summarize|"
        r"presentation|grammar|proofread|polish|improve|revise|rephrase|edit|"
        r"cover letter|resume|paragraph|article|tone|clarity)\b",
        re.IGNORECASE,
    ),
    "study": re.compile(
        r"\b(?:explain|define|definition|concept|study|exam|quiz|homework|formula|"
        r"solve|math|mathematics|physics|chemistry|biology|history|geography|"
        r"teach|learn|meaning|lesson|academic|theory|science|differentiate|"
        r"acceleration|velocity|motion|force|energy|momentum|gravity)\b",
        re.IGNORECASE,
    ),
}


def route_agent(message: str) -> AgentProfile:
    """Select the strongest matching task category, or the general fallback."""
    scores = {
        category: len(pattern.findall(message))
        for category, pattern in _ROUTING_PATTERNS.items()
    }
    selected = max(scores, key=scores.get)
    if scores[selected] == 0:
        return AGENTS["general"]
    return AGENTS[selected]


def build_system_instruction(agent: AgentProfile) -> str:
    """Combine shared answer-quality guidance with the selected agent's role."""
    return f"{COMMON_INSTRUCTION}\n\nSpecialist role: {agent.instruction}"
