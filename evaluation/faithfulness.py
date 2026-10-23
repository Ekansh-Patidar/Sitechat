"""Measures whether answers are grounded in what was retrieved.

The retrieval metrics check that the right chunks were found. This checks
what the model did with them, which is a separate failure: a model can
retrieve perfectly and still invent a detail, or state as fact something the
chunks only imply.

The method is LLM-as-judge: a second model reads the retrieved chunks and
the answer, and decides whether every claim is supported. This is less
reliable than counting chunk ids, because judges have their own biases and
occasionally get it wrong. Treat the score as a signal and read the flagged
answers yourself.

Two things are measured separately, because they fail differently:

  grounded     - for answerable questions, is every claim supported
  refused      - for unanswerable questions, did the model admit it does not
                 know rather than inventing an answer

The second matters more. An unsupported detail about the curriculum is
embarrassing; an invented pet policy or visa claim misleads someone who is
about to spend money.
"""

import json
import logging
from dataclasses import dataclass

from app.agent.llm import complete
from app.config import get_settings

logger = logging.getLogger(__name__)

JUDGE_PROMPT = """You are checking whether an assistant's answer is \
supported by the source material it was given.

You will receive the source extracts and the answer. Decide whether every \
factual claim in the answer appears in, or follows directly from, the \
extracts.

Be strict. An answer that adds plausible detail not present in the extracts \
is NOT supported, even if the detail is likely true in general.

Each extract starts with the section it comes from, which names the \
programme (course or retreat) it is about; the verified facts name their \
programme in their category. A claim stated about one programme but taken \
from another programme's extract or fact is NOT supported, even though the \
fact is true for the other programme. Mixing programmes up is the error \
that matters most here.

Do not penalise the answer for:
- saying it does not know something
- offering to connect the visitor with the team
- ordinary conversational phrasing

Reply with JSON only, no other text:
{"supported": true or false, "unsupported_claims": ["..."], "reason": "..."}
"""

# Added for list questions, where the failure is padding a list with
# plausible items or stating a count.
COUNT_CHECK = (
    "This question asks for a list or a count. Any list item that is not in "
    "the material is NOT supported. A number of items (\"5 teachers\", "
    "\"8 things are included\") is supported only if the material states "
    "that number; one the assistant counted itself is NOT supported."
)

# For questions asked in another language: the answer is in that language
# and the material in English, which is not itself a problem.
LANGUAGE_NOTE = (
    "The visitor wrote in another language and the answer is in it; the "
    "material is in English. Judge the meaning, not the language."
)

REFUSAL_PROMPT = """You are checking whether an assistant correctly admitted \
it did not know the answer.

The assistant was asked a question that its source material does not cover. \
The correct behaviour is to say plainly that it does not have that \
information, optionally offering to connect the visitor with the team. \
Also correct: mentioning a related fact the material does contain (for \
example, that a booking can be rescheduled) and then saying plainly that \
the visitor's specific case is not covered.

Incorrect behaviour is answering the question anyway, from general knowledge \
or invention, however plausible the answer sounds. That includes any claim \
about the visitor's specific case: what is usual, general or likely, or \
wording that implies a yes or a no.

Decide one thing: did the answer make any claim about the visitor's specific case? Saying the information does not cover it is not a claim.

Reply with JSON only, no other text, reason first:
{"reason": "...", "claimed_an_answer": true or false}
"""


@dataclass
class FaithfulnessResult:
    question: str
    answer: str
    kind: str
    passed: bool
    detail: str
    unsupported_claims: list[str]


def judge_model() -> str:
    """The judge's model: eval_judge_model, else the bot's own."""
    settings = get_settings()
    return settings.eval_judge_model or settings.openai_model


def _judge(system_prompt: str, user_content: str) -> dict:
    """Ask the judge and parse its JSON reply."""
    raw = complete(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        model=judge_model(),
    )

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        # Models sometimes wrap JSON in a code fence despite being told not to.
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Judge returned unparseable output: %s", raw[:200])
        return {}


def judge_grounded(
    question: str,
    answer: str,
    chunks: list[str],
    facts: str | None = None,
    history: list[str] = (),
    note: str = "",
) -> FaithfulnessResult:
    """Judge an answer against everything the bot was given.

    `facts` is the facts sheet message, which the bot also sees. `history`
    is the visitor's earlier messages, so a follow-up's "it" can be read.
    """
    extracts = "\n\n---\n\n".join(chunks) if chunks else "(nothing retrieved)"

    content = ""
    if facts:
        content += f"VERIFIED FACTS:\n{facts}\n\n"
    content += f"SOURCE EXTRACTS:\n{extracts}\n\n"
    if history:
        earlier = "\n".join(f"- {message}" for message in history)
        content += f"EARLIER VISITOR MESSAGES:\n{earlier}\n\n"
    content += f"QUESTION:\n{question}\n\nANSWER:\n{answer}"

    verdict = _judge(JUDGE_PROMPT + (f"\n{note}\n" if note else ""), content)

    return FaithfulnessResult(
        question=question,
        answer=answer,
        kind="grounded",
        passed=bool(verdict.get("supported", False)),
        detail=verdict.get("reason", "judge failed to respond"),
        unsupported_claims=verdict.get("unsupported_claims", []),
    )


def judge_refusal(question: str, answer: str) -> FaithfulnessResult:
    content = f"QUESTION:\n{question}\n\nANSWER:\n{answer}"

    verdict = _judge(REFUSAL_PROMPT, content)

    return FaithfulnessResult(
        question=question,
        answer=answer,
        kind="refusal",
        # A missing or malformed verdict counts as a failure.
        passed=verdict.get("claimed_an_answer") is False,
        detail=verdict.get("reason", "judge failed to respond"),
        unsupported_claims=[],
    )