"""
pdf-chat-lite - chat with any PDF from your terminal, powered by Claude.

Answers come only from the document and cite page numbers. The PDF is cached
after the first question, so follow-up questions are fast and cheap.

Usage:
    python pdf_chat.py document.pdf                         # interactive chat
    python pdf_chat.py document.pdf --summary               # summary only
    python pdf_chat.py document.pdf --summary --lang German # summary in another language

Want a web interface with drag-and-drop upload, one-click summaries in 9 languages
and a step-by-step setup guide? See the full PDF AI Assistant Kit:
https://borisov1.gumroad.com/l/pfjmyv
"""

import argparse
import base64
import os
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")  # also: claude-sonnet-5-5
MAX_PDF_BYTES = 32 * 1024 * 1024  # Claude API request limit

SYSTEM_PROMPT = (
    "You are a precise document assistant. Answer only from the attached PDF. "
    "When you use information from the document, mention the page number, like (p. 3). "
    "If the answer is not in the document, say so clearly instead of guessing. "
    "Answer in the same language as the user's question."
)


def summary_prompt(language: str) -> str:
    return (
        f"Write a summary of this document in {language}: a one-sentence overview, "
        "5-8 key points with page numbers, and the most important numbers, dates and names."
    )


def load_pdf(path: Path) -> dict:
    """Read a PDF and return it as a cached document content block."""
    if not path.exists():
        sys.exit(f"File not found: {path}")
    data = path.read_bytes()
    if not data.startswith(b"%PDF"):
        sys.exit(f"Not a PDF file: {path}")
    if len(data) > MAX_PDF_BYTES:
        sys.exit(f"The PDF is too large ({len(data) / 1_000_000:.1f} MB). The limit is 32 MB.")
    return {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.standard_b64encode(data).decode("ascii"),
        },
        "title": path.name,
        "cache_control": {"type": "ephemeral"},  # reuse the PDF for follow-up questions
    }


def ask(client: anthropic.Anthropic, messages: list, document: dict, question: str) -> None:
    """Send a question, stream the answer to the terminal and update the history."""
    # The PDF goes into the first user message only; later turns reuse it from history.
    content = [document, {"type": "text", "text": question}] if not messages else question
    messages.append({"role": "user", "content": content})

    print("\nAI: ", end="", flush=True)
    try:
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=messages,
            # If the model declines, retry on a fallback model in the same request.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            for text in stream.text_stream:
                print(text, end="", flush=True)
            response = stream.get_final_message()
    except Exception:
        messages.pop()  # keep the history valid so the user can retry
        raise

    if response.stop_reason == "refusal":
        messages.pop()
        print("[The model declined to answer this question.]")
        return

    # Keep the full content (including thinking blocks) in the history.
    messages.append({"role": "assistant", "content": response.content})
    u = response.usage
    cached = u.cache_read_input_tokens or 0
    total_in = u.input_tokens + cached + (u.cache_creation_input_tokens or 0)
    print(f"\n\n[tokens - input: {total_in} ({cached} from cache), output: {u.output_tokens}]")


def main():
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to a legacy code page
    parser = argparse.ArgumentParser(description="Chat with a PDF using Claude.")
    parser.add_argument("pdf", type=Path, help="path to the PDF file")
    parser.add_argument("--summary", action="store_true", help="print a summary and exit")
    parser.add_argument("--lang", default="English", help="summary language (default: English)")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Missing API key. Copy .env.example to .env and add your key from console.anthropic.com.")

    client = anthropic.Anthropic()
    document = load_pdf(args.pdf)
    messages: list = []

    try:
        if args.summary:
            ask(client, messages, document, summary_prompt(args.lang))
            return

        print(f"Loaded '{args.pdf.name}'. Ask a question (type 'summary' or 'exit').")
        while True:
            try:
                question = input("\nYou: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not question:
                continue
            if question.lower() in {"exit", "quit"}:
                break
            if question.lower() == "summary":
                question = summary_prompt(args.lang)
            ask(client, messages, document, question)
    except anthropic.AuthenticationError:
        sys.exit("\nInvalid API key. Check ANTHROPIC_API_KEY in your .env file.")
    except anthropic.RateLimitError:
        sys.exit("\nRate limit reached. Wait a minute and try again.")
    except anthropic.APIStatusError as e:
        sys.exit(f"\nAPI error ({e.status_code}): {e.message}")
    except anthropic.APIConnectionError:
        sys.exit("\nNetwork error. Check your internet connection.")


if __name__ == "__main__":
    main()
